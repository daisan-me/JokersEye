"""Read-only audit of existing inputs; never scrape or estimate settings."""
from collections import defaultdict
import csv
import json
from pathlib import Path
import sqlite3
import unicodedata


def audit(folder):
    result = {}
    with sqlite3.connect((folder / 'jokers-eye.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.create_function('is_juggler', 1, lambda name: 'ジャグラー' in unicodedata.normalize('NFKC', name or ''))
        result['columns'] = [row['name'] for row in db.execute('PRAGMA table_info(scraped_observations)')]
        rows = [dict(row) for row in db.execute('''SELECT model,COUNT(*) rows,MIN(day) first,MAX(day) last,
            SUM(bb IS NOT NULL AND rb IS NOT NULL) with_bonus,SUM(net IS NOT NULL) with_net
            FROM scraped_observations GROUP BY model ORDER BY rows DESC''')
            if 'ジャグラー' in unicodedata.normalize('NFKC', row['model'])]
        result['database'] = rows
        result['databaseSummary'] = dict(db.execute('''SELECT COUNT(*) rows,COUNT(DISTINCT day) days,
            MIN(day) first,MAX(day) last,SUM(bb IS NOT NULL AND rb IS NOT NULL) with_bonus,
            SUM(bb IS NULL OR rb IS NULL) missing_bonus,SUM(bb=0 AND rb=0) real_zero_bonus
            FROM scraped_observations WHERE is_juggler(model)''').fetchone())
        result['bonusDates'] = [dict(row) for row in db.execute('''SELECT day,COUNT(*) rows,
            SUM(bb IS NOT NULL AND rb IS NOT NULL) with_bonus,MIN(bonus_source_url) bonus_source_url
            FROM scraped_observations WHERE is_juggler(model) GROUP BY day
            HAVING SUM(bb IS NOT NULL AND rb IS NOT NULL)>0 ORDER BY day''')]
        result['bonusCollectionStatus'] = [dict(row) for row in db.execute('SELECT * FROM scrape_bonus_days ORDER BY day')]
        result['manualObservationSummary'] = dict(db.execute('''SELECT COUNT(*) rows,
            COUNT(DISTINCT day) days,MIN(day) first,MAX(day) last FROM observations WHERE is_juggler(model)''').fetchone())
        result['samples'] = [dict(row) for row in db.execute('''SELECT day,seat,model,games,bb,rb,combined,net,
            source_url,bonus_source_url FROM scraped_observations WHERE is_juggler(model)
            AND day IN ('2024-03-01','2025-03-17','2026-09-30') ORDER BY day,CAST(seat AS INTEGER)''')][:12]
    counts = defaultdict(lambda: {'rows': 0, 'with_bonus': 0, 'with_net': 0, 'first': None, 'last': None})
    with (folder / 'exports/jokers-eye-base-data-2024-03-01_2026-09-30.csv').open(encoding='utf-8-sig', newline='') as file:
        for row in csv.DictReader(file):
            if row['ジャグラーかジャグラーじゃないか'] != 'ジャグラー':
                continue
            item = counts[row['機種']]
            item['rows'] += 1
            item['with_bonus'] += bool(row['BB数'] and row['RB数'])
            item['with_net'] += bool(row['差枚'])
            item['first'] = min(item['first'] or row['日付'], row['日付'])
            item['last'] = max(item['last'] or row['日付'], row['日付'])
    result['baseCsv'] = dict(counts)
    exports = []
    for path in sorted((folder / 'exports').glob('*.csv')):
        if not path.name.startswith(('gotham-city-', 'jokers-eye-base-data-')):
            continue
        total = complete = 0
        dates = set()
        with path.open(encoding='utf-8-sig', newline='') as file:
            reader = csv.DictReader(file)
            english = 'model' in (reader.fieldnames or [])
            model_key, bb_key, rb_key, day_key = ('model', 'bb', 'rb', 'date') if english else ('機種','BB数','RB数','日付')
            for row in reader:
                if 'ジャグラー' not in unicodedata.normalize('NFKC', row.get(model_key) or ''):
                    continue
                total += 1
                complete += bool((row.get(bb_key) or '').strip() and (row.get(rb_key) or '').strip())
                dates.add(row.get(day_key))
        exports.append({'file': path.name, 'jugglerRows': total, 'with_bonus': complete, 'days': len(dates)})
    result['allExports'] = exports
    return result


if __name__ == '__main__':
    import os
    print(json.dumps(audit(Path(os.environ['LOCALAPPDATA']) / 'JokersEye'), ensure_ascii=False))
