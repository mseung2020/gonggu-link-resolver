#!/usr/bin/env python3
"""링크방식 분류 결과(data/results/link_{ig,yt}_results_recent.json)를
case_sample_{ig,yt}_100.json에 합쳐서 대시보드가 바로 보여줄 수 있게 한다.
"""
import json
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


merge('case_sample_ig_100.json', 'link_ig_results_recent.json')
merge('case_sample_yt_100.json', 'link_yt_results_recent.json')
