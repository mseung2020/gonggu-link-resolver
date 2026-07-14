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
import glob
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
from playwright_stealth import Stealth

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

# 세션(쿠키) 저장 파일 — 실행이 끝날 때마다 그 시점 쿠키를 여기에 저장하고, 다음 실행 시작할 때
# 있으면 그대로 불러온다. 로그인 없이도 어떤 요청이 우연히 안티봇을 통과하면서 받은 쿠키가 있으면
# 그게 다음 실행에도 그대로 이어져서, 매번 완전히 새로운(신뢰도 0인) 상태로 시작하지 않게 된다.
AUTH_STATE_FILE = os.environ.get('AUTH_STATE_FILE', str(ROOT / 'data/auth/session_state.json'))

AFFILIATE_MARKERS = ('파트너스', '쇼핑커넥트', '일정액의 수수료', '수수료를 제공받습니다')
BAD_DOMAINS = ('nid.naver.com', 'accounts.kakao.com', 'account.kakao.com', 'mkt.shopping.naver',
               'pf.kakao.com', 'forms.gle', 'docs.google', 'canva.site', 'band.us',
               'instagram.com', 'youtube.com', 'youtu.be')
NON_PRODUCT_TEXT = ('문의', '상담', '블로그', '유튜브', '인스타그램', '후기', '이벤트 참여',
                    '카카오채널', '카카오톡', '채널톡', '공식 홈페이지')
MAX_HOPS = 3
ITEM_DELAY = float(os.environ.get('ITEM_DELAY', '3'))  # 케이스 사이 대기(초) — 안티봇/레이트리밋 완화
BLOCKED_STATUS_CODES = (403, 429, 490)  # 490=네이버 캡차/보안확인, 403/429=일반 차단·레이트리밋
# 네이버가 상태코드는 200으로 주면서 본문만 캡차/보안확인 오버레이로 채우는 경우가 있어(세션이
# 의심스러워질수록 더 자주 발생) — 상태코드 게이트를 통과해도 본문 문구로 한 번 더 확인한다.
BLOCKED_TEXT_MARKERS = ('security verification', '보안확인을 완료', 'unusual traffic', '비정상적인 접근')

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
        # 네이버 블로그/카페, sanjitalk 같은 공구 공지·상품 페이지는 가격·구성이 JSON-LD가 아니라
        # 본문 텍스트 중간에 있는 경우가 많아서(예: "정가 238,000 공구가 166,600"), 300자로는
        # 대부분 헤더/네비게이션만 잡히고 실제 가격 문구 전에 잘림 — 2000자로 늘려서 판별 근거로 삼는다.
        body_text = page.inner_text('body')[:2000].replace('\n', ' ')
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

        # normalize_url이 처음 goto할 URL은 blog.naver.com(PC)→m.blog.naver.com으로 바꿔주지만,
        # 인포크 같은 리다이렉트 서비스를 한 번 더 거쳐서 도착하는 경우엔 그 서버 사이드 리다이렉트가
        # blog.naver.com(PC)으로 보내버려서 여기서 다시 만난다 — 이때도 본문이 iframe 안에 있어서
        # 그대로 두면 본문 텍스트/링크 추출이 전부 0으로 나온다. 도착지가 PC 블로그면 모바일로 다시 이동.
        if host_of(rec['final_url']) == 'blog.naver.com':
            mobile_url = re.sub(r'^https?://blog\.naver\.com/', 'https://m.blog.naver.com/', rec['final_url'])
            page.goto(mobile_url, wait_until='domcontentloaded', timeout=25000)
            try:
                page.wait_for_load_state('networkidle', timeout=6000)
            except Exception:
                pass
            time.sleep(wait_extra)
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
    # 같은 페이지 안의 앵커/네비게이션 링크("#main-area", "본문으로 바로가기", "게시판 목록
    # 바로가기" 등, fragment만 다르거나 완전히 같은 URL)는 페이지가 안 바뀌니 후보에서 뺀다 —
    # 안 그러면 LLM#2가 이런 걸 "제일 그나마 그럴듯한 후보"로 골라서 3홉 내내 같은 페이지를
    # 맴돌다 "최대 홉 초과"로 실패한다(2026-07-14, 최대홉초과 9건 중 4건에서 이 패턴 확인).
    current_no_frag = page.url.split('#')[0]
    out, seen = [], set()
    for l in raw:
        href, text = l.get('href', ''), l.get('text', '')
        if not href or href in seen or any(d in href for d in BAD_DOMAINS):
            continue
        if href.split('#')[0] == current_no_frag:
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
    # blog.naver.com(PC)은 실제 글이 PostView.naver iframe 안에 들어있어서 메인 프레임만 보는
    # page.inner_text('body')/eval_on_selector_all이 전부 빈 값(0글자, 0링크)을 돌려준다 —
    # m.blog.naver.com(모바일)은 같은 글을 iframe 없이 직접 렌더링하니 여기로 정규화해서 우회한다.
    u = re.sub(r'^https?://blog\.naver\.com/', 'https://m.blog.naver.com/', u)
    return u


TRUNCATED_MATCH = '__TRUNCATED_MATCH__'

# LLM#1이 판단한 "대표 구매 URL의 종류"(url_type)와 실제 도메인을 매칭시키기 위한 힌트.
# 후보가 여러 개일 때(예: 캔바 홍보 링크 + 잘린 네이버쇼핑 링크가 같이 있는 설명) 이게 없으면
# "..."로 안 잘렸다는 이유만으로 무관한 링크를 먼저 집어서 오판하게 된다.
URL_TYPE_DOMAIN_HINTS = {
    '네이버_스마트스토어': ('smartstore.naver.com', 'brand.naver.com', 'shopping.naver.com'),
    '네이버_기타': ('naver.com',),
    '쿠팡_오픈마켓': ('coupang.com', 'gmarket.co.kr', 'auction.co.kr', '11st.co.kr', 'interpark.com'),
    '카카오채널': ('kakao.com',),
}


def first_usable_url(urls, url_type=None):
    """캡션에 링크가 여러 개면 그중 온전한 것부터 시도 — "..."로 잘린 링크(크리에이터가 원본부터
    잘라서 올린 경우, 우리가 고칠 방법 없음)는 건너뛰고 다음 후보를 본다.
    url_type과 도메인이 일치하는 후보가 있으면 그걸 최우선으로 보고, 그중에서 안 잘린 것을 고른다.
    일치하는 후보가 전부 잘려있으면 TRUNCATED_MATCH를 돌려줘서 호출부가 무관한 다른 링크로
    잘못 넘어가지 않고 "실제 링크가 잘려서 확인 불가"로 처리하게 한다."""
    urls = [u for u in (urls or []) if u]
    if not urls:
        return None
    hints = URL_TYPE_DOMAIN_HINTS.get(url_type)
    if hints:
        matching = [u for u in urls if any(h in u for h in hints)]
        if matching:
            for u in matching:
                if '...' not in u:
                    return u
            return TRUNCATED_MATCH
    for u in urls:
        if '...' not in u:
            return u
    return urls[0]


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
        current_url = first_usable_url(urls, lc.get('url_type'))
    elif profile_urls:
        current_url = first_usable_url(profile_urls, lc.get('url_type'))
    elif urls:
        current_url = first_usable_url(urls, lc.get('url_type'))
    else:
        store.update(item['id'], status='unresolved', error='urls/profile_urls 없음')
        return

    if current_url == TRUNCATED_MATCH:
        store.update(item['id'], status='unresolved',
                     error=f"실제 구매 링크(url_type={lc.get('url_type')}로 판단됨)가 원본부터 "
                           f"잘려서 사용 불가 — 나머지 후보는 무관한 링크라 스킵")
        return

    current_url = normalize_url(current_url)
    store.update(item['id'], status='in_progress', hop={'step': '1_fetch', 'url': current_url})

    # 스토어메인/링크모음 사이를 A→B→A로 왕복하는 케이스가 있어서(예: 스토어 메인 ↔ 그 안의
    # 소개/약관 서브페이지) 이번 실행에서 이미 들렀던 URL은 이후 홉의 후보에서 제외한다 —
    # 안 그러면 같은 두 페이지만 오가며 3홉을 다 쓰고 "최대 홉 초과"로 끝난다.
    visited = {current_url.split('#')[0]}

    for hop_n in range(1, MAX_HOPS + 1):
        r = fetch(page, current_url)
        if r['error']:
            store.update(item['id'], status='error', error=r['error'])
            return
        store.update(item['id'], hop={'step': f'{hop_n}_result', 'url': r['final_url'],
                                       'note': f"status={r['status']} title={r['title']}"})
        if r['final_url']:
            visited.add(r['final_url'].split('#')[0])

        # HTTP 상태코드로 이미 확실한 차단 신호(네이버 490 캡차/보안확인 등)면 LLM 판단 없이 바로 처리 —
        # LLM#3이 URL 패턴에 혹해서 "상품페이지인데 정보없음"으로 오분류하는 걸 방지.
        if r['status'] in BLOCKED_STATUS_CODES:
            store.update(item['id'], status='unresolved',
                         error=f"로그인월_차단 — HTTP {r['status']} (안티봇/보안확인 페이지로 확인됨)")
            return
        if any(m.lower() in (r.get('body_text') or '').lower() for m in BLOCKED_TEXT_MARKERS):
            store.update(item['id'], status='unresolved',
                         error=f"로그인월_차단 — HTTP {r['status']}이지만 본문이 보안확인/캡차 문구로 확인됨")
            return

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

        page_type = verdict.get('page_type')
        if page_type in ('링크모음', '스토어메인'):
            # 스토어메인은 상품이 수십~수백 개일 수 있어 애매한 매칭을 그대로 채택하면 오탐 위험이 큼 —
            # 링크모음(후보 적고 버튼 텍스트 명확)은 기존처럼 최선의 후보를 채택, 스토어메인은 확신도 high일 때만 채택.
            links = extract_collection_links(page)
            links = [l for l in links if normalize_url(l['href']).split('#')[0] not in visited]
            store.update(item['id'], hop={'step': f'{hop_n}_links', 'note': f'{len(links)}개 후보 링크 ({page_type})'})
            if not links:
                store.update(item['id'], status='unresolved', error=f'{page_type}인데 후보 링크 추출 실패')
                return
            try:
                pick = pick_link(ctx, links)
            except Exception as e:
                store.update(item['id'], status='error', error=f'LLM#2 호출 실패: {str(e)[:120]}')
                return
            idx = pick.get('chosen_index', -1)
            confidence = pick.get('confidence')
            if idx is None or idx < 0 or idx >= len(links):
                store.update(item['id'], status='unresolved', error='LLM#2가 적합한 링크를 못 찾음')
                return
            if page_type == '스토어메인' and confidence != 'high':
                store.update(item['id'], status='unresolved',
                             error=f'스토어메인 후보 중 확신도 낮음(conf={confidence}) — 오탐 방지로 채택 안 함')
                return
            chosen = links[idx]
            store.update(item['id'], hop={
                'step': f'{hop_n}_pick', 'url': chosen['href'],
                'note': f"{chosen['text'][:50]} (conf={confidence})"})
            current_url = normalize_url(chosen['href'])
            continue

        if page_type == '무관':
            # LLM#3이 "무관"으로 판정한 것 중 일부는 실제로는 같은 상품인데 명칭이 달라서 못 알아본
            # 케이스일 수 있어서(사용자 확인) — 자동으로 실패 종료하지 않고 사람이 검토할 "보류"로 뺀다.
            store.update(item['id'], status='hold',
                         error=f"{page_type} — {(verdict.get('reason') or '')[:150]}")
            return

        # 로그인월_차단 / (상품페이지인데 원본과 불일치)
        store.update(item['id'], status='unresolved',
                     error=f"{page_type} — {(verdict.get('reason') or '')[:150]}")
        return

    store.update(item['id'], status='unresolved', error=f'최대 홉({MAX_HOPS}) 초과')


def _load_batch_items(tag):
    """case_sample_{ig,yt}_*.json 전 배치를 glob해서 합친다 (id 중복이면 먼저 나온 파일 우선)."""
    out, seen = [], set()
    for f in sorted(glob.glob(str(SAMPLES / f'case_sample_{tag}_*.json'))):
        for it in json.load(open(f, encoding='utf-8')):
            if it['id'] in seen:
                continue
            seen.add(it['id'])
            out.append(it)
    return out


def load_cases(platform, n):
    """대시보드 store에서 아직 'pending'인 것만 다음 N개 골라온다 — 반복 실행하면
    이미 처리한 건은 건너뛰고 자연스럽게 이어서 처리된다(누적 통계용). 여러 주차 배치를 다 합쳐서 본다.
    link_location이 "댓글참여_DM"/"고정댓글_더보기"/"링크없음_불명"이어도 크리에이터 프로필에
    상시 인포크/링크모음(profile_urls)이 따로 걸려있는 경우가 실제로 많아서(캡션이 참여를 유도한다고
    프로필 링크가 없다는 뜻은 아님) — link_location으로 미리 걸러내지 않고 gate_check와 똑같은
    기준(urls/profile_urls 둘 다 있는지)으로 판단한다."""
    items = _load_batch_items('yt' if platform == 'yt' else 'ig')
    cases = [r for r in items
             if (r.get('link_classification') or {}).get('urls')
             or (r.get('link_classification') or {}).get('profile_urls')]
    pending_ids = {r['id'] for r in store.load() if r['status'] == 'pending'}
    cases = [c for c in cases if c['id'] in pending_ids]
    return cases[:n]


def load_by_ids(ids):
    """id 목록으로 직접 지정해서 재시도 — pending 여부 상관없이 store를 pending으로 리셋하고 가져온다.
    단, 이미 status='done'인 건은 절대 리셋하지 않는다 — done은 "그 시점에 실제로 확인한 상품 정보"라
    이후에 공구 기간이 지나 게이트가 마감으로 막더라도 무효가 되면 안 된다(재시도하면 게이트에서
    막혀 기존 상품명/가격/이미지가 날아가는 사고 방지)."""
    ids = set(ids)
    already_done = {r['id'] for r in store.load() if r['id'] in ids and r['status'] == 'done'}
    if already_done:
        print(f'  [스킵] 이미 done인 {len(already_done)}건은 재시도 대상에서 제외: {sorted(already_done)}')
    ids -= already_done

    out = []
    for it in _load_batch_items('ig') + _load_batch_items('yt'):
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
        ctx_kwargs = dict(
            user_agent=UA, locale='ko-KR', viewport={'width': 1360, 'height': 900},
            extra_http_headers={'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.8'})
        if AUTH_STATE_FILE and pathlib.Path(AUTH_STATE_FILE).exists():
            ctx_kwargs['storage_state'] = AUTH_STATE_FILE
            print(f'로그인 세션 로드: {AUTH_STATE_FILE}')
        ctx = browser.new_context(**ctx_kwargs)
        # 기본값이 Win32/en-US라 UA(Mac)·locale(ko-KR)이랑 안 맞으면 오히려 더 튀어서 맞춰준다.
        Stealth(navigator_platform_override='MacIntel', navigator_languages_override=('ko-KR', 'ko')).apply_stealth_sync(ctx)
        ctx_holder['ctx'] = ctx
        page = ctx.new_page()

        def save_auth_state():
            try:
                pathlib.Path(AUTH_STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
                ctx.storage_state(path=AUTH_STATE_FILE)
                print(f'세션 저장: {AUTH_STATE_FILE}')
            except Exception as e:
                print(f'세션 저장 실패(무시): {e}')

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
            save_auth_state()
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

        save_auth_state()
        browser.close()


if __name__ == '__main__':
    main()
