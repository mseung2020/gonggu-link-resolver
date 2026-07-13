#!/usr/bin/env python3
"""링크방식 분류 결과를 (bio 조인 없는) 원본 case_sample_{ig,yt}_*.json에 합쳐서
대시보드가 바로 보여줄 수 있게 한다.

배치별로 파일명이 다르면 env var로 지정:
    IG_SAMPLE=case_sample_ig_0713_100.json IG_RESULTS=link_ig_results_0713.json \
    YT_SAMPLE=case_sample_yt_0713_100.json YT_RESULTS=link_yt_results_0713.json \
    python3 scripts/merge_classification.py
"""
import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ROOT / 'data/samples'
RESULTS = ROOT / 'data/results'


def merge(sample_file, results_file):
    samples = json.load(open(SAMPLES / sample_file, encoding='utf-8'))
    results = {r['post_id']: r['parsed'] for r in json.load(open(RESULTS / results_file, encoding='utf-8'))}
    for s in samples:
        s['link_classification'] = results.get(s['id'])
    json.dump(samples, open(SAMPLES / sample_file, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'{sample_file}: {sum(1 for s in samples if s["link_classification"])}/{len(samples)}건 분류 결과 병합')


merge(os.environ.get('IG_SAMPLE', 'case_sample_ig_100.json'),
      os.environ.get('IG_RESULTS', 'link_ig_results_recent.json'))
merge(os.environ.get('YT_SAMPLE', 'case_sample_yt_100.json'),
      os.environ.get('YT_RESULTS', 'link_yt_results_recent.json'))
