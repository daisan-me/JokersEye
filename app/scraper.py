"""User-started public-data collection. No AI service or guessed observations.

The desktop host supplies ordinary, rendered public pages through a broker.
No authentication tokens/cookies are extracted from the source browser.
"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import threading
import time
import uuid
from urllib.parse import quote, urljoin, urlsplit, parse_qs, unquote

START = '2023-04-27'
# User decision (2026-10-07): up to 240 public pages a minute, read by 12 source-browser
# windows that each start at most one page every 1.5 seconds (the desktop enforces the
# per-window cycle; the collector enforces the overall rate).
BROWSER_WINDOWS = 12
PAGES_PER_MINUTE = 240
RESTRICTED = '公開サイトが取得を制限しました'
TAG = 'https://min-repo.com/tag/' + quote('ゴッサムシティ') + '/'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS scraped_observations(day TEXT,seat TEXT,model TEXT NOT NULL,games INTEGER,bb INTEGER,rb INTEGER,combined TEXT,net INTEGER,payout_percent REAL,source_url TEXT NOT NULL,published_at TEXT,fetched_at TEXT NOT NULL,PRIMARY KEY(day,seat));
CREATE TABLE IF NOT EXISTS scrape_days(day TEXT PRIMARY KEY,url TEXT,status TEXT NOT NULL,rows INTEGER NOT NULL DEFAULT 0,message TEXT NOT NULL DEFAULT '',checked_at TEXT);
CREATE TABLE IF NOT EXISTS scrape_runs(id TEXT PRIMARY KEY,state TEXT,start_day TEXT,end_day TEXT,started_at TEXT,finished_at TEXT,message TEXT);
CREATE TABLE IF NOT EXISTS scrape_bonus_days(day TEXT PRIMARY KEY,status TEXT NOT NULL,rows INTEGER NOT NULL,message TEXT NOT NULL,checked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scrape_report_index(day TEXT PRIMARY KEY,url TEXT NOT NULL UNIQUE,checked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scrape_bonus_targets(day TEXT,model TEXT,seat TEXT,kind TEXT,url TEXT NOT NULL,PRIMARY KEY(day,model,seat,kind));
'''

def now():
    return datetime.now(timezone.utc).isoformat()

def today():
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()

class TableParser(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tables=[]; self.links=[]; self.headings=[]; self.times=[]
        self.table=None; self.row=None; self.cell=None; self.link=None; self.heading=None; self.timestamp=None
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='table': self.table=[]
        elif tag=='tr' and self.table is not None: self.row=[]
        elif tag in ('td','th') and self.row is not None: self.cell=[]
        elif tag=='a': self.link=[attrs.get('href',''),[]]
        elif tag=='h1': self.heading=[]
        elif tag=='time': self.timestamp=[attrs.get('datetime',''),[]]
    def handle_data(self, value):
        for container in (self.cell,self.heading):
            if container is not None: container.append(value)
        if self.link is not None: self.link[1].append(value)
        if self.timestamp is not None: self.timestamp[1].append(value)
    def handle_endtag(self, tag):
        text=lambda xs: re.sub(r'\s+',' ',' '.join(xs)).strip()
        if tag in ('td','th') and self.cell is not None:
            self.row.append(text(self.cell)); self.cell=None
        elif tag=='tr' and self.row is not None:
            self.table.append(self.row); self.row=None
        elif tag=='table' and self.table is not None:
            self.tables.append(self.table); self.table=None
        elif tag=='a' and self.link is not None:
            self.links.append((self.link[0],text(self.link[1]))); self.link=None
        elif tag=='h1' and self.heading is not None:
            self.headings.append(text(self.heading)); self.heading=None
        elif tag=='time' and self.timestamp is not None:
            self.times.append((self.timestamp[0],text(self.timestamp[1]))); self.timestamp=None

def number(value, integer=True):
    text=value.strip().replace(',','').replace('＋','+').replace('−','-').replace('%','')
    if text in ('','-','―','－','–','N/A'): return None
    if not re.fullmatch(r'[+-]?\d+' if integer else r'[+-]?\d+(?:\.\d+)?',text): raise ValueError('未対応の数値表記: '+value)
    return int(text) if integer else float(text)

def parse_report(html, day, url):
    doc=TableParser(html)
    expected=date.fromisoformat(day)
    headings=' '.join(doc.headings)
    match=re.search(r'(\d{1,2})/(\d{1,2})\(',headings)
    if 'ゴッサムシティ' not in headings or not match or tuple(map(int,match.groups())) != (expected.month,expected.day):
        raise ValueError('店舗名・対象日の一致を確認できません。ブラウザー確認や未掲載の可能性があります。')
    published=doc.times[0][0] if doc.times else None
    if published and not 0 <= (date.fromisoformat(published[:10])-expected).days <= 14:
        raise ValueError('公開年月日が対象日と一致しません。')
    rows={}
    for table in doc.tables:
        if not table or not all(k in table[0] for k in ('機種','台番','G数')): continue
        header=table[0]
        for cells in table[1:]:
            if len(cells)!=len(header): continue
            values=dict(zip(header,cells)); seat=values.get('台番','')
            if not seat.isdigit(): continue  # repeated headers are not data
            seat=str(int(seat))
            if seat in rows: raise ValueError('台番号が重複しています: '+seat)
            row=dict(date=day,seat=seat,model=values['機種'],games=number(values['G数']),
                bb=number(values.get('BB','')),rb=number(values.get('RB','')),net=number(values.get('差枚','')),
                payout_percent=number(values.get('出率',''),False),source_url=url,
                published_at=published,fetched_at=now(),status='scraped')
            if not row['model'] or any(row[k] is not None and not 0<=row[k]<=1000000 for k in ('games','bb','rb')):
                raise ValueError('機種名または回転・ボーナス数が不正です。')
            rows[seat]=row
    if not rows: raise ValueError('読み取れる全台表がありません。')
    return sorted(rows.values(),key=lambda r:int(r['seat']))

def all_data_link(html, day, report_url):
    """Follow the public report's own link, rather than guessing a route."""
    doc=TableParser(html)
    expected=date.fromisoformat(day)
    heading=' '.join(doc.headings)
    match=re.search(r'(\d{1,2})/(\d{1,2})\(',heading)
    if 'ゴッサムシティ' not in heading or not match or tuple(map(int,match.groups()))!=(expected.month,expected.day):
        raise ValueError('日別レポートの店舗名・日付を確認できません。')
    for href,text in doc.links:
        candidate=urljoin(report_url,href)
        link=urlsplit(candidate); report=urlsplit(report_url)
        if link.scheme=='https' and link.hostname=='min-repo.com' and link.path==report.path and link.query=='kishu=all':
            return candidate
    raise ValueError('日別レポートに全台データ一覧への公開リンクがありません。')

def bonus_links(html, report_url, rows):
    return {t['model']:t['url'] for t in bonus_targets(html,report_url,rows) if t['kind']=='model'}

def bonus_targets(html, report_url, rows):
    """Persist only links actually present on this day's public report."""
    models={r['model'] for r in rows}; seats={r['seat']:r for r in rows}; targets={}
    report=urlsplit(report_url)
    for href,text in TableParser(html).links:
        candidate=urljoin(report_url,href); link=urlsplit(candidate)
        query=parse_qs(link.query)
        if link.scheme!='https' or link.hostname!='min-repo.com' or link.path!=report.path or link.fragment:continue
        if set(query)=={'kishu'} and text in models and len(query['kishu'])==1 and re.sub(r'\s+',' ',query['kishu'][0]).strip()==text:
            targets[(text,'','model')]=dict(model=text,seat='',kind='model',url=candidate)
        if set(query)=={'num'} and len(query['num'])==1 and query['num'][0] in seats:
            seat=query['num'][0]; model=seats[seat]['model']
            if text==seat or (text==model and sum(r['model']==model for r in rows)==1):
                targets[(model,seat,'seat')]=dict(model=model,seat=seat,kind='seat',url=candidate)
    return list(targets.values())

def index_links(html, page_url, known, current_day):
    """Only date links in this shop's published listing. Never use range year."""
    doc=TableParser(html)
    if not any('ゴッサムシティ' in h for h in doc.headings):
        raise RuntimeError('店舗の公開一覧を読めません。アクセス確認が必要です。')
    reverse={url:day for day,url in known.items()}; reports={}; next_page=None
    weekdays='月火水木金土日'; current=date.fromisoformat(current_day)
    for href,text in doc.links:
        url=urljoin(page_url,href); parsed=urlsplit(url)
        match=re.fullmatch(r'(?:(20\d\d)/)?(\d{1,2})/(\d{1,2})\(([月火水木金土日])\)',text.strip())
        if match and re.fullmatch(r'https://min-repo\.com/\d+/',url):
            year,month,day,weekday=match.groups()
            candidate=date(int(year) if year else current.year,int(month),int(day))
            if not year and url in reverse:candidate=date.fromisoformat(reverse[url])
            if weekdays[candidate.weekday()]!=weekday:continue
            if START<=candidate.isoformat()<=current_day:reports[candidate.isoformat()]=url
        if text.strip() in ('»','次へ') and parsed.scheme=='https' and parsed.hostname=='min-repo.com' and not parsed.query and not parsed.fragment and re.fullmatch(re.escape(unquote(urlsplit(TAG).path))+r'page/\d+/',unquote(parsed.path)):
            next_page=url
    return reports,next_page

def parse_bonuses(html, day, model, rows, seat=None):
    doc=TableParser(html); expected=date.fromisoformat(day)
    match=re.search(r'(\d{1,2})/(\d{1,2})\(',' '.join(doc.headings))
    if 'ゴッサムシティ' not in ' '.join(doc.headings) or not match or tuple(map(int,match.groups()))!=(expected.month,expected.day):
        raise ValueError('機種別ページの店舗名・日付が一致しません。')
    if not doc.times or not 0<=(date.fromisoformat(doc.times[0][0][:10])-expected).days<=14:
        raise ValueError('機種別ページの公開年月日が一致しません。')
    wanted={r['seat']:r for r in rows if r['model']==model and (seat is None or r['seat']==seat)}; result={}
    if not wanted:raise ValueError('対象台が全台一覧にありません。')
    def read(values,number_text):
        if number(values['G数'])!=wanted[number_text]['games']:raise ValueError('全台表と詳細ページのG数が一致しません。')
        # Some detail tables hide negative net/payout values. Keep the values
        # from the all-seat table, and compare only publicly populated details.
        for name,key,integer_value in (('差枚','net',True),('出率','payout_percent',False)):
            detail=number(values.get(name,''),integer_value)
            if detail is not None and wanted[number_text].get(key) is not None and detail!=wanted[number_text][key]:
                raise ValueError('全台表と詳細ページの'+name+'が一致しません。')
        bb,rb=number(values['BB']),number(values['RB'])
        combined=values.get('合成','').strip().replace('／','/') or None
        if combined in ('-','―','－','–','N/A'):combined=None
        if combined is not None and not re.fullmatch(r'1/\d+(?:\.\d+)?',combined):raise ValueError('合成確率の値が不正です。')
        if any(n is not None and not 0<=n<=1000000 for n in (bb,rb)):raise ValueError('BB/RBの値が不正です。')
        return bb,rb,combined
    for table in doc.tables:
        if not table or not {'台番','G数','BB','RB'}.issubset(table[0]):continue
        for cells in table[1:]:
            if len(cells)!=len(table[0]):continue
            values=dict(zip(table[0],cells)); number_text=values.get('台番','')
            if not number_text.isdigit():continue
            number_text=str(int(number_text))
            if number_text not in wanted or number_text in result:raise ValueError('機種別ページの台番号が一致しないか重複しています。')
            result[number_text]=read(values,number_text)
    if not result and seat is not None:
        main=next((t for t in doc.tables if t and {'機種','G数'}.issubset(t[0]) and '台番' not in t[0]),None)
        bonus=next((t for t in doc.tables if t and t[0][:2]==['BB','RB']),None)
        # Do not interpret the past-ten-day table as today's bonus record.
        if not main or len(main)!=2 or not bonus or len(bonus)!=2:raise ValueError('個別台の当日BB/RB表がありません。')
        values=dict(zip(main[0],main[1]));values.update(zip(bonus[0],bonus[1]))
        if values.get('機種')!=model:raise ValueError('個別台の機種が一致しません。')
        result[seat]=read(values,seat)
    if set(result)!=set(wanted):raise ValueError('機種別ページで対象台のBB/RB一覧を網羅できません。')
    return result

class Collector:
    def __init__(self, store, index_path):
        self.store=store; self.lock=threading.Lock(); self.cancelled=threading.Event()
        # Page requests waiting for a source-browser window, and those being read now.
        self.ready=threading.Condition(self.lock); self.queue=deque(); self.tasks={}; self.next_dispatch=0.0
        self.halted=None  # set to the restriction message when the source site restricts access
        self.active=False; self.progress={'state':'idle'};self.page_expectations={}
        self.seed=json.loads(Path(index_path).read_text(encoding='utf-8'))['reports'] if Path(index_path).exists() else []
        with store.connect() as db:
            db.executescript(SCHEMA)
            if 'bonus_source_url' not in {r[1] for r in db.execute('PRAGMA table_info(scraped_observations)')}:
                db.execute('ALTER TABLE scraped_observations ADD COLUMN bonus_source_url TEXT')
            if 'rate' in {r[1] for r in db.execute('PRAGMA table_info(scraped_observations)')}:
                db.execute('ALTER TABLE scraped_observations DROP COLUMN rate')  # 貸区分 is no longer recorded
            db.execute("UPDATE scrape_runs SET state='interrupted',finished_at=?,message='アプリ終了で中断。次回再開できます。' WHERE state='running'",(now(),))
            previous=db.execute('SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT 1').fetchone()
            if previous:self.progress.update(state=previous['state'],id=previous['id'],start=previous['start_day'],end=previous['end_day'],message=previous['message'])

    def fetch(self,url):
        # Detail pages are retried by collect_bonuses through the actual report
        # link. Do not repeatedly navigate a transiently empty model URL here.
        attempts=1 if self.page_expectations.get(url,{}).get('kind','').startswith('bonus-') else 3
        for attempt in range(attempts):
            try:
                return self._fetch_once(url)
            except RuntimeError as ex:
                message=str(ex)
                if attempt >= attempts-1 or '公開ページを表示できませんでした' not in message:
                    raise
                with self.lock:
                    self.progress['message']='公開ページが空だったため通常再試行しています。'+str(attempt + 2)+'/3'
                if self.cancelled.wait(2.0):
                    raise RuntimeError('取得を停止しました。')

    def _fetch_once(self,url):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname!='min-repo.com': raise ValueError('取得元が許可された公開サイトではありません。')
        if self.cancelled.is_set(): raise RuntimeError('取得を停止しました。')
        if self.halted: raise RuntimeError(self.halted)
        task={'id':uuid.uuid4().hex,'url':url,**self.page_expectations.get(url,{}),'done':threading.Event(),'answer':None}
        with self.lock:
            self.tasks[task['id']]=task; self.queue.append(task['id']); self.ready.notify_all()
        if not task['done'].wait(75):
            with self.lock:
                self.tasks.pop(task['id'],None)
                if task['id'] in self.queue: self.queue.remove(task['id'])
            raise RuntimeError('公開ページを読む専用ブラウザーの応答待ちで停止しました。Joker’s eyeのデスクトップウィンドウから起動してください。')
        result=task['answer']
        if self.cancelled.is_set(): raise RuntimeError('取得を停止しました。')
        if not result or result.get('error'):
            message=(result or {}).get('error','ブラウザー取得に失敗しました。')
            if RESTRICTED in message: self.halted=message  # stop the other windows' pages too
            raise RuntimeError(message)
        return result['html']

    def browser_task(self,wait=0.0):
        """Hand the next page to a source-browser window, at most PAGES_PER_MINUTE overall.
        Waits up to `wait` seconds for one. `active` lets the desktop close its windows once the run has ended."""
        deadline=time.monotonic()+max(0.0,min(float(wait),5.0))
        with self.ready:
            while True:
                now_=time.monotonic()
                if self.queue and now_>=self.next_dispatch:
                    task=self.tasks[self.queue.popleft()]
                    self.next_dispatch=max(now_,self.next_dispatch)+60.0/PAGES_PER_MINUTE
                    return dict({k:v for k,v in task.items() if k not in ('done','answer')},active=self.active)
                if now_>=deadline: return {'active':self.active}
                self.ready.wait(min(deadline,self.next_dispatch if self.queue else deadline)-now_)

    def browser_result(self,payload):
        with self.lock:
            task=self.tasks.pop(payload.get('id'),None)
            if not task: raise ValueError('期限切れのページ取得です。')
            html=payload.get('html','')
            if not isinstance(html,str) or len(html)>8*1024*1024: raise ValueError('ページが大きすぎます。')
            task['answer']={'html':html,'error':str(payload.get('error',''))}; task['done'].set()
        return {'ok':True}

    def status(self):
        with self.lock: result=dict(self.progress,active=self.active,runFailures=list(self.progress.get('failures',[])))
        with self.store.connect() as db:
            result['summary']=dict(db.execute('SELECT COUNT(*) records,COUNT(DISTINCT day) days,MIN(day) first,MAX(day) last,SUM(bb IS NULL OR rb IS NULL) missingBonuses,SUM(net IS NULL) missingNet FROM scraped_observations').fetchone())
            result['coverage']=[dict(r) for r in db.execute('SELECT status,COUNT(*) days FROM scrape_days GROUP BY status')]
            result['failures']=[dict(r) for r in db.execute("SELECT day,status,message,url FROM scrape_days WHERE status!='complete' ORDER BY day DESC LIMIT 40")]
            result['bonusFailures']=[dict(r) for r in db.execute("SELECT day,status,message,rows FROM scrape_bonus_days WHERE status!='complete' ORDER BY day DESC LIMIT 40")]
        return result

    def start(self,end=None,start=None,include_bonus=True,only_dates=None,bonus_only=False):
        if not isinstance(include_bonus,bool):raise ValueError('BB/RB取得の指定が不正です。')
        if not isinstance(bonus_only,bool) or (bonus_only and not include_bonus):raise ValueError('BB/RB補完の指定が不正です。')
        end=end or today(); date.fromisoformat(end)
        if not START<=end<=today(): raise ValueError('取得終了日が対象範囲外です。')
        if start and (date.fromisoformat(start).isoformat()!=start or not START<=start<=end): raise ValueError('取得開始日が対象範囲外です。')
        if only_dates is not None:
            if not isinstance(only_dates,list):raise ValueError('差分日付の指定が不正です。')
            only_dates=sorted(set(only_dates))
            for value in only_dates:
                if date.fromisoformat(value).isoformat()!=value or not START<=value<=end:
                    raise ValueError('差分日付が取得範囲外です。')
        with self.lock:
            if self.active: return dict(self.progress)
            self.active=True; self.cancelled.clear(); self.halted=None
            self.progress={'state':'running','id':uuid.uuid4().hex,'end':end,'message':'公開一覧を照合しています。','added':0,'completed':0,'total':0,'onlyDates':len(only_dates) if only_dates is not None else None,'failures':[]}
        threading.Thread(target=self.run,args=(end,start,include_bonus,only_dates,bonus_only),daemon=True).start()
        return self.status()

    def stop(self):
        self.cancelled.set()
        with self.lock:
            self.queue.clear()
            for task in self.tasks.values(): task['done'].set()
            self.tasks.clear(); self.ready.notify_all()
        return {'ok':True}

    def run(self,end,start,include_bonus=False,only_dates=None,bonus_only=False):
        run_id=self.progress['id']
        requested_start=start
        try:
            with self.store.connect() as db:
                last=db.execute('SELECT MAX(day) FROM scraped_observations').fetchone()[0]
                start=start or ((date.fromisoformat(last)+timedelta(days=1)).isoformat() if last else START)
                # A normal "through today" run is a forward-only watermark
                # update. The old implementation appended every historical
                # failed/partial/not-published day, which sent the button back
                # to dates such as 2025-03-17 before it ever reached today.
                # Historical retries are intentional only for an explicit range
                # or an explicit only_dates delta list.
                retry=[]
                if only_dates is None and requested_start is not None:
                    retry=[r[0] for r in db.execute("SELECT day FROM scrape_days WHERE status IN ('failed','partial','not-published') AND day BETWEEN ? AND ? ORDER BY day",(requested_start,end))]
                    if include_bonus:
                        retry+= [r[0] for r in db.execute("SELECT DISTINCT day FROM scraped_observations WHERE day BETWEEN ? AND ? AND (bb IS NULL OR rb IS NULL) ORDER BY day",(requested_start,end))]
                db.execute('INSERT INTO scrape_runs VALUES(?,?,?,?,?,?,?)',(run_id,'running',start,end,now(),None,''))
            with self.lock: self.progress['start']=start
            if only_dates is not None:
                days=list(only_dates)
            else:
                days=[]; d=date.fromisoformat(start)
                while d<=date.fromisoformat(end): days.append(d.isoformat()); d+=timedelta(days=1)
                days=list(dict.fromkeys(days+retry))
            # Bonus repair targets only already saved records. A stale coverage
            # marker is not evidence of actual BB/RB completeness.
            if bonus_only:
                with self.store.connect() as db:
                    missing_days={r[0] for r in db.execute('SELECT DISTINCT day FROM scraped_observations WHERE bb IS NULL OR rb IS NULL')}
                days=[day for day in days if day in missing_days]
            reports={r['day']:r['url'] for r in self.seed}
            with self.store.connect() as db:
                reports.update({r['day']:r['url'] for r in db.execute('SELECT day,url FROM scrape_report_index')})
                for r in db.execute("SELECT day,url FROM scrape_days WHERE url!=''"):
                    parsed=urlsplit(r['url'])
                    if parsed.scheme=='https' and parsed.hostname=='min-repo.com' and re.fullmatch(r'/\d+/',parsed.path):
                        reports.setdefault(r['day'],parsed._replace(query='',fragment='').geturl())
            # Follow actual next-page links until the requested older dates are
            # covered. New report links survive app/package updates in SQLite.
            page=TAG; visited=set(); oldest_needed=min(days) if days else end
            while days and any(day not in reports for day in days) and page and page not in visited:
                if len(visited)>=100:raise RuntimeError('公開一覧のページ数が上限を超えました。取得状況を確認してください。')
                visited.add(page)
                discovered,next_page=index_links(self.fetch(page),page,reports,today())
                reports.update(discovered)
                with self.store.connect() as db:
                    db.executemany('INSERT INTO scrape_report_index VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET url=excluded.url,checked_at=excluded.checked_at',[(day,url,now()) for day,url in discovered.items()])
                if not next_page or (discovered and min(discovered)<=oldest_needed):break
                with self.lock:self.progress['message']='過去の公開一覧を確認しています。'+str(len(visited)+1)+'ページ目'
                page=next_page
            with self.lock: self.progress['total']=len(days)
            for day in days:
                if self.cancelled.is_set(): raise RuntimeError('取得を停止しました。')
                with self.store.connect() as db:
                    done=db.execute('SELECT status FROM scrape_days WHERE day=?',(day,)).fetchone()
                    existing=[dict(r) for r in db.execute('SELECT *,day AS date FROM scraped_observations WHERE day=? ORDER BY CAST(seat AS INTEGER)',(day,))]
                base_complete=bool(done and done[0]=='complete' and {r['seat'] for r in existing}=={str(n) for n in range(1,self.expected_count(day)+1)})
                bonus_complete=bool(existing) and all(r['bb'] is not None and r['rb'] is not None for r in existing)
                if (base_complete or bonus_only) and existing and (not include_bonus or bonus_complete):
                    if include_bonus:self.bonus_status(day,'complete',len(existing),'')
                    with self.lock:self.progress['completed']+=1
                    continue
                url=reports.get(day)
                if not url:
                    self.day_status(day,'','not-published',0,'公開一覧に当日のログが見つかりません。休業とは断定しません。')
                    with self.lock: self.progress['completed']+=1
                    continue
                with self.lock: self.progress.update(currentDay=day,message=day+' の全台表を読み取っています。')
                all_saved=False
                try:
                    all_html=None
                    if existing and (base_complete or bonus_only):
                        rows=existing
                    else:
                        all_url=all_data_link(self.fetch(url),day,url)
                        all_html=self.fetch(all_url)
                        rows=parse_report(all_html,day,all_url)
                        expected=self.expected_count(day)
                        complete={r['seat'] for r in rows}=={str(n) for n in range(1,expected+1)} if expected else False
                        self.save_rows(rows)
                        self.day_status(day,url,'complete' if complete else 'partial',len(rows),'' if complete else '台番号の全台網羅を確認できません。')
                        with self.lock:self.progress['added']+=len(rows)
                    all_saved=True
                    if include_bonus:
                        try:self.collect_bonuses(all_html,day,url,rows)
                        except (ValueError,RuntimeError) as ex:
                            with self.store.connect() as db:
                                covered=db.execute('SELECT COUNT(*) FROM scraped_observations WHERE day=? AND bb IS NOT NULL AND rb IS NOT NULL',(day,)).fetchone()[0]
                            self.bonus_status(day,'partial',covered,str(ex))
                            raise
                    with self.lock:self.progress['completed']+=1
                except (ValueError,RuntimeError) as ex:
                    with self.store.connect() as db:
                        saved=db.execute('SELECT COUNT(*) FROM scraped_observations WHERE day=?',(day,)).fetchone()[0]
                    message=str(ex)
                    # A failure on one date must not abort the delta run. The
                    # first implementation raised here, so one transient empty
                    # page left every later date unsaved. Persist the
                    # failed date and continue; the next delta run can retry it.
                    if not all_saved:
                        self.day_status(day,url,'partial' if saved else 'failed',saved,message)
                    with self.lock:
                        self.progress.setdefault('failures',[]).append({'day':day,'status':'partial' if saved else 'failed','message':message})
                        self.progress['completed']+=1
                    # HTTP 401/403/429 is a source-side access restriction, not
                    # a date-local scrape miss. Stop without attempting a burst
                    # of requests. Ordinary parse/empty-page failures continue.
                    if self.cancelled.is_set() or '公開サイトが取得を制限しました' in message:
                        raise
                    continue
            with self.store.connect() as db:
                missing=sum(db.execute('SELECT COUNT(*) FROM scraped_observations WHERE day=? AND (bb IS NULL OR rb IS NULL)',(day,)).fetchone()[0] for day in days)
            with self.lock: self.progress.update(state='complete',missingBonusRows=missing,message='取得処理が終了しました。対象日のBB/RB不足: '+str(missing)+'台。未掲載・失敗は取得状況を確認してください。')
        except Exception as ex:
            with self.lock: self.progress.update(state='stopped' if self.cancelled.is_set() else 'failed',message=str(ex))
        finally:
            with self.store.connect() as db:
                db.execute('UPDATE scrape_runs SET state=?,finished_at=?,message=? WHERE id=?',(self.progress['state'],now(),self.progress['message'],run_id))
            with self.lock: self.active=False; self.queue.clear(); self.tasks.clear(); self.ready.notify_all()

    def expected_count(self,day):
        p=next((p for p in self.store.periods if p['validFrom']<=day<p['validToExclusive']),None)
        return p['seatCount'] if p else 310  # nominal 310; not a substitute for data

    def day_status(self,day,url,status,count,message):
        with self.store.connect() as db:
            db.execute('INSERT INTO scrape_days VALUES(?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET url=excluded.url,status=excluded.status,rows=excluded.rows,message=excluded.message,checked_at=excluded.checked_at',(day,url,status,count,message,now()))

    def collect_bonuses(self,html,day,url,rows):
        groups={model:[r for r in rows if r['model']==model] for model in {r['model'] for r in rows}}
        failures=[]
        if html:self.cache_bonus_targets(html,day,url,rows)
        targets=self.get_bonus_targets(day)
        pending=[m for m in groups if not all(r['bb'] is not None and r['rb'] is not None for r in groups[m])]
        if pending and not html:
            # Enter through the day's public report, as an ordinary visitor does, before the
            # model pages. Opened directly in a fresh source-browser profile they come back
            # as empty documents, which left every BB/RB repair run without values.
            self.cache_bonus_targets(self.fetch(url),day,url,rows)
            targets=self.get_bonus_targets(day)
        with self.store.connect() as db:
            saved={r['seat']:dict(r) for r in db.execute('SELECT * FROM scraped_observations WHERE day=?',(day,))}
        complete=lambda r:saved.get(r['seat'],{}).get('bb') is not None and saved.get(r['seat'],{}).get('rb') is not None
        pending=[m for m in groups if not all(complete(r) for r in groups[m])]
        if any(not any(t['model']==m for t in targets) for m in pending):
            report_html=self.fetch(url)
            self.cache_bonus_targets(report_html,day,url,rows)
            targets=self.get_bonus_targets(day)
            if any(not any(t['model']==m for t in targets) for m in pending):
                all_url=all_data_link(report_html,day,url)
                self.cache_bonus_targets(self.fetch(all_url),day,url,rows)
                targets=self.get_bonus_targets(day)
        saving=threading.Lock()  # model pages are read by several windows at once
        def obtain(link,model,seat=None):
            expected=[r['seat'] for r in groups[model] if seat is None or r['seat']==seat]
            self.page_expectations[link]={'kind':'bonus-seat' if seat else 'bonus-model','seats':expected}
            values=parse_bonuses(self.fetch(link),day,model,groups[model],seat)
            with saving:
                for number_text,value in values.items():
                    base=next(r for r in groups[model] if r['seat']==number_text)
                    if complete(base):continue
                    self.save_bonus_values(day,base,value,link)
                    old=saved.get(number_text,{})
                    saved[number_text]=dict(old,bb=value[0] if old.get('bb') is None else old['bb'],rb=value[1] if old.get('rb') is None else old['rb'])
        def stop_now(ex):
            if RESTRICTED in str(ex):self.halted=str(ex)  # the other windows' jobs stop before reading
            return self.cancelled.is_set() or RESTRICTED in str(ex)
        def model_job(model,attempt):
            if self.cancelled.is_set():raise RuntimeError('取得を停止しました。')
            if self.halted:raise RuntimeError(self.halted)
            link=next((t['url'] for t in targets if t['model']==model and t['kind']=='model'),None)
            if link:
                try:obtain(link,model)
                except (ValueError,RuntimeError) as ex:
                    if stop_now(ex):raise
                    with saving:failures.append(model+': '+str(ex))
            # Defer a transient model miss once before individual-seat
            # fallback. A second model error must not skip fallback.
            if not link or attempt:
                for row in groups[model]:
                    if complete(row):continue
                    target=next((t['url'] for t in targets if t['model']==model and t['seat']==row['seat'] and t['kind']=='seat'),None)
                    if target:
                        try:obtain(target,model,row['seat'])
                        except (ValueError,RuntimeError) as ex:
                            if stop_now(ex):raise
                            with saving:failures.append(model+' '+row['seat']+': '+str(ex))
            covered=sum(complete(r) for r in rows)
            with self.lock:self.progress.update(currentDay=day,message=day+' のBB/RBを取得: '+model,bonusRows=covered,bonusTotal=len(rows))
        # Large/Juggler groups first, read by up to BROWSER_WINDOWS windows at once. Transient
        # misses are deferred, then revisited after the public report before individual-seat fallback.
        for attempt in range(2):
            todo=[m for m in sorted(pending,key=lambda m:(not ('ジャグラー' in m),-len(groups[m]),m)) if not all(complete(r) for r in groups[m])]
            if not todo:break
            if attempt:self.fetch(url)
            with ThreadPoolExecutor(max_workers=BROWSER_WINDOWS) as pool:
                futures=[pool.submit(model_job,model,attempt) for model in todo]
                errors=[]
                for future in futures:
                    try:future.result()
                    except (ValueError,RuntimeError) as ex:errors.append(ex)
            self.bonus_status(day,'partial',sum(complete(r) for r in rows),'BB/RB実値を補完中。揃っている台・機種は再取得しません。')
            if errors:raise next((ex for ex in errors if RESTRICTED in str(ex)),errors[0])
        covered={r['seat'] for r in rows if complete(r)}
        status='complete' if covered=={r['seat'] for r in rows} else 'partial'
        message='' if status=='complete' else '一部機種のBB/RB取得が未完了です。'+(' '+ ' / '.join(failures[:3]) if failures else '')
        self.bonus_status(day,status,len(covered),message)

    def cache_bonus_targets(self,html,day,url,rows):
        with self.store.connect() as db:
            db.executemany('INSERT INTO scrape_bonus_targets VALUES(?,?,?,?,?) ON CONFLICT(day,model,seat,kind) DO UPDATE SET url=excluded.url',
                [(day,t['model'],t['seat'],t['kind'],t['url']) for t in bonus_targets(html,url,rows)])

    def get_bonus_targets(self,day):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM scrape_bonus_targets WHERE day=?',(day,))]

    def save_bonus_values(self,day,row,values,url):
        with self.store.connect() as db:
            previous=db.execute('SELECT bb,rb FROM scraped_observations WHERE day=? AND seat=?',(day,row['seat'])).fetchone()
            if previous and any(old is not None and new is not None and old!=new for old,new in zip(previous,values[:2])):
                raise ValueError('保存済みBB/RBと詳細ページの実値が一致しません。既存値を保持します。')
            cursor=db.execute('''UPDATE scraped_observations SET bb=COALESCE(bb,?),rb=COALESCE(rb,?),combined=COALESCE(combined,?),bonus_source_url=?,fetched_at=?
                WHERE day=? AND seat=? AND model=? AND games IS ?''',(*values,url,now(),day,row['seat'],row['model'],row['games']))
            if cursor.rowcount!=1:raise ValueError('BB/RB保存時に対象台の基礎値が変わりました。')

    def bonus_status(self,day,status,count,message):
        with self.store.connect() as db:
            db.execute('INSERT INTO scrape_bonus_days VALUES(?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET status=excluded.status,rows=excluded.rows,message=excluded.message,checked_at=excluded.checked_at',(day,status,count,message,now()))

    def save_rows(self,rows):
        with self.store.connect() as db:
            db.executemany('''INSERT INTO scraped_observations(day,seat,model,games,bb,rb,combined,net,payout_percent,source_url,published_at,fetched_at,bonus_source_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(day,seat) DO UPDATE SET model=excluded.model,games=excluded.games,
                bb=CASE WHEN excluded.model=scraped_observations.model AND excluded.games IS scraped_observations.games THEN COALESCE(excluded.bb,scraped_observations.bb) ELSE excluded.bb END,
                rb=CASE WHEN excluded.model=scraped_observations.model AND excluded.games IS scraped_observations.games THEN COALESCE(excluded.rb,scraped_observations.rb) ELSE excluded.rb END,
                combined=CASE WHEN excluded.model=scraped_observations.model AND excluded.games IS scraped_observations.games THEN COALESCE(excluded.combined,scraped_observations.combined) ELSE excluded.combined END,
                net=excluded.net,payout_percent=excluded.payout_percent,source_url=excluded.source_url,published_at=excluded.published_at,fetched_at=excluded.fetched_at,
                bonus_source_url=CASE WHEN excluded.model=scraped_observations.model AND excluded.games IS scraped_observations.games THEN COALESCE(excluded.bonus_source_url,scraped_observations.bonus_source_url) ELSE excluded.bonus_source_url END''',
                [(r['date'],r['seat'],r['model'],r['games'],r['bb'],r['rb'],r.get('combined'),r['net'],r['payout_percent'],r['source_url'],r['published_at'],r['fetched_at'],r.get('bonus_source_url')) for r in rows])
