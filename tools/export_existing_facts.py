"""Export the already acquired machine-name facts without inventing metrics."""
import argparse
import csv
from datetime import date, timedelta
import json
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cache',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--start',default='2023-04-27');parser.add_argument('--end',default='2026-09-01')
    args=parser.parse_args()
    cache=Path(args.cache);output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    rows=[];coverage={};reports={r['day']:r['url'] for r in json.loads((cache/'index.json').read_text(encoding='utf-8'))['reports']}
    for path in sorted(cache.glob('20??-??-??.json')):
        value=json.loads(path.read_text(encoding='utf-8'));day=value['day']
        if not args.start<=day<=args.end:continue
        facts=value.get('seats',{})
        for seat,model in sorted(facts.items(),key=lambda s:int(s[0])):
            rows.append(dict(date=day,seat=seat,model=model,games='',bb='',rb='',net='',rate='unknown',payout_percent='',source_url=value['url']+'?num='+seat,published_at=value.get('publishedAt'),status='machine-name-only'))
        coverage[day]=dict(date=day,known_seats=len(facts),status='machine-name-only',source_url=value['url'],numeric_status='not-acquired')
    keys=[(r['date'],r['seat']) for r in rows]
    assert len(keys)==len(set(keys)), 'Duplicate day/seat'
    fields=['date','seat','model','games','bb','rb','net','rate','payout_percent','source_url','published_at','status']
    target=output/f'available-data-{args.start}_{args.end}.csv'
    with target.open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=fields);writer.writeheader();writer.writerows(rows)
    calendar=[];d=date.fromisoformat(args.start)
    while d<=date.fromisoformat(args.end):
        day=d.isoformat();calendar.append(coverage.get(day,dict(date=day,known_seats=0,status='not-acquired' if day in reports else 'no-indexed-report',source_url=reports.get(day,''),numeric_status='not-acquired')));d+=timedelta(days=1)
    with (output/f'available-data-coverage-{args.start}_{args.end}.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=['date','known_seats','status','source_url','numeric_status']);writer.writeheader();writer.writerows(calendar)
    print(json.dumps(dict(path=str(target),rows=len(rows),days=len(coverage),calendarDays=len(calendar)),ensure_ascii=False))

if __name__=='__main__':main()
