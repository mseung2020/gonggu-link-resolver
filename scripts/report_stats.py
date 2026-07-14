#!/usr/bin/env python3
"""case_test_run.json 누적 통계 리포트."""
import json
import pathlib
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
d = json.load(open(ROOT / 'data/results/case_test_run.json', encoding='utf-8'))

def is_noise(r):
    """대시보드 4종 숨기기(예고·마감/링크없음/공구아님/제휴광고성)에 해당 — 원천적으로 시도 대상이 아니었던 것."""
    e = r.get('error') or ''
    if e.startswith('stage=예고') or e.startswith('stage=마감'):
        return True
    if e.startswith('크롤링할 링크 없음') or e.startswith('urls/profile_urls 없음'):
        return True
    if r.get('is_gonggu') is False or e.startswith('is_gonggu=false'):
        return True
    if e.startswith('제휴 광고성'):
        return True
    return False


active = [r for r in d if r['status'] != 'pending']
print(f'전체 {len(d)}건 중 처리 {len(active)}건 (남음 {len(d) - len(active)}건)')
print(Counter(r['status'] for r in active).most_common())
print()

unresolved = [r for r in active if r['status'] == 'unresolved']
noise = [r for r in unresolved if is_noise(r)]
real_fail = [r for r in unresolved if not is_noise(r)]
print(f'unresolved {len(unresolved)}건 중: 노이즈(4종 숨기기) {len(noise)} / 진짜 시도했다가 실패 {len(real_fail)}')
print()

reasons = Counter()
for r in real_fail + [r for r in active if r['status'] == 'error']:
    err = (r.get('error') or '')
    key = err.split('(')[0].split('—')[0].strip()[:30]
    reasons[key] += 1
print('진짜 실패 사유:')
for k, v in reasons.most_common():
    print(f'  {v:3d}  {k}')

print()
print('=== done 목록 ===')
for r in active:
    if r['status'] == 'done':
        print(' ', r['platform'], r['id'], '|', r['final'].get('name'), '|', r['final'].get('price'))
