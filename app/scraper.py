"""User-started public-data collection. No AI service or guessed observations.

The desktop host supplies ordinary, rendered public pages through a broker.
No authentication tokens/cookies are extracted from the source browser.
"""
import csv
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import threading
import time
import uuid
from urllib.parse import quote, urljoin, urlsplit, parse_qs, unquote

START = '2023-04-27'
TAG = 'https://min-repo.com/tag/' + quote('ゴッサムシティ') + '/'
COLUMNS = ['date','seat','model','games','bb','rb','net','rate','payout_percent','source_url','published_at','fetched_at','status','bonus_source_url']
SCHEMA = '''
CREATE TABLE IF NOT EXISTS scraped_observations(day TEXT,seat TEXT,model TEXT NOT NULL,games INTEGER,bb INTEGER,rb INTEGER,net INTEGER,rate TEXT NOT NULL,payout_percent REAL,source_url TEXT NOT NULL,published_at TEXT,fetched_at TEXT NOT NULL,PRIMARY KEY(day,seat));
CREATE TABLE IF NOT EXISTS scrape_days(day TEXT PRIMARY KEY,url TEXT,status TEXT NOT NULL,rows INTEGER NOT NULL DEFAULT 0,message TEXT NOT NULL DEFAULT '',checked_at TEXT);
CREATE TABLE IF NOT EXISTS scrape_runs(id TEXT PRIMARY KEY,state TEXT,start_day TEXT,end_day TEXT,started_at TEXT,finished_at TEXT,message TEXT);
CREATE TABLE IF NOT EXISTS scrape_bonus_days(day TEXT PRIMARY KEY,status TEXT NOT NULL,rows INTEGER NOT NULL,message TEXT NOT NULL,checked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scrape_report_index(day TEXT PRIMARY KEY,url TEXT NOT NULL UNIQUE,checked_at TEXT NOT NULL);
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
                rate='unknown',payout_percent=number(values.get('出率',''),False),source_url=url,
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
    models={r['model'] for r in rows}; links={}
    report=urlsplit(report_url)
    for href,text in TableParser(html).links:
        candidate=urljoin(report_url,href); link=urlsplit(candidate)
        query=parse_qs(link.query)
        if link.scheme=='https' and link.hostname=='min-repo.com' and link.path==report.path and set(query)=={'kishu'} and text in models and query['kishu']==[text]:
            links[text]=candidate
    return links

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

def parse_bonuses(html, day, model, rows):
    doc=TableParser(html); expected=date.fromisoformat(day)
    match=re.search(r'(\d{1,2})/(\d{1,2})\(',' '.join(doc.headings))
    if 'ゴッサムシティ' not in ' '.join(doc.headings) or not match or tuple(map(int,match.groups()))!=(expected.month,expected.day):
        raise ValueError('機種別ページの店舗名・日付が一致しません。')
    if not doc.times or not 0<=(date.fromisoformat(doc.times[0][0][:10])-expected).days<=14:
        raise ValueError('機種別ページの公開年月日が一致しません。')
    wanted={r['seat']:r for r in rows if r['model']==model}; result={}
    for table in doc.tables:
        if not table or not {'台番','G数','BB','RB'}.issubset(table[0]):continue
        for cells in table[1:]:
            if len(cells)!=len(table[0]):continue
            values=dict(zip(table[0],cells)); seat=values.get('台番','')
            if not seat.isdigit():continue
            seat=str(int(seat))
            if seat not in wanted or seat in result:raise ValueError('機種別ページの台番号が一致しないか重複しています。')
            if number(values['G数'])!=wanted[seat]['games']:raise ValueError('全台表と機種別ページのG数が一致しません。')
            bb,rb=number(values['BB']),number(values['RB'])
            if any(n is not None and not 0<=n<=1000000 for n in (bb,rb)):raise ValueError('BB/RBの値が不正です。')
            result[seat]=(bb,rb)
    if set(result)!=set(wanted):raise ValueError('機種別ページで対象台のBB/RB一覧を網羅できません。')
    return result

class Collector:
    def __init__(self, store, index_path):
        self.store=store; self.lock=threading.Lock(); self.export_lock=threading.Lock(); self.event=threading.Event(); self.cancelled=threading.Event()
        self.pending=None; self.answer=None; self.active=False; self.progress={'state':'idle'}
        self.seed=json.loads(Path(index_path).read_text(encoding='utf-8'))['reports'] if Path(index_path).exists() else []
        with store.connect() as db:
            db.executescript(SCHEMA)
            if 'bonus_source_url' not in {r[1] for r in db.execute('PRAGMA table_info(scraped_observations)')}:
                db.execute('ALTER TABLE scraped_observations ADD COLUMN bonus_source_url TEXT')
            db.execute("UPDATE scrape_runs SET state='interrupted',finished_at=?,message='アプリ終了で中断。次回再開できます。' WHERE state='running'",(now(),))
            previous=db.execute('SELECT * FROM scrape_runs ORDER BY started_at DESC LIMIT 1').fetchone()
            if previous:self.progress.update(state=previous['state'],id=previous['id'],start=previous['start_day'],end=previous['end_day'],message=previous['message'])

    def fetch(self,url,pace=1.0):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname!='min-repo.com': raise ValueError('取得元が許可された公開サイトではありません。')
        if self.cancelled.is_set(): raise RuntimeError('取得を停止しました。')
        token=uuid.uuid4().hex
        with self.lock:
            self.pending={'id':token,'url':url}; self.answer=None; self.event.clear()
        if not self.event.wait(75):
            with self.lock: self.pending=None
            raise RuntimeError('公開ページを読む専用ブラウザーの応答待ちで停止しました。EXEから起動してください。')
        with self.lock: result=self.answer; self.pending=None
        if self.cancelled.is_set(): raise RuntimeError('取得を停止しました。')
        if not result or result.get('error'): raise RuntimeError((result or {}).get('error','ブラウザー取得に失敗しました。'))
        # Pace even cached responses. Never create parallel source-page requests.
        if self.cancelled.wait(pace): raise RuntimeError('取得を停止しました。')
        return result['html']

    def browser_task(self):
        with self.lock: return self.pending or {}

    def browser_result(self,payload):
        with self.lock:
            if not self.pending or payload.get('id')!=self.pending['id']: raise ValueError('期限切れのページ取得です。')
            html=payload.get('html','')
            if not isinstance(html,str) or len(html)>8*1024*1024: raise ValueError('ページが大きすぎます。')
            self.answer={'html':html,'error':str(payload.get('error',''))}; self.event.set()
        return {'ok':True}

    def status(self):
        with self.lock: result=dict(self.progress)
        with self.store.connect() as db:
            result['summary']=dict(db.execute('SELECT COUNT(*) records,COUNT(DISTINCT day) days,MIN(day) first,MAX(day) last,SUM(bb IS NULL OR rb IS NULL) missingBonuses,SUM(net IS NULL) missingNet FROM scraped_observations').fetchone())
            result['coverage']=[dict(r) for r in db.execute('SELECT status,COUNT(*) days FROM scrape_days GROUP BY status')]
            result['failures']=[dict(r) for r in db.execute("SELECT day,status,message,url FROM scrape_days WHERE status!='complete' ORDER BY day DESC LIMIT 40")]
            result['bonusFailures']=[dict(r) for r in db.execute("SELECT day,status,message,rows FROM scrape_bonus_days WHERE status!='complete' ORDER BY day DESC LIMIT 40")]
        return result

    def start(self,end=None,start=None,include_bonus=False):
        if not isinstance(include_bonus,bool):raise ValueError('BB/RB取得の指定が不正です。')
        end=end or today(); date.fromisoformat(end)
        if not START<=end<=today(): raise ValueError('取得終了日が対象範囲外です。')
        if start and (date.fromisoformat(start).isoformat()!=start or not START<=start<=end): raise ValueError('取得開始日が対象範囲外です。')
        with self.lock:
            if self.active: return dict(self.progress)
            self.active=True; self.cancelled.clear()
            self.progress={'state':'running','id':uuid.uuid4().hex,'end':end,'message':'公開一覧を照合しています。','added':0,'completed':0,'total':0}
        threading.Thread(target=self.run,args=(end,start,include_bonus),daemon=True).start()
        return self.status()

    def stop(self):
        self.cancelled.set(); self.event.set(); return {'ok':True}

    def run(self,end,start,include_bonus=False):
        run_id=self.progress['id']
        requested_start=start
        try:
            with self.store.connect() as db:
                last=db.execute('SELECT MAX(day) FROM scraped_observations').fetchone()[0]
                start=start or ((date.fromisoformat(last)+timedelta(days=1)).isoformat() if last else START)
                # Retry published but failed days, never skip them behind the watermark.
                retry=[r[0] for r in db.execute("SELECT day FROM scrape_days WHERE status IN ('failed','partial','not-published') AND day BETWEEN ? AND ? ORDER BY day",(requested_start or START,end))]
                if include_bonus:
                    retry+= [r[0] for r in db.execute("SELECT DISTINCT s.day FROM scraped_observations s LEFT JOIN scrape_bonus_days b ON b.day=s.day WHERE s.day BETWEEN ? AND ? AND (b.status IS NULL OR b.status!='complete') ORDER BY s.day",(requested_start or START,end))]
                db.execute('INSERT INTO scrape_runs VALUES(?,?,?,?,?,?,?)',(run_id,'running',start,end,now(),None,''))
            with self.lock: self.progress['start']=start
            days=[]; d=date.fromisoformat(start)
            while d<=date.fromisoformat(end): days.append(d.isoformat()); d+=timedelta(days=1)
            days=list(dict.fromkeys(days+retry))
            reports={r['day']:r['url'] for r in self.seed}
            with self.store.connect() as db:
                reports.update({r['day']:r['url'] for r in db.execute('SELECT day,url FROM scrape_report_index')})
            # Follow actual next-page links until the requested older dates are
            # covered. New report links survive app/package updates in SQLite.
            page=TAG; visited=set(); oldest_needed=min(days) if days else end
            while page and page not in visited:
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
                    bonus_done=db.execute('SELECT status FROM scrape_bonus_days WHERE day=?',(day,)).fetchone()
                if done and done[0]=='complete' and (not include_bonus or (bonus_done and bonus_done[0]=='complete')):
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
                    all_url=all_data_link(self.fetch(url),day,url)
                    all_html=self.fetch(all_url)
                    rows=parse_report(all_html,day,all_url)
                    expected=self.expected_count(day)
                    numbers={r['seat'] for r in rows}
                    complete=numbers=={str(n) for n in range(1,expected+1)} if expected else False
                    self.save_rows(rows)
                    self.day_status(day,url,'complete' if complete else 'partial',len(rows),'' if complete else '台番号の全台網羅を確認できません。')
                    all_saved=True
                    with self.lock:
                        self.progress['added']+=len(rows); self.progress['completed']+=1
                    if include_bonus:self.collect_bonuses(all_html,day,url,rows)
                except (ValueError,RuntimeError) as ex:
                    with self.store.connect() as db:
                        saved=db.execute('SELECT COUNT(*) FROM scraped_observations WHERE day=?',(day,)).fetchone()[0]
                    # A bonus-page failure does not erase a successfully checked
                    # all-machine day; bonus coverage has its own status table.
                    if not all_saved:
                        self.day_status(day,url,'failed',saved,str(ex))
                    # Readability/access failures stop, rather than cycling requests.
                    raise
                if self.progress['completed']%10==0: self.export(self.store.folder/'exports',START,end)
            self.export(self.store.folder/'exports',START,end)
            with self.lock: self.progress.update(state='complete',message='公開データの取得処理が終了しました。欠落と未掲載は取得状況CSVを確認してください。')
        except Exception as ex:
            with self.lock: self.progress.update(state='stopped' if self.cancelled.is_set() else 'failed',message=str(ex))
            self.export(self.store.folder/'exports',START,end)
        finally:
            with self.store.connect() as db:
                db.execute('UPDATE scrape_runs SET state=?,finished_at=?,message=? WHERE id=?',(self.progress['state'],now(),self.progress['message'],run_id))
            with self.lock: self.active=False; self.pending=None

    def expected_count(self,day):
        p=next((p for p in self.store.periods if p['validFrom']<=day<p['validToExclusive']),None)
        return p['seatCount'] if p else 310  # nominal 310; not a substitute for data

    def day_status(self,day,url,status,count,message):
        with self.store.connect() as db:
            db.execute('INSERT INTO scrape_days VALUES(?,?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET url=excluded.url,status=excluded.status,rows=excluded.rows,message=excluded.message,checked_at=excluded.checked_at',(day,url,status,count,message,now()))

    def collect_bonuses(self,html,day,url,rows):
        links=bonus_links(html,url,rows); covered=set()
        with self.store.connect() as db:
            saved={r['seat']:dict(r) for r in db.execute('SELECT * FROM scraped_observations WHERE day=?',(day,))}
        try:
            # Larger groups first, reducing missing rows with fewer page visits.
            for model,link in sorted(links.items(),key=lambda item:-sum(r['model']==item[0] for r in rows)):
                group=[r for r in rows if r['model']==model]
                if all(saved[r['seat']]['bonus_source_url']==link and saved[r['seat']]['model']==model and saved[r['seat']]['games']==r['games'] for r in group):
                    covered.update(r['seat'] for r in group);continue
                with self.lock:self.progress.update(message=day+' のBB/RBを取得: '+model)
                # Model pages are a second, potentially large traversal. Keep
                # a longer ordinary-browser interval so the public site is not
                # hit with a burst of same-report requests.
                values=parse_bonuses(self.fetch(link,pace=3.0),day,model,rows)
                selected=[]
                for row in group:
                    enriched=dict(row); enriched['bb'],enriched['rb']=values[row['seat']]; enriched['bonus_source_url']=link; enriched['fetched_at']=now()
                    selected.append(enriched); covered.add(row['seat'])
                self.save_rows(selected)
                self.bonus_status(day,'partial',len(covered),'BB/RB取得を継続中。保存済み機種は再取得しません。')
        except (ValueError,RuntimeError) as ex:
            self.bonus_status(day,'failed',len(covered),str(ex));raise
        status='complete' if covered=={r['seat'] for r in rows} else 'partial'
        message='' if status=='complete' else '公開リンクを確認できない機種があるためBB/RB取得は部分的です。'
        self.bonus_status(day,status,len(covered),message)

    def bonus_status(self,day,status,count,message):
        with self.store.connect() as db:
            db.execute('INSERT INTO scrape_bonus_days VALUES(?,?,?,?,?) ON CONFLICT(day) DO UPDATE SET status=excluded.status,rows=excluded.rows,message=excluded.message,checked_at=excluded.checked_at',(day,status,count,message,now()))

    def save_rows(self,rows):
        with self.store.connect() as db:
            db.executemany('''INSERT INTO scraped_observations(day,seat,model,games,bb,rb,net,rate,payout_percent,source_url,published_at,fetched_at,bonus_source_url) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(day,seat) DO UPDATE SET model=excluded.model,games=excluded.games,
                bb=CASE WHEN excluded.model=scraped_observations.model AND excluded.games IS scraped_observations.games THEN COALESCE(excluded.bb,scraped_observations.bb) ELSE excluded.bb END,
                rb=CASE WHEN excluded.model=scraped_observations.model AND excluded.games IS scraped_observations.games THEN COALESCE(excluded.rb,scraped_observations.rb) ELSE excluded.rb END,
                net=excluded.net,payout_percent=excluded.payout_percent,source_url=excluded.source_url,published_at=excluded.published_at,fetched_at=excluded.fetched_at,
                bonus_source_url=CASE WHEN excluded.model=scraped_observations.model AND excluded.games IS scraped_observations.games THEN COALESCE(excluded.bonus_source_url,scraped_observations.bonus_source_url) ELSE excluded.bonus_source_url END''',
                [(r['date'],r['seat'],r['model'],r['games'],r['bb'],r['rb'],r['net'],r['rate'],r['payout_percent'],r['source_url'],r['published_at'],r['fetched_at'],r.get('bonus_source_url')) for r in rows])

    def export(self,folder,start,end):
        with self.export_lock:
            return self._export(folder,start,end)

    def _export(self,folder,start,end):
        folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
        with self.store.connect() as db:
            rows=[dict(r) for r in db.execute('SELECT day AS date,seat,model,games,bb,rb,net,rate,payout_percent,source_url,published_at,fetched_at,bonus_source_url FROM scraped_observations WHERE day BETWEEN ? AND ? ORDER BY day,CAST(seat AS INTEGER)',(start,end))]
            coverage={r['day']:dict(r) for r in db.execute('SELECT * FROM scrape_days WHERE day BETWEEN ? AND ? ORDER BY day',(start,end))}
            bonus_coverage={r['day']:dict(r) for r in db.execute('SELECT * FROM scrape_bonus_days WHERE day BETWEEN ? AND ?',(start,end))}
        for r in rows: r['status']='scraped'
        data_file=folder/f'gotham-city-{start}_{end}.csv'
        self.write_csv(data_file,COLUMNS,rows)
        days=[]; d=date.fromisoformat(start)
        while d<=date.fromisoformat(end):
            key=d.isoformat(); entry=coverage.get(key,{'day':key,'status':'not-attempted','rows':0,'message':'未取得','url':'','checked_at':''})
            bonus=bonus_coverage.get(key,{})
            entry.update(bonus_status=bonus.get('status','not-attempted'),bonus_rows=bonus.get('rows',0),bonus_message=bonus.get('message','BB/RB追加取得は未実行'))
            days.append(entry); d+=timedelta(days=1)
        self.write_csv(folder/f'coverage-{start}_{end}.csv',['day','status','rows','message','url','checked_at','bonus_status','bonus_rows','bonus_message'],days)
        return {'path':str(data_file),'rows':len(rows),'days':len({r['date'] for r in rows})}

    @staticmethod
    def write_csv(path,columns,rows):
        tmp=path.with_suffix('.csv.tmp')
        with tmp.open('w',encoding='utf-8-sig',newline='') as file:
            writer=csv.DictWriter(file,fieldnames=columns,extrasaction='ignore'); writer.writeheader();writer.writerows(rows)
        tmp.replace(path)
