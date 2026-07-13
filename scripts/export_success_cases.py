#!/usr/bin/env python3
"""case_test_run.json에서 status=='done'인 성공 케이스만 뽑아 별도 파일로 저장한다.
원본 포스트 내용(캡션/제목)까지 같이 붙여서, 입력→출력이 한 파일에서 다 보이게 한다."""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / 'data/results/case_test_run.json'
SAMPLES = ROOT / 'data/samples'
OUT_FILE = ROOT / 'data/results/success_cases.json'

posts_by_id = {}
for fname in ('case_sample_ig_100.json', 'case_sample_yt_100.json'):
    fp = SAMPLES / fname
    if fp.exists():
        for it in json.load(open(fp, encoding='utf-8')):
            posts_by_id[it['id']] = it

records = json.load(open(STORE_FILE, encoding='utf-8'))
success = [r for r in records if r['status'] == 'done']

out = []
for r in success:
    post = posts_by_id.get(r['id'], {})
    out.append({
        'id': r['id'],
        'platform': r['platform'],
        'original_url': r['original_url'],
        'original_caption': post.get('description') or post.get('video_description') or '',
        'product_hint': r.get('product_hint'),
        'period_start': r.get('period_start'),
        'period_end': r.get('period_end'),
        'link_location': r.get('link_location'),
        'hops': r.get('hops', []),
        'final': r.get('final', {}),
        'resolved_at': r.get('updated_at'),
    })

json.dump(out, open(OUT_FILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print(f'성공 케이스 {len(out)}건 -> {OUT_FILE}')
