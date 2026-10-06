"""Public-table bonus enrichment and missing-only CSV regression tests."""
import csv
import importlib.util
from html import escape
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import urllib.request

spec = importlib.util.spec_from_file_location('server', Path(__file__).resolve().parents[1] / 'app/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
from scraper import parse_report, parse_bonuses, bonus_targets

FIXTURE = Path(__file__).parent / 'fixtures' / 'min-repo-bonuses.json'
DAY = '2026-10-03'
REPORT = 'https://min-repo.com/3389999/'


def html(page):
    anchors = lambda links: ''.join('<a href="' + escape(link['url'], quote=True) + '">' + escape(link['text']) + '</a>' for link in links)
    return '<h1>' + escape(page['heading']) + '</h1><time datetime="' + page['published'] + '"></time>' + anchors(page.get('links', [])) + ''.join(
        '<table>' + ''.join('<tr>' + ''.join('<td>' + escape(cell) + '</td>' for cell in row['cells']) + '</tr>' + anchors(row.get('links', [])) for row in table) + '</table>' for table in page['tables'])


class BonusCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = json.loads(FIXTURE.read_text(encoding='utf-8'))
        cls.sources = {page['url']: html(page) for page in cls.pages}
        cls.rows = parse_report(cls.sources[REPORT + '?kishu=all'], DAY, REPORT + '?kishu=all')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = server.Store(self.tmp.name)
        self.collector = self.store.collector
        self.collector.save_rows(self.rows)
        self.collector.day_status(DAY, REPORT, 'complete', 310, '')
        self.calls = []
        def fetch(url, **kwargs):
            self.calls.append(url)
            if url not in self.sources:raise RuntimeError('TEST ONLY: unavailable captured model page')
            return self.sources[url]
        self.collector.fetch = fetch

    def tearDown(self):
        self.tmp.cleanup()

    def run_repair(self):
        self.collector.progress = {'id':'test-repair','state':'running','completed':0,'added':0}
        self.collector.run(DAY, DAY, True, [DAY], True)

    def test_all_310_actual_counts_and_102_jugglers(self):
        self.collector.collect_bonuses(self.sources[REPORT+'?kishu=all'], DAY, REPORT, self.rows)
        with self.store.connect() as db:
            saved = [dict(row) for row in db.execute('SELECT * FROM scraped_observations WHERE day=? ORDER BY CAST(seat AS INTEGER)', (DAY,))]
        self.assertEqual(len(saved),310)
        self.assertTrue(all(row['bb'] is not None and row['rb'] is not None for row in saved))
        self.assertEqual(sum(self.store._is_juggler(row['model']) for row in saved),102)
        self.assertEqual(sum(row['combined'] is None for row in saved),4)
        self.assertEqual(sum(row['payout_percent'] is None for row in saved),5)
        self.assertEqual((saved[282]['games'],saved[282]['bb'],saved[282]['rb'],saved[282]['net'],saved[282]['payout_percent']),(7188,40,18,3070,114.2))
        for original, enriched in zip(self.rows,saved):
            self.assertEqual((original['seat'],original['model'],original['games'],original['net'],original['payout_percent']), (enriched['seat'],enriched['model'],enriched['games'],enriched['net'],enriched['payout_percent']))
        self.assertIn(REPORT+'?num=259',self.calls)
        self.assertIn(REPORT+'?num=264',self.calls)
        count=len(self.calls)
        self.run_repair()
        self.assertEqual(len(self.calls),count)
        self.assertEqual(self.collector.progress['total'],0)

    def test_resume_skips_completed_models_and_stale_marker(self):
        self.collector.cache_bonus_targets(self.sources[REPORT+'?kishu=all'],DAY,REPORT,self.rows)
        complete = [dict(row,bb=0,rb=0) for row in self.rows if row['seat'] not in ('283','284','285')]
        self.collector.save_rows(complete)
        self.collector.bonus_status(DAY,'complete',310,'STALE')
        self.run_repair()
        self.assertEqual(self.collector.progress['added'],0)
        self.assertEqual(self.collector.progress['completed'],1)
        self.assertEqual(self.collector.status()['summary']['missingBonuses'],0)
        # The day's report once (ordinary entry point), then only the one unfinished model.
        self.assertEqual(len(self.calls),2)
        self.assertEqual(self.calls[0],REPORT)
        self.assertIn('kishu=',self.calls[1])
        self.assertNotIn(REPORT+'?kishu=all',self.calls)

    def test_model_failure_falls_back_to_actual_singleton_link(self):
        row=next(row for row in self.rows if row['seat']=='259')
        self.collector.cache_bonus_targets(self.sources[REPORT+'?kishu=all'],DAY,REPORT,[row])
        with self.store.connect() as db:
            db.execute('DELETE FROM scraped_observations WHERE seat!=?',('259',))
        original=self.collector.fetch
        def fetch(url, **kwargs):
            if 'kishu=' in url:raise ValueError('TEST ONLY: incomplete model table')
            return original(url,**kwargs)
        self.collector.fetch=fetch
        self.collector.collect_bonuses(None,DAY,REPORT,[row])
        self.assertEqual(self.collector.status()['summary']['missingBonuses'],0)
        self.assertIn(REPORT+'?num=259',self.calls)

    def test_restriction_stops_without_fallback(self):
        self.collector.cache_bonus_targets(self.sources[REPORT+'?kishu=all'],DAY,REPORT,self.rows)
        self.collector.fetch=Mock(side_effect=RuntimeError('公開サイトが取得を制限しました（HTTP 403）。'))
        self.run_repair()
        self.assertEqual(self.collector.progress['state'],'failed')
        self.assertEqual(self.collector.fetch.call_count,1)
        self.assertEqual(self.collector.status()['summary']['missingBonuses'],310)

    def test_conflicting_saved_count_is_not_mixed(self):
        row=self.rows[0]
        self.collector.save_rows([dict(row,bb=999)])
        with self.assertRaises(ValueError):self.collector.save_bonus_values(DAY,row,(1,2,'1/100'),REPORT)
        with self.store.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT bb,rb FROM scraped_observations WHERE seat=?',(row['seat'],)).fetchone()),(999,None))

    def test_csv_missing_only_sync_order_and_next_dates(self):
        day='2026-09-30'
        initial=[dict(row,date=day) for row in self.rows[:2]]
        self.collector.save_rows(initial)
        self.store.build_base_sheet('create')
        before=self.store._read_base_sheet()
        self.collector.save_bonus_values(day,initial[0],(0,0,None),REPORT)
        self.collector.save_bonus_values(day,initial[1],(10,5,'1/100'),REPORT)
        update=self.store.build_base_sheet('bonuses')
        self.assertEqual(update['updatedRows'],2)
        self.assertEqual(update['addedRows'],0)
        after=self.store._read_base_sheet()
        self.assertEqual(after[0]['BB数'],'0')
        for a,b in zip(before,after):
            self.assertEqual({k:v for k,v in a.items() if k not in ('BB数','RB数','合成')},{k:v for k,v in b.items() if k not in ('BB数','RB数','合成')})
        self.assertEqual(self.store.build_base_sheet('bonuses')['updatedRows'],0)
        # New dates are added locally; existing dates are enriched, not duplicated.
        self.store.build_base_sheet('update')
        rows=self.store._read_base_sheet()
        self.assertEqual(len(rows),312)
        self.assertEqual([(r['日付'],int(r['台番号'])) for r in rows],sorted((r['日付'],int(r['台番号'])) for r in rows))
        with self.store.base_sheet_path().open(encoding='utf-8-sig',newline='') as file:
            self.assertEqual(csv.DictReader(file).fieldnames,server.BASE_SHEET_COLUMNS)

    def test_bonus_plan_uses_nulls_not_coverage_status(self):
        self.store.build_base_sheet('create')
        self.store.build_base_sheet('update')
        self.collector.save_rows([dict(row,bb=0,rb=0) for row in self.rows if row['seat']!='283'])
        self.collector.bonus_status(DAY,'complete',310,'STALE')
        plan=self.store.base_sheet_bonus_plan(DAY,DAY)
        self.assertEqual((plan['missingDates'],plan['missingRows']),([DAY],1))
        self.assertEqual(self.calls,[])

    def test_csv_record_identity_conflict_is_not_refetched_or_overwritten(self):
        self.store.build_base_sheet('create')
        self.store.build_base_sheet('update')
        before=self.store._read_base_sheet()
        self.collector.save_rows([dict(row,model='TEST CHANGED',games=999) for row in self.rows])
        plan=self.store.base_sheet_bonus_plan(DAY,DAY)
        self.assertEqual(plan['missingRows'],0)
        self.assertEqual(plan['unavailableRows'],310)
        self.assertEqual(before,self.store._read_base_sheet())

    def test_partial_source_preserves_missing_and_is_not_complete(self):
        row=self.rows[0]
        with self.store.connect() as db:db.execute('DELETE FROM scraped_observations WHERE seat!=?',(row['seat'],))
        self.collector.cache_bonus_targets(self.sources[REPORT+'?kishu=all'],DAY,REPORT,[row])
        self.collector.fetch=Mock(return_value='<h1>10/3(土) ゴッサムシティ</h1><time datetime="2026-10-04"></time><table><tr><td>台番</td><td>G数</td><td>BB</td><td>RB</td></tr><tr><td>1</td><td>'+str(row['games'])+'</td><td>0</td><td>-</td></tr></table>')
        self.collector.collect_bonuses(None,DAY,REPORT,[row])
        self.assertEqual(self.collector.status()['summary']['missingBonuses'],1)
        self.assertEqual(self.collector.status()['bonusFailures'][0]['status'],'partial')

    def test_empty_detail_is_deferred_without_same_url_retry_burst(self):
        url=REPORT+'?num=259'
        self.collector.page_expectations[url]={'kind':'bonus-seat','seats':['259']}
        self.collector._fetch_once=Mock(side_effect=RuntimeError('公開ページを表示できませんでした'))
        with self.assertRaises(RuntimeError):type(self.collector).fetch(self.collector,url)
        self.assertEqual(self.collector._fetch_once.call_count,1)


if __name__=='__main__':unittest.main()
