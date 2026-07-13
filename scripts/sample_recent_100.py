#!/usr/bin/env python3
"""최근 1주일 공구/공동구매 키워드 매칭 원본(data/raw/insta_7days.json, youtube_7days.json)에서
IG/YT 각 100개씩 무작위 샘플링해 케이스 테스트용 샘플 파일을 다시 만든다.
(CSV export가 쉼표 포함 caption에서 컬럼이 밀리는 문제가 있어 JSON export로 교체함.)

기존 case_sample_ig_100.json / case_sample_yt_100.json은 v3 링크방식 분류가 이미 붙어 있었지만,
이번 샘플은 아직 is_gonggu/링크방식 분류 전(원본 그대로) 상태다 — 다음 단계에서 분류를 다시 태워야 함.

결과: data/samples/case_sample_ig_100.json, data/samples/case_sample_yt_100.json (덮어씀)
"""
import json
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / 'data/raw'
SAMPLES = ROOT / 'data/samples'
SEED = 42
N = 100


def load_json(fname):
    return json.load(open(RAW / fname, encoding='utf-8'))


def sample(rows, id_key, platform, out_file):
    picked = random.Random(SEED).sample(rows, N)
    merged = []
    for r in picked:
        merged.append({
            'id': r[id_key],
            'platform': platform,
            **r,
        })
    json.dump(merged, open(SAMPLES / out_file, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'{platform}: {len(rows)}건 중 {len(merged)}개 샘플 -> {out_file}')
    return merged


ig_rows = load_json('insta_7days.json')
yt_rows = load_json('youtube_7days.json')

sample(ig_rows, 'post_id', 'instagram', 'case_sample_ig_100.json')
sample(yt_rows, 'video_id', 'youtube', 'case_sample_yt_100.json')
