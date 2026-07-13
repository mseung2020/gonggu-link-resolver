#!/usr/bin/env python3
"""이미 진행 중인 case_test_run.json을 초기화(리셋)하지 않고 product_hint/period_start/period_end만
샘플 파일에서 가져와 채워넣는다 (진행 상태/hops/final은 그대로 보존)."""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / 'data/results/case_test_run.json'
SAMPLES = ROOT / 'data/samples'

lc_by_id = {}
for fname in ('case_sample_ig_100.json', 'case_sample_yt_100.json'):
    for it in json.load(open(SAMPLES / fname, encoding='utf-8')):
        lc_by_id[it['id']] = it.get('link_classification') or {}

records = json.load(open(STORE_FILE, encoding='utf-8'))
n = 0
for r in records:
    lc = lc_by_id.get(r['id'], {})
    r['product_hint'] = lc.get('product_hint')
    r['period_start'] = lc.get('period_start')
    r['period_end'] = lc.get('period_end')
    r['comment_gated'] = lc.get('comment_gated')
    r['is_gonggu'] = lc.get('is_gonggu')
    n += 1

tmp = STORE_FILE.with_suffix('.tmp')
json.dump(records, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
tmp.replace(STORE_FILE)
print(f'{n}건에 product_hint/period_start/period_end 채움 (상태/hops/final 보존)')
