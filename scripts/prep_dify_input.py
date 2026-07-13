#!/usr/bin/env python3
"""링크방식 분류 Dify 워크플로우가 기대하는 필드명(description, publish_date, creator_description)에
맞춰 샘플 파일을 정규화한다.
YT는 title+video_description을 description으로 합치고(기존 관례와 동일), publishDate를 publish_date로 매핑.
IG는 build_creator_bio_ig.py를 이미 돌렸으면 그쪽 출력을 쓰는 게 맞음 — 여기 prep_ig()는 bio 조인 없이
급하게 돌릴 때만 쓰는 fallback (creator_description 빈 문자열).

배치별로 파일명이 다르면 env var로 지정:
    IG_SAMPLE=case_sample_ig_0713_100.json YT_SAMPLE=case_sample_yt_0713_100.json python3 scripts/prep_dify_input.py

결과: {SAMPLE 이름}_enriched.json
"""
import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ROOT / 'data/samples'

IG_SAMPLE = os.environ.get('IG_SAMPLE', 'case_sample_ig_100.json')
YT_SAMPLE = os.environ.get('YT_SAMPLE', 'case_sample_yt_100.json')


def prep_ig():
    items = json.load(open(SAMPLES / IG_SAMPLE, encoding='utf-8'))
    for it in items:
        it.setdefault('creator_description', '')
    out = IG_SAMPLE.replace('.json', '_enriched.json')
    json.dump(items, open(SAMPLES / out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'IG {len(items)}건 -> {out} (bio 조인 없는 fallback)')


def prep_yt():
    items = json.load(open(SAMPLES / YT_SAMPLE, encoding='utf-8'))
    for it in items:
        it['description'] = f"[제목] {it.get('title', '')}\n\n{it.get('video_description', '')}"
        it['publish_date'] = it.get('publishDate')
        it.setdefault('creator_description', '')
    out = YT_SAMPLE.replace('.json', '_enriched.json')
    json.dump(items, open(SAMPLES / out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'YT {len(items)}건 -> {out}')


if __name__ == '__main__':
    import sys
    if '--ig' in sys.argv or len(sys.argv) == 1:
        prep_ig()
    if '--yt' in sys.argv or len(sys.argv) == 1:
        prep_yt()
