#!/usr/bin/env python3
"""케이스 테스트 대시보드가 읽는 실시간 결과 저장소.

크롤/LLM 모듈이 홉을 진행할 때마다 update()를 호출해 상태를 기록하면,
dashboard_server.py가 이 파일을 폴링해서 화면에 보여준다.
동시 쓰기 충돌을 피하려고 매 update마다 파일 전체를 읽고 고쳐서 원자적으로 덮어쓴다.
"""
import json
import pathlib
import threading
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
STORE_FILE = ROOT / 'data/results/case_test_run.json'
_lock = threading.Lock()


def init_from_samples():
    records = []
    for platform, fname, id_key in [
        ('instagram', 'case_sample_ig_100.json', 'id'),
        ('youtube', 'case_sample_yt_100.json', 'id'),
    ]:
        items = json.load(open(ROOT / 'data/samples' / fname, encoding='utf-8'))
        for it in items:
            lc = it.get('link_classification', {})
            original_url = it.get('url') or f"https://www.youtube.com/watch?v={it['id']}"
            records.append({
                'id': it[id_key],
                'platform': platform,
                'original_url': original_url,
                'desc_preview': (it.get('description') or it.get('title') or '')[:100].replace('\n', ' '),
                'is_gonggu': lc.get('is_gonggu'),
                'link_location': lc.get('link_location'),
                'url_type': lc.get('url_type'),
                'comment_gated': lc.get('comment_gated'),
                'product_hint': lc.get('product_hint'),
                'period_start': lc.get('period_start'),
                'period_end': lc.get('period_end'),
                'status': 'pending',
                'hops': [],
                'final': {},
                'error': None,
                'updated_at': None,
            })
    _write(records)
    return records


def load():
    if not STORE_FILE.exists():
        return init_from_samples()
    return json.load(open(STORE_FILE, encoding='utf-8'))


def _write(records):
    tmp = STORE_FILE.with_suffix('.tmp')
    json.dump(records, open(tmp, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    tmp.replace(STORE_FILE)


def update(item_id, status=None, hop=None, final=None, error=None):
    """item_id 하나의 상태를 갱신. hop이 있으면 hops 리스트에 append.
    final이 있으면 final dict에 merge."""
    with _lock:
        records = load()
        for r in records:
            if r['id'] != item_id:
                continue
            if status is not None:
                r['status'] = status
            if hop is not None:
                r['hops'].append(hop)
            if final is not None:
                r['final'].update(final)
            if error is not None:
                r['error'] = error
            r['updated_at'] = datetime.now().isoformat(timespec='seconds')
            break
        _write(records)


if __name__ == '__main__':
    recs = init_from_samples()
    print(f'초기화: {len(recs)}건 → {STORE_FILE}')
