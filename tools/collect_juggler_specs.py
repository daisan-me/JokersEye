"""Vendor the manufacturer's public probability tables; never run an estimator.

Usage: python tools/collect_juggler_specs.py --reference-dir PATH
The optional directory receives payout images for human reference verification.
No application observations, CSVs or database records are read or modified.
"""
import argparse
import html
import json
from pathlib import Path
import re
import urllib.request

LIST_URL = 'https://www.kitadenshi.co.jp/slot/'
VERIFIED_ON = '2026-10-04'
CURRENT = {
    'myjuggler6', 'neoimjugglerex', 'ultramiraclejuggler', 'mrjuggler',
    'jugglergirlsss', 'gogojuggler3', 'happyjugglerv3', 'myjuggler5',
    'funkyjuggler2', 'imjugglerex2020',
}
# Published approximate net bonus awards, not gross bonus payouts.
BONUS_NET = {key: {'bb': 240, 'rb': 96} for key in CURRENT}
BONUS_NET['imjugglerex2020'] = {'bb': 252, 'rb': 96}
BONUS_NET['neoimjugglerex'] = {'bb': 252, 'rb': 96}
BONUS_NET['imjugglerex2020_gr'] = {'bb': 252, 'rb': 96}
CURRENT_PANELS = CURRENT | {'imjugglerex2020_gr'}


def get(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'JokersEye/1.3 public-spec-reference'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def text(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', fragment))).strip()


def collect(reference_dir=None):
    source = get(LIST_URL).decode('utf-8')
    machines = []
    skipped = []
    for block in source.split('<li class="slot-list_item">')[1:]:
        title = re.search(r'<h2[^>]*>(.*?)</h2>', block, re.S)
        if not title or 'ジャグラー' not in text(title[1]):
            continue
        link = re.search(r'href=["\']?(https://www\.kitadenshi\.co\.jp/slot/([^/"\'>]+)/)', block)
        if not link:
            raise ValueError('Manufacturer product link not found')
        name, url, key = text(title[1]), link[1], link[2]
        date = re.search(r'class="slot-link_date">(.*?)</div>', block, re.S)
        table = re.search(r'<table[^>]*>(.*?)</table>', block, re.S)
        if not table:
            raise ValueError('Manufacturer setting table missing for ' + key)
        if 'BB確率' not in text(table[1]) or 'RB確率' not in text(table[1]):
            skipped.append({'name': name, 'sourceUrl': url, 'reason': 'メーカーの一覧にBB・RB別の表が未掲載'})
        rows = []
        headers = None
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>', table[1], re.S):
            cells = [text(cell) for cell in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row, re.S)]
            if cells and cells[0] == '設定':
                headers = cells
            if not cells or not re.fullmatch('[1-6]', cells[0]):
                continue
            if not headers or len(cells) != len(headers):
                raise ValueError('Unexpected columns for ' + key)
            values = dict(zip(headers, cells))
            rows.append({
                'setting': int(cells[0]),
                'bbDenominator': float(values['BB確率'].removeprefix('1/')) if 'BB確率' in values else None,
                'rbDenominator': float(values['RB確率'].removeprefix('1/')) if 'RB確率' in values else None,
                'combinedDenominator': float(values['合成確率'].removeprefix('1/')) if '合成確率' in values else None,
                'payoutPercent': float(values['出玉率'].replace('％', '%').rstrip('%')),
            })
        if [row['setting'] for row in rows] != [1, 2, 3, 4, 5, 6]:
            raise ValueError('Incomplete setting table for ' + key)
        machine = {
            'id': key, 'name': name, 'generation': 6 if key in CURRENT_PANELS else 5,
            'introduced': text(date[1]) if date else '',
            'sourceUrl': url, 'verifiedOn': VERIFIED_ON, 'settings': rows,
            'bonusNetCoins': BONUS_NET.get(key),
            'grapePayoutCoins': 8 if key in CURRENT_PANELS else None,
        }
        payout_link = re.search(r'href="#modal-haito"\s+data-modal-src="\s*([^"]+?)\s*"', block, re.S)
        if payout_link:
            machine['payoutSourceUrl'] = html.unescape(payout_link[1])
        machines.append(machine)
        if reference_dir and key in CURRENT_PANELS:
            if not machine.get('payoutSourceUrl'):
                raise ValueError('Payout reference not found for ' + key)
            Path(reference_dir).mkdir(parents=True, exist_ok=True)
            Path(reference_dir, key + '.png').write_bytes(get(machine['payoutSourceUrl']))
    if not CURRENT.issubset({machine['id'] for machine in machines}):
        raise ValueError('A current series is missing from the manufacturer listing')
    return {'schemaVersion': 1, 'verifiedOn': VERIFIED_ON, 'sourceUrl': LIST_URL,
            'machines': machines, 'unpublishedSeparateBonusTables': skipped}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-dir', type=Path)
    args = parser.parse_args()
    result = collect(args.reference_dir)
    # This is generated reference data, not a rewritten application file.
    target = Path(__file__).resolve().parents[1] / 'web' / 'juggler-specs.json'
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'models': len(result['machines']), 'modelsWithUnpublishedBonusTables': result['unpublishedSeparateBonusTables'],
                      'catalog': [{'id': item['id'], 'name': item['name'], 'introduced': item['introduced'],
                                   'payoutSourceUrl': item.get('payoutSourceUrl')} for item in result['machines']]}, ensure_ascii=False))
