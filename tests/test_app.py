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
ROW={'date':'2024-03-01','seat':'1','model':'マイジャグラーV','games':7000,'bb':30,'rb':25,'combined':'1/127','net':1500,'rate':'unknown','payout_percent':108.2,'source_url':'https://min-repo.com/1/','published_at':'2024-03-02','fetched_at':'2026-10-03T00:00:00Z'}

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
        self.assertEqual(reopened.observations('')['rows'][0]['seat'],'1')
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
        plan=self.store.gdb_update_plan()
        self.assertNotIn('2024-03-01',plan['missingDates']);self.assertNotIn('2024-03-02',plan['missingDates'])
        self.assertEqual(plan['missingDates'][0],'2024-03-03')
        self.assertEqual(self.store.gdb_bonus_plan(),{'start':'2024-03-01','targetEnd':plan['targetEnd'],'missingDates':['2024-03-02'],'missingDateCount':1,'missingRows':1})
        with self.store.connect() as db:  # a filled BB/RB changes fetched_at and refreshes the cached view
            db.execute("UPDATE scraped_observations SET bb=3,rb=4,fetched_at='2026-10-07T00:00:00Z' WHERE day='2024-03-02'")
        self.assertEqual(self.store.gdb_status()['missingBonusRows'],0)
        self.assertEqual(self.store.gdb_bonus_plan()['missingDates'],[])
    def test_untrusted_fields_remain_text(self):
        self.store.collector.save_rows([dict(ROW,model='<script>alert(1)</script>')])
        self.assertEqual(self.store.observations('')['rows'][0]['model'],'<script>alert(1)</script>')

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

    def test_new_scraping_defaults_to_bonus_capture(self):
        start=Mock(return_value={'state':'running'})
        self.host.store.collector.start=start
        self.post('scrape/start',{'start':'2026-10-03','end':'2026-10-03'})
        start.assert_called_once_with('2026-10-03','2026-10-03',True,None,False)

    def test_gdb_update_requests_only_dates_without_records(self):
        start=Mock(return_value={'state':'running'})
        self.host.store.collector.start=start
        self.host.store.gdb_update_plan=Mock(return_value={'targetEnd':'2026-10-03','missingDates':['2026-10-01','2026-10-03']})
        self.post('gdb/update',{})
        start.assert_called_once_with('2026-10-03','2026-10-01',True,['2026-10-01','2026-10-03'])
        self.host.store.gdb_update_plan.return_value={'targetEnd':'2026-10-03','missingDates':[]}
        start.reset_mock()
        self.assertEqual(self.post('gdb/update',{})['status'],'up-to-date')
        start.assert_not_called()

    def test_bonus_action_targets_only_plan_dates(self):
        start=Mock(return_value={'state':'running'})
        self.host.store.collector.start=start
        self.host.store.gdb_bonus_plan=Mock(return_value={'targetEnd':'2026-10-03','missingDates':['2026-10-03'],'missingRows':3})
        self.post('gdb/bonuses',{'start':'2026-10-03','end':'2026-10-03'})
        start.assert_called_once_with('2026-10-03','2026-10-03',True,['2026-10-03'],True)
        self.host.store.gdb_bonus_plan.return_value={'missingDates':[],'missingRows':0}
        start.reset_mock()
        self.assertEqual(self.post('gdb/bonuses',{})['status'],'up-to-date')
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
