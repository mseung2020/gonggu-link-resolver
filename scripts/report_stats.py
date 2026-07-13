#!/usr/bin/env python3
"""case_test_run.json 누적 통계 리포트."""
import json
import pathlib
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
d = json.load(open(ROOT / 'data/results/case_test_run.json', encoding='utf-8'))

active = [r for r in d if r['status'] != 'pending']
print(f'전체 {len(d)}건 중 처리 {len(active)}건 (남음 {len(d) - len(active)}건)')
print(Counter(r['status'] for r in active).most_common())
print()

reasons = Counter()
for r in active:
    if r['status'] in ('unresolved', 'error'):
        err = (r.get('error') or '')
        key = err.split('(')[0].split('—')[0].strip()[:30]
        reasons[key] += 1
for k, v in reasons.most_common():
    print(f'  {v:3d}  {k}')

print()
print('=== done 목록 ===')
for r in active:
    if r['status'] == 'done':
        print(' ', r['platform'], r['id'], '|', r['final'].get('name'), '|', r['final'].get('price'))
