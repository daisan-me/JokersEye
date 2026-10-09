import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import Mock
from contextlib import closing

spec=importlib.util.spec_from_file_location('server',Path(__file__).resolve().parents[1]/'app/server.py')
server=importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
ROW={'date':'2024-03-01','seat':'1','model':'マイジャグラーV','games':7000,'bb':30,'rb':25,'combined':'1/127','net':1500,'payout_percent':108.2,'source_url':'https://min-repo.com/1/','published_at':'2024-03-02','fetched_at':'2026-10-03T00:00:00Z'}

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=server.Store(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def test_empty_and_persistence(self):
        self.assertEqual(self.store.state()['summary']['records'],0)
        self.store.collector.save_rows([ROW])
        self.store.settings({'notes':'検証メモ','theme':'light'})
        reopened=server.Store(self.tmp.name)
        self.assertEqual(reopened.state()['summary']['records'],1)
        self.assertEqual(reopened.state()['settings']['notes'],'検証メモ')
        self.assertEqual(reopened.state()['databaseFile'],'GothamDataBase.sqlite')
        self.assertEqual(reopened.observations('')['rows'][0]['台番号'],'1')
    def test_database_is_one_gotham_database_file(self):
        self.assertEqual(self.store.path,Path(self.tmp.name)/'GothamDataBase.sqlite')
        self.assertTrue(self.store.path.exists())
        self.assertFalse((Path(self.tmp.name)/'jokers-eye.sqlite3').exists())
    def test_legacy_database_is_renamed_with_its_data(self):
        with tempfile.TemporaryDirectory() as folder:
            legacy=Path(folder)/'jokers-eye.sqlite3'
            with closing(sqlite3.connect(legacy)) as db:
                db.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
                db.execute("INSERT INTO settings VALUES('notes','旧ファイルのメモ')");db.commit()
            (Path(folder)/'jokers-eye.sqlite3-wal').write_bytes(b'')
            store=server.Store(folder)
            self.assertFalse(legacy.exists())
            self.assertFalse((Path(folder)/'jokers-eye.sqlite3-wal').exists())
            self.assertEqual(store.state()['settings']['notes'],'旧ファイルのメモ')
    def test_backup_is_readable(self):
        self.store.collector.save_rows([ROW])
        path=Path(self.store.backup())
        self.assertTrue(path.name.startswith('GothamDataBase-'))
        self.assertEqual(path.suffix,'.sqlite')
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM scraped_observations').fetchone()[0],1)
    def test_gdb_columns_filters_and_plans_come_from_the_database(self):
        self.store.collector.save_rows([ROW,dict(ROW,date='2023-05-01',seat='9'),
            dict(ROW,date='2024-03-02',seat='2',model='SLOT TEST',combined=None,bb=None,rb=None,net=None,payout_percent=None)])
        status=self.store.gdb_status()
        self.assertEqual(status['columns'],server.GDB_COLUMNS)
        self.assertEqual((status['rowCount'],status['firstDate'],status['lastDate']),(2,'2024-03-01','2024-03-02'))  # before 2024-03-01 is not GDB
        self.assertEqual((status['missingBonusRows'],status['missingJugglerBonusRows']),(1,0))
        filtered=self.store.gdb_rows({'juggler':'juggler','games_min':'6000','combined_n_min':'120','payout_min':'108','offset':'0','limit':'100'})
        self.assertEqual(filtered['total'],1)
        self.assertEqual((filtered['rows'][0]['曜日'],filtered['rows'][0]['出率']),('金曜日','108.2%'))
        self.assertEqual([row['日付'] for row in self.store.gdb_rows({})['rows']],['2024-03-01','2024-03-02'])
        plan=self.store.gdb_today_plan()  # from the last recorded date: 03-02 still lacks BB/RB, then 03-03 onward
        self.assertEqual((plan['lastDate'],plan['start']),('2024-03-02','2024-03-02'))
        self.assertEqual(plan['missingDates'][:2],['2024-03-02','2024-03-03'])
        self.assertNotIn('2024-03-01',plan['missingDates'])
        self.assertEqual(self.store.gdb_bonus_plan(),{'start':'2024-03-01','targetEnd':plan['targetEnd'],'missingDates':['2024-03-02'],'missingDateCount':1,'missingRows':1})
        with self.store.connect() as db:  # a filled BB/RB changes fetched_at and refreshes the cached view
            db.execute("UPDATE scraped_observations SET bb=3,rb=4,fetched_at='2026-10-07T00:00:00Z' WHERE day='2024-03-02'")
        self.assertEqual(self.store.gdb_status()['missingBonusRows'],0)
        self.assertEqual(self.store.gdb_bonus_plan()['missingDates'],[])
    def test_range_plan_takes_new_and_bonus_missing_dates_inside_the_range_only(self):
        self.store.collector.save_rows([ROW,dict(ROW,date='2024-03-02',seat='2',bb=None,rb=None),dict(ROW,date='2024-03-09',seat='3',bb=None)])
        self.store.collector.day_status('2024-03-04','','not-published',0,'TEST ONLY')
        plan=self.store.gdb_range_plan('2024-03-01','2024-03-05')
        # 03-01 is complete, 03-02 lacks BB/RB, 03-03 and 03-05 have no record, 03-04 is known unpublished
        self.assertEqual(plan['missingDates'],['2024-03-02','2024-03-03','2024-03-05'])
        self.assertEqual((plan['newDateCount'],plan['bonusDateCount'],plan['unpublishedDates']),(2,1,['2024-03-04']))
        self.assertEqual(self.store.gdb_range_plan('2024-03-01','2024-03-01')['missingDates'],[])
        for start,end in ((None,'2024-03-05'),('2024-03-01',''),('2024-02-29','2024-03-05'),('2024-03-05','2024-03-01'),('2024-03-01','2099-01-01'),('2024-3-1','2024-03-05')):
            with self.assertRaises(ValueError):
                self.store.gdb_range_plan(start,end)
    def test_registered_observations_use_the_gdb_columns_without_lending_rate(self):
        self.store.collector.save_rows([dict(ROW,date='2026-03-13',seat='114',model='スマスロ北斗の拳',games=3000,bb=12,rb=13,combined='1/120',net=970,payout_percent=107.3),
                                        dict(ROW,date='2026-03-13',seat='2',bb=None,rb=None,combined=None)])
        result=self.store.observations('2026-03-13')
        self.assertEqual(result['columns'],server.GDB_COLUMNS)
        self.assertEqual([row['台番号'] for row in result['rows']],['2','114'])
        self.assertEqual(list(result['rows'][1].values()),['2026-03-13','金曜日','114','スマスロ北斗の拳','ジャグラーではない','3000','12','13','1/120','970','107.3%'])
        self.assertEqual((result['rows'][0]['BB数'],result['rows'][0]['ジャグラーかジャグラーじゃないか']),('','ジャグラー'))
        summary=self.store.state()['summary']
        self.assertEqual((summary['records'],summary['withBonus']),(2,1))
        self.assertEqual((summary['complete'],summary['withDash'],summary['notYetRead']),(1,0,1))  # 11列そろい / 「-」あり / 未取得あり
        self.assertNotIn('main',summary)
    def test_lending_rate_is_removed_from_an_old_database(self):
        with tempfile.TemporaryDirectory() as folder:
            with closing(sqlite3.connect(Path(folder)/'GothamDataBase.sqlite')) as db:
                db.executescript("""
                    CREATE TABLE imports(id INTEGER PRIMARY KEY,created_at TEXT NOT NULL,filename TEXT NOT NULL,rows INTEGER NOT NULL,source TEXT NOT NULL);
                    CREATE TABLE observations(day TEXT NOT NULL,seat TEXT NOT NULL,model TEXT NOT NULL,games INTEGER NOT NULL,bb INTEGER NOT NULL,rb INTEGER NOT NULL,net INTEGER,rate TEXT NOT NULL,import_id INTEGER NOT NULL REFERENCES imports(id),PRIMARY KEY(day,seat));
                    CREATE TABLE installations(seat TEXT,model TEXT,rate TEXT,valid_from TEXT,valid_to TEXT,source TEXT,PRIMARY KEY(seat,valid_from));
                    CREATE TABLE scraped_observations(day TEXT,seat TEXT,model TEXT NOT NULL,games INTEGER,bb INTEGER,rb INTEGER,combined TEXT,net INTEGER,rate TEXT NOT NULL,payout_percent REAL,source_url TEXT NOT NULL,published_at TEXT,fetched_at TEXT NOT NULL,bonus_source_url TEXT,PRIMARY KEY(day,seat));
                    INSERT INTO scraped_observations VALUES('2024-03-01','1','旧機種',100,1,2,'1/33',50,'unknown',101.0,'https://min-repo.com/1/',NULL,'2026-10-01',NULL);""")
            store=server.Store(folder)
            with store.connect() as db:
                tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in ('scraped_observations','installations'):
                    self.assertNotIn('rate',{row[1] for row in db.execute(f'PRAGMA table_info({table})')},table)
            self.assertFalse({'observations','imports'}&tables)
            self.assertEqual(store.gdb_rows_all()[0]['機種'],'旧機種')  # the seat records are kept
            store.collector.save_rows([dict(ROW,date='2024-03-02')])  # and new ones are saved without it
            self.assertEqual(store.state()['summary']['records'],2)
    def test_today_plan_starts_at_the_last_recorded_date_and_retries_recent_unpublished_days(self):
        empty=self.store.gdb_today_plan()
        self.assertEqual((empty['lastDate'],empty['start'],empty['missingDates'][0]),(None,'2024-03-01','2024-03-01'))
        self.store.collector.save_rows([dict(ROW,date='2024-03-05')])
        self.store.collector.day_status('2024-03-02','','not-published',0,'TEST ONLY')  # before the last date: skipped
        self.store.collector.day_status('2024-03-06','','not-published',0,'TEST ONLY')  # after it: tried again
        plan=self.store.gdb_today_plan()
        self.assertEqual((plan['lastDate'],plan['start']),('2024-03-05','2024-03-05'))
        self.assertEqual(plan['missingDates'][0],'2024-03-06')  # 03-05 is complete and not requested again
        self.assertNotIn('2024-03-02',plan['missingDates'])
        self.assertEqual(plan['missingDates'][-1],plan['targetEnd'])
    def test_untrusted_fields_remain_text(self):
        self.store.collector.save_rows([dict(ROW,model='<script>alert(1)</script>')])
        self.assertEqual(self.store.observations('')['rows'][0]['機種'],'<script>alert(1)</script>')

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.host=server.Host(('127.0.0.1',0),server.Store(self.tmp.name))
        self.thread=threading.Thread(target=self.host.serve_forever)
        self.thread.start()
        self.base='http://127.0.0.1:%s'%self.host.server_port
    def tearDown(self):
        self.host.shutdown();self.host.server_close();self.thread.join();self.tmp.cleanup()
    def get(self, path):
        req=urllib.request.Request(self.base+'/api/'+path,headers={'X-Joker-Token':self.host.token})
        with urllib.request.urlopen(req) as response:return json.load(response)
    def test_api_auth_and_roundtrip(self):
        with self.assertRaises(urllib.error.HTTPError) as ex:
            urllib.request.urlopen(self.base+'/api/state')
        self.assertEqual(ex.exception.code,403)
        self.host.store.collector.save_rows([ROW])
        self.assertEqual(self.get('state')['summary']['records'],1)
        self.assertEqual(self.get('gdb/rows')['rows'][0]['機種'],'マイジャグラーV')
        self.assertEqual(self.get('gdb/status')['rowCount'],1)
    def test_static_and_traversal(self):
        with urllib.request.urlopen(self.base+'/') as r:
            self.assertIn(b"Joker's eye",r.read())
            self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        with self.assertRaises(urllib.error.HTTPError) as ex:
            urllib.request.urlopen(self.base+'/../app/server.py')
        self.assertEqual(ex.exception.code,404)
    def test_csv_features_are_gone(self):
        with urllib.request.urlopen(self.base+'/gdb.js') as r:
            self.assertEqual(r.status,200)
        for path in ('/template.csv','/base-sheet.js'):
            with self.assertRaises(urllib.error.HTTPError) as ex:
                urllib.request.urlopen(self.base+path)
            self.assertEqual(ex.exception.code,404,path)
        for path in ('scrape/csv','base-sheet/status','base-sheet/download'):
            with self.assertRaises(urllib.error.HTTPError) as ex:
                self.get(path)
            self.assertEqual(ex.exception.code,404,path)
        for path in ('import','base-sheet/create','base-sheet/update','base-sheet/build'):
            with self.assertRaises(urllib.error.HTTPError) as ex:
                self.post(path,{})
            self.assertEqual(ex.exception.code,404,path)

    def post(self, path, payload):
        req=urllib.request.Request(self.base+'/api/'+path,data=json.dumps(payload).encode(),headers={'X-Joker-Token':self.host.token,'Content-Type':'application/json'})
        with urllib.request.urlopen(req) as response:return json.load(response)

    def test_today_update_requests_the_plan_dates_with_bonus_capture(self):
        start=Mock(return_value={'state':'running'})
        self.host.store.collector.start=start
        self.host.store.gdb_today_plan=Mock(return_value={'targetEnd':'2026-10-03','missingDates':['2026-10-01','2026-10-03']})
        self.post('gdb/update',{})
        start.assert_called_once_with('2026-10-03','2026-10-01',True,['2026-10-01','2026-10-03'])
        self.host.store.gdb_today_plan.return_value={'targetEnd':'2026-10-03','missingDates':[]}
        start.reset_mock()
        self.assertEqual(self.post('gdb/update',{})['status'],'up-to-date')
        start.assert_not_called()

    def test_scraping_starts_only_from_the_one_panel(self):
        for path in ('scrape/start','gdb/bonuses'):
            with self.assertRaises(urllib.error.HTTPError) as ex:
                self.post(path,{})
            self.assertEqual(ex.exception.code,404,path)

    def test_range_action_requests_only_plan_dates_with_bonus_capture(self):
        start=Mock(return_value={'state':'running'})
        self.host.store.collector.start=start
        self.host.store.gdb_range_plan=Mock(return_value={'targetEnd':'2024-03-07','missingDates':['2024-03-02','2024-03-05']})
        self.post('gdb/range',{'start':'2024-03-01','end':'2024-03-07'})
        self.host.store.gdb_range_plan.assert_called_once_with('2024-03-01','2024-03-07')
        start.assert_called_once_with('2024-03-07','2024-03-02',True,['2024-03-02','2024-03-05'])
        self.host.store.gdb_range_plan.return_value={'targetEnd':'2024-03-07','missingDates':[]}
        start.reset_mock()
        self.assertEqual(self.post('gdb/range',{'start':'2024-03-01','end':'2024-03-07'})['status'],'up-to-date')
        start.assert_not_called()

    def test_range_action_rejects_a_missing_date_and_a_running_collector(self):
        with self.assertRaises(urllib.error.HTTPError) as ex:
            self.post('gdb/range',{'start':'2024-03-01'})
        self.assertEqual(ex.exception.code,400)
        self.assertIn('開始日と終了日',json.load(ex.exception)['error'])
        self.host.store.collector.active=True
        try:
            with self.assertRaises(urllib.error.HTTPError) as ex:
                self.post('gdb/range',{'start':'2024-03-01','end':'2024-03-02'})
            self.assertIn('スクレイピング中',json.load(ex.exception)['error'])
        finally:
            self.host.store.collector.active=False

    def test_days_can_be_deleted_and_locked_but_not_during_a_run(self):
        self.assertEqual(self.post('gdb/lock',{'start':'2024-03-02','end':'2024-03-03'})['lockedDates'],['2024-03-02','2024-03-03'])
        result=self.post('gdb/delete',{'start':'2024-03-01','end':'2024-03-03'})
        self.assertEqual((result['deletedDays'],result['lockedKept']),(1,['2024-03-02','2024-03-03']))
        self.assertEqual(self.post('gdb/unlock',{'start':'2024-03-03','end':'2024-03-03'})['lockedDates'],['2024-03-02'])
        with self.assertRaises(urllib.error.HTTPError) as ex:
            self.post('gdb/delete',{'start':'2024-03-03','end':'2024-03-01'})
        self.assertEqual(ex.exception.code,400)
        self.host.store.collector.active=True
        try:
            for path in ('gdb/delete','gdb/lock','gdb/unlock'):
                with self.assertRaises(urllib.error.HTTPError) as ex:
                    self.post(path,{'start':'2024-03-01','end':'2024-03-01'})
                self.assertIn('取得中',json.load(ex.exception)['error'])
        finally:
            self.host.store.collector.active=False
        self.assertEqual(self.post('gdb/lock-complete-rows',{})['lockedRows'],0)
        self.assertEqual(self.post('gdb/unlock-rows',{})['lockedRows'],0)
        self.host.store.collector.active=True
        try:
            for path in ('gdb/lock-complete-rows','gdb/unlock-rows'):
                with self.assertRaises(urllib.error.HTTPError) as ex:
                    self.post(path,{})
                self.assertIn('取得中',json.load(ex.exception)['error'])
        finally:
            self.host.store.collector.active=False
        for path in ('scrape/settings',):
            with self.assertRaises(urllib.error.HTTPError) as ex:
                self.post(path,{})
            self.assertEqual(ex.exception.code,404,path)

    def test_rescrape_requests_only_the_plan_dates_in_fill_mode(self):
        start=Mock(return_value={'state':'running'})
        self.host.store.collector.start=start
        self.host.store.gdb_rescrape_plan=Mock(return_value={'targetEnd':'2024-03-07','dates':['2024-03-02','2024-03-05'],'dateCount':2,'rows':3})
        self.post('gdb/rescrape',{'start':'2024-03-01','end':'2024-03-07'})
        start.assert_called_once_with('2024-03-07','2024-03-02',True,['2024-03-02','2024-03-05'],fill_dashes=True)
        self.host.store.gdb_rescrape_plan.return_value={'targetEnd':'2024-03-07','dates':[],'dateCount':0,'rows':0}
        start.reset_mock()
        self.assertEqual(self.post('gdb/rescrape',{'start':'2024-03-01','end':'2024-03-07'})['status'],'up-to-date')
        start.assert_not_called()

    def test_fixed_floor_assets_and_registration(self):
        with urllib.request.urlopen(self.base+'/fixed-floor.json') as response:
            floor = json.load(response)
        self.assertEqual(floor['name'], 'Numbered map 1.4 ex.Is-ID')
        self.assertEqual(len(floor['faces']), 32)
        self.assertEqual(sorted(tile['seatNumber'] for face in floor['faces'] for tile in face['tiles']), list(range(1, 311)))
        self.assertFalse(floor['islandLabelsDisplayed'])
        self.assertTrue(self.host.store.state()['mapRegistered'])
        with urllib.request.urlopen(self.base+'/fixed-floor.js') as response:
            self.assertIn(b'const FixedFloor', response.read())
        with urllib.request.urlopen(self.base+'/fixed-floor-display.json') as response:
            display = json.load(response)
        self.assertEqual(display['source'], floor['name'])
        self.assertEqual(display['baseTileSize'] * display['tileScale'], 10.75)
        self.assertEqual(len(display['radialOutward']), 5)

if __name__=='__main__':
    unittest.main()
