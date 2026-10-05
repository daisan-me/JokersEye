import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from contextlib import closing

spec=importlib.util.spec_from_file_location('server',Path(__file__).resolve().parents[1]/'app/server.py')
server=importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
HEADER='date,seat,model,games,bb,rb,net,rate\n'
GOOD=HEADER+'2023-04-27,001,テスト機種,7000,25,20,1200,main\n'

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=server.Store(self.tmp.name)
    def tearDown(self):
        self.tmp.cleanup()
    def test_empty_and_persistence(self):
        self.assertEqual(self.store.state()['summary']['records'],0)
        self.store.import_csv(GOOD,'test.csv','手動検証')
        self.store.settings({'notes':'検証メモ','theme':'light'})
        reopened=server.Store(self.tmp.name)
        self.assertEqual(reopened.state()['summary']['main'],1)
        self.assertEqual(reopened.state()['settings']['notes'],'検証メモ')
        self.assertEqual(reopened.observations('')['rows'][0]['seat'],'001')
    def test_invalid_batch_atomic(self):
        with self.assertRaises(ValueError):
            self.store.import_csv(GOOD+'2023-04-28,002,機種,-1,0,0,,main\n','bad.csv','test')
        self.assertEqual(self.store.state()['summary']['records'],0)
        self.assertEqual(len(self.store.state()['imports']),0)
    def test_duplicate_does_not_overwrite_or_partially_import(self):
        self.store.import_csv(GOOD,'one.csv','test')
        with self.assertRaises(ValueError):
            self.store.import_csv(HEADER+'2023-04-28,003,機種,100,0,0,,main\n'+GOOD.split('\n')[1]+'\n','two.csv','test')
        self.assertEqual(self.store.state()['summary']['records'],1)
    def test_missing_dates_not_filled_and_legacy_separate(self):
        text=HEADER+'2023-04-26,1,A,100,0,0,,main\n2023-04-29,1,A,100,0,0,,low\n2023-05-01,1,A,100,0,0,,unknown\n'
        self.store.import_csv(text,'mixed.csv','test')
        s=self.store.state()['summary']
        self.assertEqual((s['days'],s['legacy'],s['main']),(3,1,0))
        self.assertEqual(self.store.observations('2023-04-27')['rows'],[])
        self.assertIsNone(self.store.observations('2023-04-29')['rows'][0]['net'])
    def test_backup_is_readable(self):
        self.store.import_csv(GOOD,'test.csv','test')
        path=self.store.backup()
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM observations').fetchone()[0],1)
    def test_base_sheet_columns_filters_and_delta_update(self):
        row = {'date':'2024-03-01','seat':'1','model':'マイジャグラーV','games':7000,'bb':30,'rb':25,'combined':'1/127','net':1500,'rate':'unknown','payout_percent':108.2,'source_url':'https://min-repo.com/1/','published_at':'2024-03-02','fetched_at':'2026-10-03T00:00:00Z'}
        self.store.collector.save_rows([row])
        created = self.store.build_base_sheet('create')
        self.assertEqual(created['status'],'created')
        self.assertEqual(created['sheet']['columns'],server.BASE_SHEET_COLUMNS)
        filtered = self.store.base_sheet_rows({'juggler':'juggler','games_min':'6000','combined_n_min':'120','payout_min':'108','offset':'0','limit':'100'})
        self.assertEqual(filtered['total'],1)
        self.assertEqual(filtered['rows'][0]['曜日'],'金曜日')
        self.store.collector.save_rows([dict(row,date='2024-03-02',seat='2',model='SLOT TEST',combined=None,bb=None,rb=None,net=None,payout_percent=None)])
        updated = self.store.build_base_sheet('update')
        self.assertEqual(updated['addedRows'],1)
        self.assertEqual(self.store.base_sheet_status()['rowCount'],2)
        self.assertEqual(self.store.base_sheet_status()['firstDate'],'2024-03-01')
        self.assertEqual([row['日付'] for row in self.store._read_base_sheet()], ['2024-03-01', '2024-03-02'])
    def test_untrusted_fields_remain_text(self):
        text=GOOD.replace('テスト機種','<script>alert(1)</script>')
        self.store.import_csv(text,'a.csv','test')
        self.assertEqual(self.store.observations('')['rows'][0]['model'],'<script>alert(1)</script>')
    def test_invalid_values(self):
        for text in [GOOD.replace(',main',',20'),GOOD.replace('2023-04-27','2023-02-30'),GOOD.replace('7000','1'),HEADER,GOOD+GOOD.split('\n')[1]+'\n']:
            with self.assertRaises(ValueError):
                self.store.import_csv(text,'bad.csv','test')
        with self.assertRaises(ValueError):
            self.store.import_csv(GOOD,'no-source.csv','')

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.host=server.Host(('127.0.0.1',0),server.Store(self.tmp.name))
        self.thread=threading.Thread(target=self.host.serve_forever)
        self.thread.start()
        self.base='http://127.0.0.1:%s'%self.host.server_port
    def tearDown(self):
        self.host.shutdown();self.host.server_close();self.thread.join();self.tmp.cleanup()
    def test_api_auth_and_roundtrip(self):
        with self.assertRaises(urllib.error.HTTPError) as ex:
            urllib.request.urlopen(self.base+'/api/state')
        self.assertEqual(ex.exception.code,403)
        payload=json.dumps({'text':GOOD,'filename':'test.csv','source':'API test'}).encode()
        req=urllib.request.Request(self.base+'/api/import',data=payload,headers={'X-Joker-Token':self.host.token,'Content-Type':'application/json'})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(json.load(r)['rows'],1)
        req=urllib.request.Request(self.base+'/api/state',headers={'X-Joker-Token':self.host.token})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(json.load(r)['summary']['records'],1)
    def test_static_and_traversal(self):
        with urllib.request.urlopen(self.base+'/') as r:
            self.assertIn(b"Joker's eye",r.read())
            self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        with self.assertRaises(urllib.error.HTTPError) as ex:
            urllib.request.urlopen(self.base+'/../app/server.py')
        self.assertEqual(ex.exception.code,404)

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
