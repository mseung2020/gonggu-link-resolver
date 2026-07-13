#!/usr/bin/env python3
"""이미 진행 중인 case_test_run.json을 초기화(리셋)하지 않고 분류 결과 필드(link_location/url_type/
is_gonggu/comment_gated/product_hint/period_start/period_end)만 샘플 파일에서 다시 가져와 채워넣는다
(진행 상태/hops/final은 그대로 보존). 새 배치를 sync_from_samples()로 추가한 뒤 분류를 나중에
병합했을 때, 이미 store에 들어간 레코드의 분류 필드를 갱신하려고 쓴다."""
import glob
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / 'data/results/case_test_run.json'
SAMPLES = ROOT / 'data/samples'

lc_by_id = {}
for tag in ('ig', 'yt'):
    for f in sorted(glob.glob(str(SAMPLES / f'case_sample_{tag}_*.json'))):
        for it in json.load(open(f, encoding='utf-8')):
            if it['id'] not in lc_by_id:  # 먼저 나온 배치 우선(이미 값 있으면 덮어쓰지 않음)
                lc_by_id[it['id']] = it.get('link_classification') or {}

records = json.load(open(STORE_FILE, encoding='utf-8'))
n = 0
for r in records:
    lc = lc_by_id.get(r['id'], {})
    r['link_location'] = lc.get('link_location')
    r['url_type'] = lc.get('url_type')
    r['product_hint'] = lc.get('product_hint')
    r['period_start'] = lc.get('period_start')
    r['period_end'] = lc.get('period_end')
    r['comment_gated'] = lc.get('comment_gated')
    r['is_gonggu'] = lc.get('is_gonggu')
    n += 1

tmp = STORE_FILE.with_suffix('.tmp')
json.dump(records, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
tmp.replace(STORE_FILE)
print(f'{n}건 분류 필드 갱신 (상태/hops/final 보존)')
