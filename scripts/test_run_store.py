#!/usr/bin/env python3
"""케이스 테스트 대시보드가 읽는 실시간 결과 저장소.

크롤/LLM 모듈이 홉을 진행할 때마다 update()를 호출해 상태를 기록하면,
dashboard_server.py가 이 파일을 폴링해서 화면에 보여준다.
동시 쓰기 충돌을 피하려고 매 update마다 파일 전체를 읽고 고쳐서 원자적으로 덮어쓴다.

여러 주차 배치(case_sample_ig_0709_100.json, case_sample_ig_0713_100.json, ...)를 계속 누적하기 위해
샘플 파일은 case_sample_{ig,yt}_*.json 패턴으로 전부 glob해서 모은다.
"""
import glob
import json
import pathlib
import threading
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ROOT / 'data/samples'
STORE_FILE = ROOT / 'data/results/case_test_run.json'
_lock = threading.Lock()


def _record(it, platform, id_key='id'):
    lc = it.get('link_classification') or {}
    original_url = it.get('url') or f"https://www.youtube.com/watch?v={it[id_key]}"
    return {
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
    }


def _all_sample_items():
    """(platform, item) 전체 — case_sample_{ig,yt}_*.json 전 배치 glob, id 중복이면 먼저 나온 파일 우선."""
    out, seen = [], set()
    for platform, tag in (('instagram', 'ig'), ('youtube', 'yt')):
        for f in sorted(glob.glob(str(SAMPLES / f'case_sample_{tag}_*.json'))):
            for it in json.load(open(f, encoding='utf-8')):
                if it['id'] in seen:
                    continue
                seen.add(it['id'])
                out.append((platform, it))
    return out


def init_from_samples():
    """전체 리셋 — 지금 존재하는 모든 배치 파일 기준으로 처음부터 다시 만든다(기존 진행상태 사라짐)."""
    records = [_record(it, platform) for platform, it in _all_sample_items()]
    _write(records)
    return records


def sync_from_samples():
    """이미 store에 있는 id는 그대로 두고, 새로 추가된 배치 파일의 새 id만 pending으로 덧붙인다."""
    existing = json.load(open(STORE_FILE, encoding='utf-8')) if STORE_FILE.exists() else []
    existing_ids = {r['id'] for r in existing}
    added = 0
    for platform, it in _all_sample_items():
        if it['id'] in existing_ids:
            continue
        existing.append(_record(it, platform))
        existing_ids.add(it['id'])
        added += 1
    _write(existing)
    return added, len(existing)


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
                if error is None:  # 상태가 바뀌는데 새 에러가 없으면 이전 에러 메시지는 지운다(재시도 성공 시 잔존 방지)
                    r['error'] = None
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
    import sys
    if '--reset' in sys.argv:
        recs = init_from_samples()
        print(f'전체 리셋: {len(recs)}건 → {STORE_FILE}')
    else:
        added, total = sync_from_samples()
        print(f'새 배치 {added}건 추가 (누적 {total}건) → {STORE_FILE}')
