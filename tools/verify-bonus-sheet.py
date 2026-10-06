"""Read-only audit of an actual day's BB/RB, base values and 11-column CSV."""
import argparse
import csv
import json
from pathlib import Path
import sqlite3
import unicodedata

HEADERS = ['日付','曜日','台番号','機種','ジャグラーかジャグラーじゃないか','ゲーム数','BB数','RB数','合成','差枚','出率']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--day', required=True)
    parser.add_argument('--sheet', type=Path)
    args = parser.parse_args()
    database = args.data / 'jokers-eye.sqlite3'
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute('SELECT * FROM scraped_observations WHERE day=? ORDER BY CAST(seat AS INTEGER)', (args.day,))]
    sheet = args.sheet or args.data / 'exports' / 'jokers-eye-base-data-2024-03-01_2026-09-30.csv'
    previous = None
    count = 0
    selected = []
    with sheet.open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != HEADERS:raise ValueError('CSV columns differ from the base-sheet schema')
        for row in reader:
            key = (row['日付'], int(row['台番号']))
            if previous is not None and key <= previous:raise ValueError('CSV duplicate or unsorted row')
            previous = key
            count += 1
            if row['日付'] == args.day:selected.append(row)
    if len(selected) != len(rows):raise ValueError('Day row count differs between CSV and SQLite')
    for record, saved in zip(rows, selected):
        for source, target in [('seat','台番号'),('model','機種'),('games','ゲーム数'),('bb','BB数'),('rb','RB数'),('combined','合成'),('net','差枚')]:
            expected = '' if record[source] is None else str(record[source])
            if saved[target] != expected:raise ValueError('CSV/SQLite mismatch at seat ' + record['seat'] + ': ' + target)
        payout = '' if record['payout_percent'] is None else f"{record['payout_percent']:.1f}".rstrip('0').rstrip('.') + '%'
        if saved['出率'] != payout:raise ValueError('Payout mismatch')
    juggler = [row for row in rows if 'ジャグラー' in unicodedata.normalize('NFKC',row['model'])]
    result = {'day':args.day,'databaseRows':len(rows),'csvRows':len(selected),'csvTotalRows':count,
              'bonusComplete':sum(row['bb'] is not None and row['rb'] is not None for row in rows),
              'jugglerRows':len(juggler),'jugglerBonusComplete':sum(row['bb'] is not None and row['rb'] is not None for row in juggler),
              'missingCombined':sum(row['combined'] is None for row in rows),'missingPayout':sum(row['payout_percent'] is None for row in rows),
              'sortedUnique':True,'csvMatchesDatabase':True}
    print(json.dumps(result,ensure_ascii=False))
    if result['bonusComplete'] != len(rows) or not rows:raise SystemExit(1)


if __name__ == '__main__':main()
