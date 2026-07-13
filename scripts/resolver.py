#!/usr/bin/env python3
"""링크 해석 모듈 — LLM 홉 구조 (2026-07-09 설계).

파이프라인:
  LLM#1(링크방식분류, 사전 실행됨) 결과의 gonggu_stage/comment_gated로 게이트 체크
    -> 첫 URL(직접링크 또는 프로필링크) 크롤 (스크립트)
    -> LLM#3(페이지판별): 최종 상품페이지인지, 원본 포스트 상품과 일치하는지 판별
       - 링크모음이면: 후보 링크 추출(스크립트) -> LLM#2(링크선택) -> 크롤 -> LLM#3 재판별 (최대 3홉)
       - 상품페이지+일치 -> 완료
       - 그 외(스토어메인/로그인월_차단/무관/불일치) -> 미해결

Dify API 키 3개 필요 (env var):
  DIFY_KEY_CLASSIFY (참고용, 이 스크립트에서는 호출 안 함 — 분류는 run_dify_batch.py로 사전 실행)
  DIFY_KEY_PICK     ("공구왕 링크선택")
  DIFY_KEY_JUDGE    ("공구왕 페이지판별")

사용법:
    python3 scripts/resolver.py yt [N]
    python3 scripts/resolver.py ig [N]
    python3 scripts/resolver.py all [N]
"""
import json
import os
from datetime import date
import pathlib
import re
import sys
import time
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

import test_run_store as store

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ROOT / 'data/samples'
MEDIA_DIR = ROOT / 'data/cache/case_test'
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36')

DIFY_URL = os.environ.get('DIFY_URL', 'https://api.dify.ai/v1').rstrip('/')
DIFY_KEY_PICK = os.environ.get('DIFY_KEY_PICK', '')
DIFY_KEY_JUDGE = os.environ.get('DIFY_KEY_JUDGE', '')

AFFILIATE_MARKERS = ('파트너스', '쇼핑커넥트', '일정액의 수수료', '수수료를 제공받습니다')
BAD_DOMAINS = ('nid.naver.com', 'accounts.kakao.com', 'account.kakao.com', 'mkt.shopping.naver',
               'pf.kakao.com', 'forms.gle', 'docs.google', 'canva.site', 'band.us',
               'instagram.com', 'youtube.com', 'youtu.be')
NON_PRODUCT_TEXT = ('문의', '상담', '블로그', '유튜브', '인스타그램', '후기', '이벤트 참여',
                    '카카오채널', '카카오톡', '채널톡', '공식 홈페이지')
MAX_HOPS = 3
ITEM_DELAY = float(os.environ.get('ITEM_DELAY', '3'))  # 케이스 사이 대기(초) — 안티봇/레이트리밋 완화

ctx_holder = {}


# ---------------- Dify 호출 (판단은 전부 여기로) ----------------

def call_dify(api_key, input_obj, timeout=60):
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    payload = {'inputs': {'input': input_obj}, 'response_mode': 'blocking', 'user': 'gonggu-resolver'}
    r = requests.post(f'{DIFY_URL}/workflows/run', headers=headers, data=json.dumps(payload), timeout=timeout)
    r.raise_for_status()
    data = r.json()
    raw = (data.get('data', {}).get('outputs', {}) or {}).get('result', '')
    try:
        return json.loads(raw)
    except Exception:
        s, e = raw.find('{'), raw.rfind('}')
        if s != -1 and e != -1:
            return json.loads(raw[s:e + 1])
        raise ValueError(f'JSON 파싱 실패: {raw[:200]}')


def pick_link(post_context, candidates):
    """LLM#2 · 공구왕 링크선택"""
    return call_dify(DIFY_KEY_PICK, {'post_context': post_context, 'candidates': candidates})


def judge_page(post_context, page_info):
    """LLM#3 · 공구왕 페이지판별"""
    return call_dify(DIFY_KEY_JUDGE, {'post_context': post_context, 'page': page_info})


# ---------------- 크롤링/파싱 (순수 스크립트, 판단 없음) ----------------

def is_affiliate_ranking(description, urls):
    """쿠팡파트너스/네이버쇼핑커넥트 'TOP N 추천' 리뷰 — 법정 고지문구 매칭이라 규칙으로 유지."""
    return len(urls or []) >= 3 and any(m in (description or '') for m in AFFILIATE_MARKERS)


def meta(page, prop):
    try:
        el = page.query_selector(f'meta[property="{prop}"]') or page.query_selector(f'meta[name="{prop}"]')
        return el.get_attribute('content') if el else None
    except Exception:
        return None


def extract_jsonld(html):
    out = {}
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        if isinstance(data, dict) and '@graph' in data:
            items = data['@graph']
        for it in items:
            if isinstance(it, dict):
                t = it.get('@type', '')
                t = t if isinstance(t, str) else ','.join(t)
                if 'Product' in t:
                    img = it.get('image')
                    offers = it.get('offers') or {}
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    return {'name': it.get('name'), 'image': img[0] if isinstance(img, list) else img,
                            'price': offers.get('price'), 'currency': offers.get('priceCurrency')}
    return out


SLOW_REDIRECT_DOMAINS = ('mkt.shopping.naver.com',)  # 클라이언트 사이드 리다이렉트가 느려서 첫 로드 시점엔 목적지 도착 전


def _extract_once(page):
    title = meta(page, 'og:title') or (page.title() or '').strip()
    html = page.content()
    og_image = meta(page, 'og:image')
    jsonld = extract_jsonld(html)
    try:
        body_text = page.inner_text('body')[:300].replace('\n', ' ')
    except Exception:
        body_text = ''
    return title, og_image, jsonld, body_text


def fetch(page, url, wait_extra=1.5):
    rec = {'status': None, 'final_url': None, 'title': None, 'og_image': None, 'jsonld': {},
           'body_text': '', 'error': None}
    try:
        resp = page.goto(url, wait_until='domcontentloaded', timeout=25000)
        try:
            page.wait_for_load_state('networkidle', timeout=6000)
        except Exception:
            pass
        time.sleep(wait_extra)
        rec['status'] = resp.status if resp else None
        rec['final_url'] = page.url

        # 네이버 마케팅 단축링크류는 클라 사이드 리다이렉트가 늦게 끝나는 경우가 있어 한 번 더 기다려본다
        if host_of(rec['final_url']) in SLOW_REDIRECT_DOMAINS:
            time.sleep(3)
            try:
                page.wait_for_load_state('networkidle', timeout=4000)
            except Exception:
                pass
            rec['final_url'] = page.url

        title, og_image, jsonld, body_text = _extract_once(page)
        # JSON-LD/og:image가 둘 다 비어있으면 SPA가 늦게 하이드레이션됐을 수 있으니 한 번만 재확인
        if not jsonld.get('image') and not og_image:
            time.sleep(2)
            title, og_image, jsonld, body_text = _extract_once(page)

        rec['title'], rec['og_image'], rec['jsonld'], rec['body_text'] = title, og_image, jsonld, body_text
    except Exception as e:
        rec['error'] = str(e)[:160]
    return rec


def download_image(ctx, img_url, item_id):
    try:
        if img_url.startswith('//'):
            img_url = 'https:' + img_url
        r = ctx.request.get(img_url, timeout=20000)
        if r.ok and 'image' in r.headers.get('content-type', ''):
            (MEDIA_DIR / f'{item_id}.jpg').write_bytes(r.body())
            return True
    except Exception:
        pass
    return False


MAX_CANDIDATES = 80  # cafe.naver.com류 커뮤니티 페이지는 게시판 네비게이션까지 다 잡혀서 100건 넘게 나올 수 있음


def extract_collection_links(page):
    try:
        raw = page.eval_on_selector_all(
            'a[href]', "els => els.map(e => ({href: e.href, text: e.innerText.trim()}))")
    except Exception:
        return []
    out, seen = [], set()
    for l in raw:
        href, text = l.get('href', ''), l.get('text', '')
        if not href or href in seen or any(d in href for d in BAD_DOMAINS):
            continue
        seen.add(href)
        out.append({'href': href, 'text': text})
        if len(out) >= MAX_CANDIDATES:
            break
    return out


def host_of(url):
    try:
        return urlparse(url).netloc
    except Exception:
        return ''


def normalize_url(u):
    """캡션 원문에서 그대로 뽑힌 URL이라 콜론 빠짐(https//...)이나 스킴 없음(brand.naver.com/...),
    중복 스킴(https://https//...) 같은 오타가 섞여 있을 수 있어 fetch 전에 보정한다."""
    u = (u or '').strip()
    if not u:
        return u
    u = re.sub(r'(https?)//', r'\1://', u)  # 콜론 빠진 스킴, 문자열 어디든
    matches = list(re.finditer(r'https?://', u))
    if len(matches) > 1:
        u = u[matches[-1].start():]  # 중복 스킴이면 마지막(진짜) 스킴부터 다시
    if not re.match(r'^https?://', u):
        u = 'https://' + u
    return u


def first_usable_url(urls):
    """캡션에 링크가 여러 개면 그중 온전한 것부터 시도 — "..."로 잘린 링크(크리에이터가 원본부터
    잘라서 올린 경우, 우리가 고칠 방법 없음)는 건너뛰고 다음 후보를 본다."""
    for u in urls or []:
        if u and '...' not in u:
            return u
    return (urls or [None])[0]


def post_context_text(item):
    lc = item.get('link_classification') or {}
    hint = lc.get('product_hint')
    raw = item.get('description') or item.get('video_description') or ''
    if hint:
        return f'[상품 요약] {hint}\n\n[캡션 원문(참고)]\n{raw}'
    return raw


def _parse_date(s):
    """YYYY-MM-DD 형식만 신뢰. LLM이 null/이상한 값을 주면 그냥 None."""
    try:
        y, m, d = map(int, (s or '')[:10].split('-'))
        return date(y, m, d)
    except Exception:
        return None


def compute_stage(lc, today=None):
    """gonggu_stage는 LLM이 캡션 문구만 보고 낸 '힌트'일 뿐 — 실제 진행상태는
    period_start/period_end(명시적 날짜)와 진짜 오늘 날짜를 코드가 직접 비교해서 계산한다.
    (build_prototype.py의 compute_status와 동일한 원칙: 오늘 기준 계산값은 LLM이 아니라 코드가 낸다.)
    명시적 날짜가 없으면 LLM의 gonggu_stage 힌트로 폴백."""
    today = today or date.today()
    ps, pe = _parse_date(lc.get('period_start')), _parse_date(lc.get('period_end'))
    if pe:
        if ps and today < ps:
            return '예고'
        return '마감' if today > pe else '진행중'
    if ps and today < ps:
        return '예고'
    return lc.get('gonggu_stage') or '불명'


# ---------------- 오케스트레이션 (게이트 + 홉 루프) ----------------

def gate_check(item):
    lc = item.get('link_classification') or {}
    if not lc.get('is_gonggu'):
        return 'is_gonggu=false (실제 공구 아님)'
    stage = compute_stage(lc)
    if stage in ('예고', '마감'):
        return (f'stage={stage} (오늘 날짜 기준 계산값, LLM 힌트={lc.get("gonggu_stage")}) '
                f'— 아직 안 열림/이미 종료, 지금 링크로 특정 불가')
    urls, profile_urls = lc.get('urls') or [], lc.get('profile_urls') or []
    if not urls and not profile_urls:
        return '크롤링할 링크 없음(urls/profile_urls 둘 다 비어있음)'
    return None


def resolve_item(page, item):
    reason = gate_check(item)
    if reason:
        store.update(item['id'], status='unresolved', error=reason)
        return

    lc = item['link_classification']
    ctx = post_context_text(item)
    urls, profile_urls = lc.get('urls') or [], lc.get('profile_urls') or []

    if lc.get('link_location') == '설명_직접링크' and urls:
        if is_affiliate_ranking(ctx, urls):
            store.update(item['id'], status='unresolved',
                         error='제휴 광고성 다중 링크(TOP N 리뷰, 단일 공구 아님)',
                         hop={'step': '0_skip', 'note': f'{len(urls)}개 링크 + 파트너스 문구'})
            return
        current_url = first_usable_url(urls)
    elif profile_urls:
        current_url = first_usable_url(profile_urls)
    elif urls:
        current_url = first_usable_url(urls)
    else:
        store.update(item['id'], status='unresolved', error='urls/profile_urls 없음')
        return

    current_url = normalize_url(current_url)
    store.update(item['id'], status='in_progress', hop={'step': '1_fetch', 'url': current_url})

    for hop_n in range(1, MAX_HOPS + 1):
        r = fetch(page, current_url)
        if r['error']:
            store.update(item['id'], status='error', error=r['error'])
            return
        store.update(item['id'], hop={'step': f'{hop_n}_result', 'url': r['final_url'],
                                       'note': f"status={r['status']} title={r['title']}"})

        page_info = {
            'url': r['final_url'],
            'host': host_of(r['final_url'] or current_url),
            'title': r['title'],
            'jsonld_name': r['jsonld'].get('name'),
            'jsonld_price': r['jsonld'].get('price'),
            'has_og_image': bool(r['jsonld'].get('image') or r['og_image']),
            'body_text_snippet': r.get('body_text', ''),  # og/JSON-LD 없을 때 판단 근거 보강용
        }
        try:
            verdict = judge_page(ctx, page_info)
        except Exception as e:
            store.update(item['id'], status='error', error=f'LLM#3 호출 실패: {str(e)[:120]}')
            return
        store.update(item['id'], hop={
            'step': f'{hop_n}_judge',
            'note': f"{verdict.get('page_type')} / final={verdict.get('is_final_product_page')} / "
                    f"{(verdict.get('reason') or '')[:80]}"})

        if verdict.get('page_type') == '상품페이지' and verdict.get('is_final_product_page'):
            img_url = r['jsonld'].get('image') or r['og_image']
            img_ok = download_image(ctx_holder['ctx'], img_url, item['id']) if img_url else False
            store.update(item['id'], status='done', final={
                'final_url': r['final_url'],
                'name': r['jsonld'].get('name') or verdict.get('product_name_guess') or r['title'],
                'price': r['jsonld'].get('price'),
                'image_saved': img_ok,
            })
            return

        if verdict.get('page_type') == '링크모음':
            links = extract_collection_links(page)
            store.update(item['id'], hop={'step': f'{hop_n}_links', 'note': f'{len(links)}개 후보 링크'})
            if not links:
                store.update(item['id'], status='unresolved', error='링크모음인데 후보 링크 추출 실패')
                return
            try:
                pick = pick_link(ctx, links)
            except Exception as e:
                store.update(item['id'], status='error', error=f'LLM#2 호출 실패: {str(e)[:120]}')
                return
            idx = pick.get('chosen_index', -1)
            if idx is None or idx < 0 or idx >= len(links):
                store.update(item['id'], status='unresolved', error='LLM#2가 적합한 링크를 못 찾음')
                return
            chosen = links[idx]
            store.update(item['id'], hop={
                'step': f'{hop_n}_pick', 'url': chosen['href'],
                'note': f"{chosen['text'][:50]} (conf={pick.get('confidence')})"})
            current_url = normalize_url(chosen['href'])
            continue

        # 스토어메인 / 로그인월_차단 / 무관 / (상품페이지인데 원본과 불일치)
        store.update(item['id'], status='unresolved',
                     error=f"{verdict.get('page_type')} — {(verdict.get('reason') or '')[:150]}")
        return

    store.update(item['id'], status='unresolved', error=f'최대 홉({MAX_HOPS}) 초과')


def load_cases(platform, n):
    """대시보드 store에서 아직 'pending'인 것만 다음 N개 골라온다 — 반복 실행하면
    이미 처리한 건은 건너뛰고 자연스럽게 이어서 처리된다(누적 통계용)."""
    ELIGIBLE = ('설명_직접링크', '설명_프로필안내')
    if platform == 'yt':
        items = json.load(open(SAMPLES / 'case_sample_yt_100.json', encoding='utf-8'))
    else:
        items = json.load(open(SAMPLES / 'case_sample_ig_100.json', encoding='utf-8'))
    cases = [r for r in items if (r.get('link_classification') or {}).get('link_location') in ELIGIBLE]
    pending_ids = {r['id'] for r in store.load() if r['status'] == 'pending'}
    cases = [c for c in cases if c['id'] in pending_ids]
    return cases[:n]


def load_by_ids(ids):
    """id 목록으로 직접 지정해서 재시도 — pending 여부 상관없이 store를 pending으로 리셋하고 가져온다."""
    ids = set(ids)
    out = []
    for fname in ('case_sample_ig_100.json', 'case_sample_yt_100.json'):
        for it in json.load(open(SAMPLES / fname, encoding='utf-8')):
            if it['id'] in ids:
                out.append(it)
    for i in ids:
        store.update(i, status='pending', error=None)
    # update()는 병합만 하니 hops/final도 리셋
    records = store.load()
    for r in records:
        if r['id'] in ids:
            r['hops'], r['final'] = [], {}
    store._write(records)
    return out


def main():
    if not DIFY_KEY_PICK or not DIFY_KEY_JUDGE:
        print('DIFY_KEY_PICK / DIFY_KEY_JUDGE 환경변수가 필요합니다.', file=sys.stderr)
        sys.exit(1)

    target = sys.argv[1] if len(sys.argv) > 1 else 'all'
    n = int(sys.argv[2]) if len(sys.argv) > 2 and target != 'retry' else 10

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
        ctx = browser.new_context(
            user_agent=UA, locale='ko-KR', viewport={'width': 1360, 'height': 900},
            extra_http_headers={'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.8'})
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        ctx_holder['ctx'] = ctx
        page = ctx.new_page()

        if target == 'retry':
            ids = sys.argv[2].split(',') if len(sys.argv) > 2 else []
            cases = load_by_ids(ids)
            print(f'retry: {len(cases)}건 처리')
            for item in cases:
                try:
                    resolve_item(page, item)
                except Exception as e:
                    store.update(item['id'], status='error', error=str(e)[:160])
                print(' ', item['id'], 'done')
                time.sleep(ITEM_DELAY)
            browser.close()
            return

        for plat in (['yt', 'ig'] if target == 'all' else [target]):
            cases = load_cases(plat, n)
            print(f'{plat}: {len(cases)}건 처리')
            for item in cases:
                try:
                    resolve_item(page, item)
                except Exception as e:
                    store.update(item['id'], status='error', error=str(e)[:160])
                print(' ', item['id'], 'done')
                time.sleep(ITEM_DELAY)

        browser.close()


if __name__ == '__main__':
    main()
