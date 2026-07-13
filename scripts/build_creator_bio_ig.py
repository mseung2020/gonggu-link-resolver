#!/usr/bin/env python3
"""IG 샘플 100건의 creator_description을 채운다.
- bio 텍스트: data/raw/user_description.csv에서 user_id로 매칭 (로컬에 이미 있음)
- 프로필 웹사이트 링크: data/samples/{EXT_URL_FILE} (user_id별 최신 external_url, created_at 최대값)
두 개를 합쳐 Dify 워크플로우의 creator_description 입력으로 쓴다.

배치별로 입력/출력/external_url 파일명이 다르면 env var로 지정:
    IG_SAMPLE=case_sample_ig_0713_100.json EXT_URL_FILE=ext_url_ig_0713.json python3 scripts/build_creator_bio_ig.py

결과: data/samples/{IG_SAMPLE 이름에 _enriched 붙인 파일} (creator_description 갱신)
"""
import csv
import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ROOT / 'data/samples'
RAW = ROOT / 'data/raw'

IG_SAMPLE = os.environ.get('IG_SAMPLE', 'case_sample_ig_100.json')
EXT_URL_FILE = os.environ.get('EXT_URL_FILE', 'ext_url_ig_recent96.json')
OUT_FILE = IG_SAMPLE.replace('.json', '_enriched.json')


def latest_external_url():
    rows = json.load(open(SAMPLES / EXT_URL_FILE, encoding='utf-8'))
    latest = {}
    for r in rows:
        uid = r['user_id']
        if uid not in latest or r['created_at'] > latest[uid]['created_at']:
            latest[uid] = r
    return {uid: r['external_url'] for uid, r in latest.items()}


def bio_text(target_ids):
    found = {}
    with open(RAW / 'user_description.csv', encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            uid = row.get('user_id')
            if uid in target_ids and row.get('description', '').strip():
                found[uid] = row['description']
    return found


ig = json.load(open(SAMPLES / IG_SAMPLE, encoding='utf-8'))
target_ids = {r['user_id'] for r in ig}

ext_urls = latest_external_url()
bios = bio_text(target_ids)

filled = 0
for r in ig:
    uid = r['user_id']
    parts = []
    if uid in bios:
        parts.append(bios[uid])
    if uid in ext_urls:
        parts.append(f"[프로필 웹사이트 링크] {ext_urls[uid]}")
    r['creator_description'] = '\n\n'.join(parts)
    if parts:
        filled += 1

json.dump(ig, open(SAMPLES / OUT_FILE, 'w', encoding='utf-8'),
          ensure_ascii=False, indent=2)
print(f'{filled}/{len(ig)}건 creator_description 채움 (bio {len(bios)}명, external_url {len(ext_urls)}명) -> {OUT_FILE}')
