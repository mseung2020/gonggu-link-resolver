#!/usr/bin/env python3
"""링크방식 분류 Dify 워크플로우가 기대하는 필드명(description, publish_date, creator_description)에
맞춰 case_sample_ig_100.json / case_sample_yt_100.json을 정규화한다.
YT는 title+video_description을 description으로 합치고(기존 관례와 동일), publishDate를 publish_date로 매핑.
이번 라운드는 크리에이터 bio를 join하지 않아서 creator_description은 빈 문자열로 둔다.

결과: data/samples/case_sample_ig_100_enriched.json, data/samples/case_sample_yt_100_enriched.json
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ROOT / 'data/samples'


def prep_ig():
    items = json.load(open(SAMPLES / 'case_sample_ig_100.json', encoding='utf-8'))
    for it in items:
        it.setdefault('creator_description', '')
    json.dump(items, open(SAMPLES / 'case_sample_ig_100_enriched.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    print(f'IG {len(items)}건 -> case_sample_ig_100_enriched.json')


def prep_yt():
    items = json.load(open(SAMPLES / 'case_sample_yt_100.json', encoding='utf-8'))
    for it in items:
        it['description'] = f"[제목] {it.get('title', '')}\n\n{it.get('video_description', '')}"
        it['publish_date'] = it.get('publishDate')
        it.setdefault('creator_description', '')
    json.dump(items, open(SAMPLES / 'case_sample_yt_100_enriched.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=2)
    print(f'YT {len(items)}건 -> case_sample_yt_100_enriched.json')


prep_ig()
prep_yt()
