#!/usr/bin/env python3
"""최근 1주일 공구/공동구매 키워드 매칭 원본에서 IG/YT 각 100개씩 무작위 샘플링해
케이스 테스트용 샘플 파일을 만든다. 배치(주차)별로 파일을 따로 만들어 계속 누적할 수 있게
BATCH 태그로 파일명을 구분하고, 이전 배치에서 이미 뽑은 id는 제외한다.

이번 샘플은 아직 is_gonggu/링크방식 분류 전(원본 그대로) 상태 — 다음 단계에서 분류를 태워야 함.

사용법:
    BATCH=0713 IG_RAW=insta_7days_0713.json YT_RAW=youtube_7days_0713.json python3 scripts/sample_recent_100.py

결과: data/samples/case_sample_ig_{BATCH}_100.json, data/samples/case_sample_yt_{BATCH}_100.json
"""
import glob
import json
import os
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / 'data/raw'
SAMPLES = ROOT / 'data/samples'
SEED = 42
N = 100

BATCH = os.environ.get('BATCH', '')
IG_RAW = os.environ.get('IG_RAW', 'insta_7days.json')
YT_RAW = os.environ.get('YT_RAW', 'youtube_7days.json')


def load_json(fname):
    return json.load(open(RAW / fname, encoding='utf-8'))


def already_sampled_ids(platform):
    ids = set()
    for f in glob.glob(str(SAMPLES / f'case_sample_{platform}_*.json')):
        for r in json.load(open(f, encoding='utf-8')):
            ids.add(r['id'])
    return ids


def sample(rows, id_key, platform, tag, out_file):
    seen = already_sampled_ids(tag)
    fresh = [r for r in rows if r[id_key] not in seen]
    picked = random.Random(SEED).sample(fresh, min(N, len(fresh)))
    merged = []
    for r in picked:
        merged.append({
            'id': r[id_key],
            'platform': platform,
            **r,
        })
    json.dump(merged, open(SAMPLES / out_file, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'{platform}: 원본 {len(rows)}건, 기존 배치와 중복 제외 후 {len(fresh)}건 중 {len(merged)}개 샘플 -> {out_file}')
    return merged


suffix = f'_{BATCH}' if BATCH else ''
ig_rows = load_json(IG_RAW)
yt_rows = load_json(YT_RAW)

sample(ig_rows, 'post_id', 'instagram', 'ig', f'case_sample_ig{suffix}_100.json')
sample(yt_rows, 'video_id', 'youtube', 'yt', f'case_sample_yt{suffix}_100.json')
