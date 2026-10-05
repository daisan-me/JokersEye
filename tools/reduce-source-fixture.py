"""Reduce an ordinary public DOM capture to current-table regression evidence."""
import argparse
import json
from pathlib import Path


def reduce_pages(pages):
    result = []
    for page in pages:
        tables = []
        for table in page['tables']:
            header = table[0]['cells'] if table else []
            if ('G数' in header and ('台番' in header or '機種' in header)) or header[:2] == ['BB', 'RB']:
                tables.append(table)
        if not tables:
            # Keep only actual report links, not unrelated rankings/other shops.
            tables = [[row for table in page['tables'] for row in table
                       if any(link['url'].startswith(page['url']) for link in row.get('links', []))]]
        result.append({key: value for key, value in page.items()
                       if key in ('url', 'model', 'singletonSeat', 'heading', 'published', 'links')})
        result[-1]['tables'] = tables
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pages = reduce_pages(json.loads(args.source.read_text(encoding='utf-8')))
    args.output.write_text(json.dumps(pages, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8', newline='\n')
    print(f'{len(pages)} pages / {args.output.stat().st_size} bytes')
