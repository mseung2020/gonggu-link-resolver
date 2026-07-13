#!/usr/bin/env python3
"""
공구왕 - sample_100.json 을 Dify 워크플로우(공구왕 디스크립션 파싱 테스트)에
한 건씩 던져 파싱 결과를 모은다.

워크플로우 입력 변수: input (json_object) → 포스트 객체 하나
워크플로우 출력 변수: result (LLM이 뱉은 JSON 문자열)

사용법:
    export DIFY_URL="https://api.dify.ai/v1"     # 자체호스팅이면 그 주소/v1
    export DIFY_KEY="app-xxxxxxxx"               # 이 앱의 API Key
    python3 run_dify_batch.py                     # 기본 sample_100.json 전체
    python3 run_dify_batch.py 10                  # 앞 10건만 (빠른 확인용)

결과: results_100.json  (post_id, 입력요약, 파싱 JSON, raw 텍스트, 오류)
"""

import json
import os
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent

DIFY_URL = os.environ.get("DIFY_URL", "https://api.dify.ai/v1").rstrip("/")
DIFY_KEY = os.environ.get("DIFY_KEY", "")
IN_FILE = os.environ.get("IN_FILE", str(ROOT / "data/samples/sample_100_enriched.json"))   # 포스트 + creator_description 병합본
OUT_FILE = os.environ.get("OUT_FILE", str(ROOT / "data/results/results_100.json"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "4"))   # 동시 호출 수 (rate limit 나면 낮추기)
MAX_RETRY = 3

HEADERS = {"Authorization": f"Bearer {DIFY_KEY}", "Content-Type": "application/json"}


def run_one(post: dict) -> dict:
    """포스트 1건을 워크플로우에 실행."""
    payload = {
        "inputs": {"input": post},
        "response_mode": "blocking",
        "user": "gonggu-eval",
    }
    pid = post.get("post_id") or post.get("video_id")   # IG=post_id / YT=video_id
    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = requests.post(
                f"{DIFY_URL}/workflows/run",
                headers=HEADERS,
                data=json.dumps(payload),
                timeout=120,
            )
            if r.status_code == 429:      # rate limit → 백오프
                time.sleep(2 * attempt)
                continue
            r.raise_for_status()
            data = r.json()
            raw = (data.get("data", {}).get("outputs", {}) or {}).get("result", "")
            parsed, perr = None, None
            try:
                parsed = json.loads(raw)
            except Exception as e:
                # 모델이 코드블록/설명 섞어 뱉은 경우 { } 구간만 재시도 추출
                try:
                    s, e2 = raw.find("{"), raw.rfind("}")
                    if s != -1 and e2 != -1:
                        parsed = json.loads(raw[s:e2 + 1])
                    else:
                        perr = f"JSON 파싱 실패: {e}"
                except Exception as e3:
                    perr = f"JSON 파싱 실패: {e3}"
            return {
                "post_id": pid,
                "publish_date": post.get("publish_date"),
                "desc_preview": (post.get("description") or "")[:80].replace("\n", " "),
                "parsed": parsed,
                "parse_error": perr,
                "raw": raw,
                "error": None,
            }
        except Exception as e:
            last_err = str(e)
            time.sleep(1.5 * attempt)
    return {
        "post_id": pid,
        "publish_date": post.get("publish_date"),
        "desc_preview": (post.get("description") or "")[:80].replace("\n", " "),
        "parsed": None,
        "parse_error": None,
        "raw": None,
        "error": last_err,
    }


def main():
    if not DIFY_KEY:
        print("DIFY_KEY 환경변수가 없어. export DIFY_KEY=app-xxxx 먼저.", file=sys.stderr)
        sys.exit(1)

    posts = json.load(open(IN_FILE, encoding="utf-8"))

    # 체크포인트: 기존 결과 이어받기 (batch/resume)
    prior = []
    if os.path.exists(OUT_FILE):
        try:
            prior = json.load(open(OUT_FILE, encoding="utf-8"))
        except Exception:
            prior = []
    done_ids = {r.get("post_id") for r in prior}

    def _pid(p):
        return p.get("post_id") or p.get("video_id")

    todo = [p for p in posts if _pid(p) not in done_ids]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(todo)
    todo = todo[:limit]
    print(f"전체 {len(posts)} | 완료 {len(prior)} | 이번 실행 {len(todo)}건 (동시 {CONCURRENCY})")
    if not todo:
        print("남은 대상 없음. 끝.")
        return

    new = [None] * len(todo)
    done = 0

    def _checkpoint():
        merged = prior + [x for x in new if x is not None]
        json.dump(merged, open(OUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(run_one, p): i for i, p in enumerate(todo)}
        for fut in as_completed(futs):
            i = futs[fut]
            new[i] = fut.result()
            done += 1
            tag = "OK" if new[i]["parsed"] else ("ERR" if new[i]["error"] else "BADJSON")
            print(f"  [{done}/{len(todo)}] {new[i]['post_id']} {tag}", flush=True)
            if done % 10 == 0:            # 증분 저장 → 진행률 폴링 가능
                _checkpoint()

    _checkpoint()
    merged = prior + new
    ok = sum(1 for r in merged if r and r["parsed"])
    print(f"\n누적 {len(merged)}/{len(posts)} | 파싱성공 {ok} → {OUT_FILE}", flush=True)


if __name__ == "__main__":
    main()
