"""Read-only audit of existing inputs; never scrape or estimate settings."""
import json
from pathlib import Path
import sqlite3
import unicodedata


def audit(folder):
    result = {}
    database = folder / 'GothamDataBase.sqlite'
    if not database.exists():  # not yet renamed by a newer app
        database = folder / 'jokers-eye.sqlite3'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
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
        result['samples'] = [dict(row) for row in db.execute('''SELECT day,seat,model,games,bb,rb,combined,net,
            source_url,bonus_source_url FROM scraped_observations WHERE is_juggler(model)
            AND day IN ('2024-03-01','2025-03-17','2026-09-30') ORDER BY day,CAST(seat AS INTEGER)''')][:12]
    return result


if __name__ == '__main__':
    import os
    print(json.dumps(audit(Path(os.environ['LOCALAPPDATA']) / 'JokersEye'), ensure_ascii=False))
