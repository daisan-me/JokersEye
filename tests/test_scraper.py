import importlib.util
from pathlib import Path
import tempfile
import threading
import time
import unittest

spec=importlib.util.spec_from_file_location('scraper_server',Path(__file__).resolve().parents[1]/'app/server.py')
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)
import scraper
from scraper import parse_report, number, TableParser, all_data_link, bonus_links, parse_bonuses, index_links, TAG

FIXTURE='''<h1>9/1(火) ゴッサムシティ</h1><time datetime="2026-09-02T05:00:00+09:00">2026年9月2日</time><table><tr><th>機種</th><th>台番</th><th>差枚</th><th>G数</th><th>出率</th></tr><tr><td>TEST ONLY</td><td>1</td><td>-</td><td>0</td><td>-</td></tr><tr><th>機種</th><th>台番</th><th>差枚</th><th>G数</th><th>出率</th></tr><tr><td>TEST ONLY</td><td>2</td><td>-1,234</td><td>2,345</td><td>82.1%</td></tr></table>'''

class ScraperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=server.Store(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_missing_is_not_zero(self):
        rows=parse_report(FIXTURE,'2026-09-01','https://min-repo.com/3326458/?kishu=all')
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['games'],0)
        self.assertIsNone(rows[0]['bb']);self.assertIsNone(rows[0]['rb']);self.assertIsNone(rows[0]['net'])
        self.assertEqual(rows[1]['net'],-1234);self.assertEqual(rows[1]['payout_percent'],82.1)
    def test_validate_identity_and_duplicates(self):
        for html in [FIXTURE.replace('ゴッサムシティ','別の店'),FIXTURE.replace('9/1(火)','9/2(水)'),FIXTURE.replace('<td>2</td>','<td>1</td>'),FIXTURE.replace('2026-09-02','2025-09-02')]:
            with self.assertRaises(ValueError):parse_report(html,'2026-09-01','https://min-repo.com/3326458/')
    def test_idempotent_save_and_missing_values_stay_empty(self):
        rows=parse_report(FIXTURE,'2026-09-01','https://min-repo.com/3326458/?kishu=all')
        self.store.collector.save_rows(rows);self.store.collector.save_rows(rows)
        self.assertEqual(self.store.state()['summary']['records'],2)
        self.assertEqual(len(self.store.observations('2026-09-01')['rows']),2)
        self.store.collector.day_status('2026-09-01',rows[0]['source_url'],'partial',2,'TEST ONLY')
        saved=self.store.gdb_rows_all()
        self.assertEqual((saved[0]['BB数'],saved[0]['ゲーム数']),('','0'))
        self.assertEqual(server.Store(self.tmp.name).collector.status()['summary']['records'],2)
    def fetch_in_background(self,url):
        result={}
        def run():
            try:result['html']=self.store.collector._fetch_once(url)
            except Exception as ex:result['error']=ex
        thread=threading.Thread(target=run);thread.start()
        return thread,result
    def test_browser_task_reports_whether_a_run_is_active(self):
        collector=self.store.collector
        self.assertEqual(collector.browser_task(),{'active':False})
        collector.active=True
        self.assertEqual(collector.browser_task(),{'active':True})
        collector.active=False
    def test_pages_go_to_several_windows_at_most_240_a_minute(self):
        collector=self.store.collector
        self.assertEqual(scraper.PAGES_PER_MINUTE,240)
        first,first_result=self.fetch_in_background('https://min-repo.com/1/')
        second,second_result=self.fetch_in_background('https://min-repo.com/2/')
        one=collector.browser_task(wait=1)
        started=time.monotonic()
        self.assertEqual(collector.browser_task(),{'active':False})  # the next page waits for its slot
        two=collector.browser_task(wait=1)
        self.assertGreaterEqual(time.monotonic()-started,60/240-0.05)
        self.assertEqual({one['url'],two['url']},{'https://min-repo.com/1/','https://min-repo.com/2/'})
        self.assertNotIn('done',one)
        collector.browser_result({'id':two['id'],'html':'page '+two['url']})  # answers may come back in any order
        collector.browser_result({'id':one['id'],'html':'page '+one['url']})
        first.join(5);second.join(5)
        self.assertEqual((first_result['html'],second_result['html']),('page https://min-repo.com/1/','page https://min-repo.com/2/'))
        with self.assertRaises(ValueError):collector.browser_result({'id':one['id'],'html':'late'})
    def test_a_page_no_window_answered_is_handed_out_again(self):
        collector=self.store.collector
        thread,result=self.fetch_in_background('https://min-repo.com/1/')
        first=collector.browser_task(wait=1)
        original=scraper.PAGE_LEASE
        scraper.PAGE_LEASE=0.05
        try:
            time.sleep(0.3)  # the window that took it never answers (e.g. it did not receive the reply)
            again=collector.browser_task(wait=1)
        finally:
            scraper.PAGE_LEASE=original
        self.assertEqual(again['id'],first['id'])
        collector.browser_result({'id':again['id'],'html':'second window'})
        thread.join(5)
        self.assertEqual(result['html'],'second window')
        with self.assertRaises(ValueError):collector.browser_result({'id':first['id'],'html':'late first window'})
    def test_a_restriction_in_one_window_stops_the_others(self):
        collector=self.store.collector
        thread,result=self.fetch_in_background('https://min-repo.com/1/')
        task=collector.browser_task(wait=1)
        collector.browser_result({'id':task['id'],'error':scraper.RESTRICTED+'（HTTP 429）。再試行せず停止します。'})
        thread.join(5)
        self.assertIn('HTTP 429',str(result['error']))
        with self.assertRaises(RuntimeError):collector._fetch_once('https://min-repo.com/2/')  # not even queued
        self.assertEqual(collector.browser_task(),{'active':False})
    def test_stop_releases_pages_that_are_still_waiting(self):
        collector=self.store.collector
        thread,result=self.fetch_in_background('https://min-repo.com/1/')
        for _ in range(50):
            if collector.queue:break
            time.sleep(0.01)
        collector.stop();thread.join(5)
        self.assertIn('停止',str(result['error']))
        self.assertEqual(collector.browser_task(),{'active':False})
    def test_past_future_ranges_and_blocked_html(self):
        for end in ['2020-01-01','2099-01-01','bad']:
            with self.assertRaises(ValueError):self.store.collector.start(end)
        with self.assertRaises(ValueError):parse_report('<script>browser challenge</script>','2026-09-01','https://min-repo.com/3326458/')

    def test_default_update_does_not_retry_older_failures(self):
        collector=self.store.collector
        collector.save_rows(parse_report(FIXTURE,'2026-09-01','https://min-repo.com/3326458/'))
        collector.day_status('2026-09-01','https://min-repo.com/3326458/','complete',2,'TEST ONLY')
        collector.day_status('2023-04-27','https://min-repo.com/810716/','failed',0,'TEST ONLY')
        collector.seed=[{'day':'2023-04-27','url':'https://min-repo.com/810716/'}]
        calls=[]
        def fetch(url):
            calls.append(url)
            if '/tag/' in url:return '<h1>ゴッサムシティ</h1><table></table>'
            raise RuntimeError('TEST ONLY: unreadable')
        collector.fetch=fetch
        collector.progress={'id':'test-delta','state':'running','added':0,'completed':0,'total':0}
        collector.run('2026-09-02',None)
        self.assertEqual(collector.progress['total'],1)
        self.assertEqual(collector.progress['completed'],1)
        self.assertEqual(collector.progress['state'],'complete')
        self.assertNotIn('https://min-repo.com/810716/',calls)
        self.assertEqual(collector.status()['summary']['records'],2)
        self.assertEqual(list(Path(self.tmp.name).rglob('*.csv')),[])  # a run keeps everything in the database

    def test_explicit_range_does_not_retry_outside_it(self):
        collector=self.store.collector
        collector.day_status('2023-04-27','https://min-repo.com/810716/','failed',0,'TEST ONLY')
        collector.seed=[]
        calls=[]
        def fetch(url):
            calls.append(url)
            return '<h1>ゴッサムシティ</h1><table></table>'
        collector.fetch=fetch
        collector.progress={'id':'test-range','state':'running','added':0,'completed':0,'total':0}
        collector.run('2026-09-01','2026-09-01')
        self.assertEqual(collector.progress['total'],1)
        self.assertEqual(len(calls),1)
        self.assertEqual(collector.progress['state'],'complete')

    def test_follow_report_link_and_reject_other_origin(self):
        html=FIXTURE+'<a href="?kishu=all">全台データ一覧</a>'
        self.assertEqual(all_data_link(html,'2026-09-01','https://min-repo.com/3326458/'),'https://min-repo.com/3326458/?kishu=all')
        for bad in [html.replace('?kishu=all','https://example.com/3326458/?kishu=all'),html.replace('9/1(火)','9/2(水)')]:
            with self.assertRaises(ValueError):all_data_link(bad,'2026-09-01','https://min-repo.com/3326458/')

    def test_bonus_table_identity_and_missing_values(self):
        rows=parse_report(FIXTURE,'2026-09-01','https://min-repo.com/3326458/?kishu=all')
        detail='<h1>9/1(火) ゴッサムシティ</h1><time datetime="2026-09-02">9/2</time><table><tr><th>台番</th><th>G数</th><th>BB</th><th>RB</th></tr><tr><td>1</td><td>0</td><td>0</td><td>0</td></tr><tr><td>2</td><td>2,345</td><td>-</td><td>5</td></tr></table>'
        values=parse_bonuses(detail,'2026-09-01','TEST ONLY',rows)
        self.assertEqual(values,{'1':(0,0,None),'2':(None,5,None)})
        for bad in [detail.replace('2,345','2,346'),detail.replace('2026-09-02','2025-09-02'),detail.replace('<td>2</td>','<td>3</td>')]:
            with self.assertRaises(ValueError):parse_bonuses(bad,'2026-09-01','TEST ONLY',rows)
        with self.assertRaises(ValueError):number('1.5')

    def test_bonus_combined_accepts_decimal_denominator_and_missing_marker(self):
        rows=parse_report(FIXTURE,'2026-09-01','https://min-repo.com/3326458/?kishu=all')
        detail='<h1>9/1(火) ゴッサムシティ</h1><time datetime="2026-09-02">9/2</time><table><tr><th>台番</th><th>G数</th><th>BB</th><th>RB</th><th>合成</th></tr><tr><td>1</td><td>0</td><td>0</td><td>0</td><td>-</td></tr><tr><td>2</td><td>2,345</td><td>10</td><td>5</td><td>1/156.3</td></tr></table>'
        values=parse_bonuses(detail,'2026-09-01','TEST ONLY',rows)
        self.assertEqual(values,{'1':(0,0,None),'2':(10,5,'1/156.3')})

    def test_bonus_links_and_provenance_preserved_on_rescrape(self):
        rows=parse_report(FIXTURE,'2026-09-01','https://min-repo.com/3326458/?kishu=all')
        html=FIXTURE+'<a href="?kishu=TEST%20ONLY">TEST ONLY</a><a href="https://example.com/?kishu=TEST%20ONLY">TEST ONLY</a>'
        link='https://min-repo.com/3326458/?kishu=TEST%20ONLY'
        self.assertEqual(bonus_links(html,'https://min-repo.com/3326458/',rows),{'TEST ONLY':link})
        enriched=dict(rows[0],bb=1,rb=2,bonus_source_url=link)
        self.store.collector.save_rows([enriched]);self.store.collector.save_rows(rows)
        with self.store.connect() as db:
            saved=db.execute('SELECT bb,rb,bonus_source_url FROM scraped_observations WHERE seat=?',('1',)).fetchone()
            self.assertEqual(tuple(saved),(1,2,link))

    def test_successful_report_workflow_from_canonical_link(self):
        collector=self.store.collector; url='https://min-repo.com/3326458/'
        collector.seed=[{'day':'2026-09-01','url':url}];collector.expected_count=lambda day:2
        calls=[]
        def fetch(target):
            calls.append(target)
            if '/tag/' in target:return '<h1>ゴッサムシティ</h1><table></table>'
            if target==url:return FIXTURE+'<a href="?kishu=all">全台データ一覧</a>'
            return FIXTURE
        collector.fetch=fetch
        collector.progress={'id':'test-success','state':'running','added':0,'completed':0,'total':0}
        collector.run('2026-09-01','2026-09-01')
        self.assertEqual(calls,[url,url+'?kishu=all'])
        self.assertEqual(collector.progress['state'],'complete')
        self.assertEqual(collector.progress['added'],2)
        self.assertEqual(collector.status()['coverage'],[{'status':'complete','days':1}])

    def test_index_year_pagination_and_durable_links(self):
        url='https://min-repo.com/3382117/'
        html='<h1>ゴッサムシティ</h1><a href="'+url+'">9/29(火)</a><a href="'+TAG+'page/2/">»</a><a href="https://example.com/page/3/">次へ</a>'
        reports,next_page=index_links(html,TAG,{},'2026-10-01')
        self.assertEqual(reports,{'2026-09-29':url})
        self.assertEqual(next_page,TAG+'page/2/')
        collector=self.store.collector;collector.seed=[]
        collector.fetch=lambda target:html
        collector.progress={'id':'test-index','state':'running','added':0,'completed':0,'total':0}
        collector.run('2026-09-30','2026-09-30')
        with self.store.connect() as db:
            self.assertEqual(tuple(db.execute('SELECT day,url FROM scrape_report_index').fetchone()),('2026-09-29',url))
        # Old requested range cannot turn a current report into a 2023 report.
        self.assertNotIn('2023-09-29',reports)
        known={'2025-12-31':'https://min-repo.com/999/'}
        old='<h1>ゴッサムシティ</h1><a href="https://min-repo.com/999/">12/31(水)</a>'
        self.assertEqual(index_links(old,TAG,known,'2026-01-01')[0],known)

    def test_changed_model_or_games_invalidates_previous_bonuses(self):
        rows=parse_report(FIXTURE,'2026-09-01','https://min-repo.com/3326458/?kishu=all')
        collector=self.store.collector
        for changed in (dict(rows[0],model='TEST CHANGED'),dict(rows[0],games=1)):
            collector.save_rows([dict(rows[0],bb=1,rb=2,bonus_source_url='https://min-repo.com/3326458/?kishu=TEST')])
            collector.save_rows([changed])
            with self.store.connect() as db:
                self.assertEqual(tuple(db.execute('SELECT bb,rb,bonus_source_url FROM scraped_observations WHERE seat="1"').fetchone()),(None,None,None))

    def test_bonus_resume_keeps_numeric_day_and_skips_completed_models(self):
        collector=self.store.collector;url='https://min-repo.com/3326458/'
        all_html=FIXTURE+'<a href="?kishu=TEST%20ONLY">TEST ONLY</a>'
        detail='<h1>9/1(火) ゴッサムシティ</h1><time datetime="2026-09-02">9/2</time><table><tr><th>台番</th><th>G数</th><th>BB</th><th>RB</th></tr><tr><td>1</td><td>0</td><td>0</td><td>0</td></tr><tr><td>2</td><td>2345</td><td>10</td><td>5</td></tr></table>'
        collector.seed=[{'day':'2026-09-01','url':url}];collector.expected_count=lambda day:2
        def failing_fetch(target,**kwargs):
            if '/tag/' in target:return '<h1>ゴッサムシティ</h1><table></table>'
            if target==url:return all_html+'<a href="?kishu=all">全台データ一覧</a>'
            if target.endswith('kishu=all'):return all_html
            raise RuntimeError('TEST ONLY: unreadable bonus page')
        collector.fetch=failing_fetch
        collector.progress={'id':'test-bonus-fail','state':'running','added':0,'completed':0,'total':0}
        collector.run('2026-09-01','2026-09-01',True)
        self.assertEqual(collector.progress['state'],'complete')
        self.assertEqual(collector.status()['coverage'],[{'status':'complete','days':1}])
        self.assertEqual(collector.status()['bonusFailures'][0]['status'],'partial')
        calls=[]
        def success_fetch(target,**kwargs):
            calls.append(target)
            return detail if target.endswith('kishu=TEST%20ONLY') else failing_fetch(target)
        collector.fetch=success_fetch
        collector.progress={'id':'test-bonus-resume','state':'running','added':0,'completed':0,'total':0}
        collector.run('2026-09-01','2026-09-01',True)
        self.assertEqual(collector.progress['state'],'complete')
        self.assertEqual(collector.status()['summary']['missingBonuses'],0)
        collector.collect_bonuses(all_html,'2026-09-01',url,parse_report(FIXTURE,'2026-09-01',url+'?kishu=all'))
        self.assertEqual(sum(c.endswith('kishu=TEST%20ONLY') for c in calls),1)

if __name__=='__main__':unittest.main()
