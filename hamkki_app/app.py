"""
함끼(Hamkki) - Flask 웹 서버

흐름
 1) 사용자가 장소를 검색해서 고른다 (카카오 키워드 검색, 자동완성)
 2) 지금까지 모은 모든 지역 데이터에서 그 장소 반경 안의 식당을 찾는다
 3) 근처 데이터가 부족하면 그 자리에서 카카오로 자동 수집해 저장한다 (다음부터 재사용)
 4) '정확한 조건'(음식 종류)과 상황 규칙으로 후보 20곳을 추린다
 5) Gemini가 후보 중 3곳을 고르고 이유를 만든다 (실패하면 후보 목록을 그대로 보여줌)

실행:  python app.py   →  브라우저에서 http://localhost:5000
"""

import json
import math
import os

# 학교·회사 네트워크처럼 HTTPS를 중간에서 검사하는 곳에서는
# 파이썬 기본 인증서 목록으로는 연결이 막힌다(CERTIFICATE_VERIFY_FAILED).
# truststore를 쓰면 운영체제(Windows)가 믿는 인증서 목록을 그대로 써서 해결된다.
# 보안 검사를 끄는 게 아니라 '어떤 인증서를 믿을지'를 OS에 맡기는 방식이다.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import secrets
import sys
import threading
import time
from urllib.parse import quote

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeSerializer  # Flask와 함께 설치되는 서명 도구
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()  # 상위 폴더(hamkki)의 .env까지 찾아서 읽는다

from ai_recommend import recommend_with_ai  # noqa: E402  (.env를 먼저 읽어야 키가 잡힘)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)  # hamkki_app 의 한 단계 위 = hamkki

# 수집 스크립트(hamkki/collect_restaurants.py)와 DB 저장소(hamkki/db.py)를 웹에서도 같이 쓴다
sys.path.insert(0, PROJECT_DIR)
import collect_restaurants as collector  # noqa: E402
import db  # noqa: E402
import vote_store  # noqa: E402  (db.py를 쓰므로 경로 추가 뒤에)
import feedback_store  # noqa: E402

if db.enabled():  # 배포 서버: 시작할 때 DB 연결을 한 번 확인해서 로그에 남긴다
    print("[DB]", db.check()[1], flush=True)
else:
    print("[DB] DATABASE_URL 없음 → data/ 폴더 파일에 저장", flush=True)

app = Flask(__name__)
# Render는 앞단 서버가 https를 받고 우리 앱에는 http로 넘겨준다.
# 이 설정이 없으면 공유 링크가 http://... 로 만들어지고 쿠키 secure 판단도 틀린다.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# 결과 화면 → 투표 만들기로 넘기는 추천 내용에 서명한다.
# 서명이 없으면 누군가 화면 값을 고쳐서 없는 식당·이상한 문구로 투표를 만들 수 있다.
# 배포 때는 .env(또는 Render 환경변수)에 SECRET_KEY를 꼭 넣는다. 없으면 실행할 때마다 새로 만든다
# (그 경우 앱을 재시작하면 이전 결과 화면의 '투표하기' 버튼만 안 되고, 만들어진 투표는 그대로 된다).
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
_signer = URLSafeSerializer(app.secret_key, salt="hamkki-vote")
VOTER_COOKIE = "hk_voter"   # 브라우저 표시 (중복 투표 방지용, 로그인 없음)
VOTE_OPTION_COUNT = 3       # 투표에 올릴 식당 수

MAX_CANDIDATES = 20     # AI에게 넘길 후보 수
SEARCH_RADIUS_M = 1000  # 검색한 장소에서 이 거리 안의 식당만 후보로
AUTO_TILE_DAILY_LIMIT = 60       # 하루에 새로 모을 수 있는 칸 수 (카카오 쿼터 보호)
AUTO_TILES_PER_SEARCH = 9        # 검색 한 번에 새로 모을 수 있는 칸 수 (기다리는 시간 제한)
_auto_collect_log = []           # 자동으로 칸을 모은 시각 기록

# Gemini 무료 쿼터 보호: 배포 주소는 누구나 들어올 수 있어서, 한 사람이 연달아 누르거나
# 누가 장난으로 계속 요청하면 데모데이에 AI가 멈출 수 있다. 넘으면 AI 없이 후보 목록을 보여준다.
AI_PER_IP_LIMIT = 6              # 한 사람(IP)이 AI_PER_IP_WINDOW초 안에 쓸 수 있는 AI 추천 횟수
AI_PER_IP_WINDOW = 600           # 10분
AI_DAILY_LIMIT = int(os.getenv("AI_DAILY_LIMIT", "300"))  # 서버 전체 하루 AI 추천 횟수
_ai_log = {}                     # IP → 최근 AI 추천 시각들
_ai_day = []                     # 최근 24시간 AI 추천 시각 (서버 전체)
_ai_lock = threading.Lock()

# 예전 방식(장소 이름별 파일)으로 모은 데이터가 있으면 처음 한 번 칸 방식으로 옮긴다
collector.migrate_legacy()

# ---------------------------------------------------------------
# 상황별 규칙 (AI에게 넘길 후보 20곳을 추릴 때, AI가 실패했을 때 사용)
#  prefer: 우선 보여줄 종류 / avoid: 이 상황엔 빼는 종류
#  ※ 이런 단순 규칙으로는 "조용한 곳" 같은 조건을 처리할 수 없다는 것이
#    바로 AI가 필요한 이유이기도 하다.
# ---------------------------------------------------------------
SITUATIONS = {
    "데이트": {
        "emoji": "💑",
        "prefer": {"양식", "일식", "아시아음식", "퓨전요리", "샐러드", "패밀리레스토랑"},
        "avoid": {"분식", "패스트푸드"},
    },
    "가족": {
        "emoji": "👨‍👩‍👧",
        "prefer": {"한식", "중식", "뷔페", "샤브샤브", "패밀리레스토랑"},
        "avoid": {"술집"},
    },
    "친구": {
        "emoji": "🙌",
        "prefer": {"술집", "치킨", "양식", "아시아음식"},
        "avoid": set(),
    },
    "회식": {
        "emoji": "🍻",
        "prefer": {"한식", "술집", "중식", "샤브샤브"},
        "avoid": {"패스트푸드", "분식", "샐러드"},
    },
    "혼밥": {
        "emoji": "🍜",
        "prefer": {"분식", "패스트푸드", "한식", "일식"},
        "avoid": {"뷔페", "술집"},
    },
}

TIME_SLOTS = ["점심", "저녁", "늦은 밤"]
# 음식 종류 선택지 (카카오 분류 두 번째 단계)
KINDS = ["한식", "중식", "일식", "양식", "아시아음식", "분식", "치킨", "술집", "패스트푸드",
         "샤브샤브", "뷔페", "샐러드", "퓨전요리", "패밀리레스토랑"]
BUDGETS = ["1만원 이하", "1~2만원", "2~4만원", "4만원 이상"]
# 재추천 빠른 선택 버튼
FOLLOWUP_CHIPS = ["더 저렴한 곳으로", "더 가까운 곳으로", "조용히 얘기하기 좋은 곳", "다른 종류 음식으로"]
MAX_FOLLOWUPS = 3       # 덧붙일 수 있는 요청 수 (프롬프트가 너무 길어지지 않게)
FOLLOWUP_MAX_LEN = 60


# ---------------------------------------------------------------
# 데이터 (칸 단위 저장은 collect_restaurants.py가 담당)
# ---------------------------------------------------------------
def ai_limit_reason(ip):
    """AI를 써도 되면 None, 막아야 하면 화면에 보여줄 안내 문구. 통과하면 이번 요청을 기록한다.
    (메모리에만 기록하므로 서버가 재시작하면 초기화된다)"""
    now = time.time()
    with _ai_lock:
        _ai_day[:] = [t for t in _ai_day if now - t < 86400]
        if len(_ai_day) >= AI_DAILY_LIMIT:
            return "오늘 AI 추천 사용량이 많아서 잠시 쉬고 있어요."
        mine = [t for t in _ai_log.get(ip, []) if now - t < AI_PER_IP_WINDOW]
        if len(mine) >= AI_PER_IP_LIMIT:
            wait = int((AI_PER_IP_WINDOW - (now - mine[0])) // 60) + 1
            _ai_log[ip] = mine
            return f"AI 추천을 짧은 시간에 많이 요청했어요. {wait}분 뒤에 다시 해 주세요."
        mine.append(now)
        _ai_log[ip] = mine
        _ai_day.append(now)
        if len(_ai_log) > 5000:  # 오래된 IP 기록 정리 (메모리가 계속 늘지 않게)
            for k in [k for k, v in _ai_log.items() if not v or now - v[-1] > AI_PER_IP_WINDOW]:
                del _ai_log[k]
        return None


def tiles_left_today():
    """최근 24시간 동안 새로 모은 칸 수를 빼고 남은 한도"""
    now = time.time()
    _auto_collect_log[:] = [t for t in _auto_collect_log if now - t < 86400]
    return max(0, AUTO_TILE_DAILY_LIMIT - len(_auto_collect_log))


def quick_places(limit=6):
    """첫 화면 '바로 찾기' 버튼: 많이 검색된 장소 순"""
    places = collector.load_named_places()
    ranked = sorted(places.items(), key=lambda kv: -kv[1].get("searches", 0))
    return [name for name, _ in ranked[:limit]]


def get_kind(place):
    """'음식점 > 한식 > 국밥' 에서 두 번째 단계('한식')를 꺼낸다."""
    parts = [s.strip() for s in place.get("category_name", "").split(">")]
    return parts[1] if len(parts) > 1 else "기타"


def get_detail(place):
    """'음식점 > 한식 > 국밥' 에서 마지막 단계('국밥')를 꺼낸다."""
    parts = [s.strip() for s in place.get("category_name", "").split(">")]
    return parts[-1] if len(parts) > 2 else ""


def place_page_url(place):
    """카카오맵 장소 페이지. 수집 데이터의 place_url은 http:// 라서 https:// 웹 주소로 만든다.
    (앱 설치 여부와 상관없이 브라우저에서 열리도록 웹 주소만 쓴다)"""
    return f"https://place.map.kakao.com/{place['id']}" if place.get("id") else ""


def route_url(place):
    """카카오맵 길찾기 웹 링크 (도착지만 지정, 출발지는 사용자가 카카오맵에서 정함)"""
    name = quote(place.get("place_name", "목적지"), safe="")
    return f"https://map.kakao.com/link/to/{name},{place['y']},{place['x']}"


def map_points(items):
    """지도에 찍을 정보만 골라서 화면(JavaScript)으로 넘긴다."""
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"],
             "x": r["x"], "y": r["y"], "rank": i + 1} for i, r in enumerate(items)]


# ---------------------------------------------------------------
# 후보 고르기
# ---------------------------------------------------------------
def find_candidates(nearby, situation, kind_filter):
    """근처 식당 중에서 정확한 조건(음식 종류)과 상황 규칙으로 후보를 추린다."""
    rule = SITUATIONS[situation]
    results = []
    for p, dist in nearby:
        kind = get_kind(p)
        if kind_filter and kind != kind_filter:        # 정확한 조건: 음식 종류
            continue
        if not kind_filter and kind in rule["avoid"]:  # 상황에 안 맞는 종류 제외
            continue
        results.append({
            "id": p["id"],
            "name": p["place_name"],
            "kind": kind,
            "detail": get_detail(p),
            "address": p.get("road_address_name") or p.get("address_name", ""),
            "phone": p.get("phone", ""),
            "url": place_page_url(p),
            "route_url": route_url(p),
            "x": float(p["x"]),
            "y": float(p["y"]),
            "distance": dist,
            "preferred": kind in rule["prefer"],
        })
    # 상황에 잘 맞는 종류 먼저, 그다음 검색한 장소에서 가까운 순
    results.sort(key=lambda r: (not r["preferred"], r["distance"]))
    return results[:MAX_CANDIDATES]


# ---------------------------------------------------------------
# 화면
# ---------------------------------------------------------------
@app.route("/")
def index():
    quick = quick_places()
    return render_template(
        "index.html",
        situations=SITUATIONS,
        time_slots=TIME_SLOTS,
        budgets=BUDGETS,
        kinds=KINDS,
        quick=quick,
        default_place=quick[0] if quick else "",
    )


@app.route("/api/search-place")
def api_search_place():
    """검색창 자동완성. 카카오 키는 서버에만 있고 화면에는 결과만 보낸다."""
    q = request.args.get("q", "").strip()[:40]
    if len(q) < 2:
        return jsonify([])
    try:
        return jsonify(collector.search_places(q))
    except Exception as e:
        print(f"[장소 검색 오류] {type(e).__name__}: {e}")
        if app.debug:  # 개발 중에는 원인을 응답 헤더에 담는다
            return jsonify([]), 502, {"X-Hamkki-Error": f"{type(e).__name__}: {e}"[:300].encode("ascii", "backslashreplace").decode()}
        return jsonify([]), 502


@app.route("/api/where")
def api_where():
    """'현재 위치로 찾기': 휴대폰이 알려준 좌표를 동네 이름으로 바꾼다. 좌표는 저장하지 않는다."""
    try:
        x, y = float(request.args.get("x", "")), float(request.args.get("y", ""))
    except ValueError:
        return jsonify({"error": "좌표가 이상해요."}), 400
    if not (124 < x < 132 and 33 < y < 39):
        return jsonify({"error": "함끼는 국내에서만 쓸 수 있어요."}), 400
    try:
        region = collector.region_name_at(x, y)
    except Exception as e:  # 이름을 못 찾아도 좌표로는 찾을 수 있으니 실패로 치지 않는다
        print(f"[현재 위치 이름 오류] {type(e).__name__}: {e}")
        region = ""
    return jsonify({"name": f"현재 위치 · {region}" if region else "현재 위치"})


def resolve_place(form):
    """
    폼에서 검색한 장소를 좌표로 바꾼다.
    자동완성에서 골랐으면 그 좌표를, 이름만 입력했으면 카카오 검색 1순위를 쓴다.
    """
    name = form.get("place_name", "").strip()[:40]
    gps = form.get("place_source") == "gps"
    try:
        x, y = float(form.get("place_x", "")), float(form.get("place_y", ""))
        if name and 124 < x < 132 and 33 < y < 39:   # 한국 범위 안인지 확인
            if gps:
                # 현재 위치는 약 100m 단위로 흐리게 한다. 투표 링크 지도에 정확한 위치가 나가지 않게.
                # (반경 1km 검색이라 결과에는 거의 차이가 없다)
                x, y = round(x, 3), round(y, 3)
            return {"name": name, "x": x, "y": y, "gps": gps}
    except ValueError:
        pass
    if gps:  # 좌표가 이상하면 이름('현재 위치 · ○○동')으로 검색하지 않는다
        return None

    query = form.get("place_query", "").strip()[:40] or name
    if not query:
        return None
    known = collector.load_named_places()
    if query in known:                                # 전에 검색된 장소면 카카오를 다시 부르지 않음
        return {"name": query, "x": known[query]["x"], "y": known[query]["y"]}
    try:
        found = collector.search_places(query, size=1)
    except Exception as e:
        print(f"[장소 검색 오류] {e}")
        found = []
    if found:
        return {"name": found[0]["name"], "x": found[0]["x"], "y": found[0]["y"]}
    return None


@app.route("/recommend", methods=["POST"])
def recommend():
    situation = request.form.get("situation", "친구")
    if situation not in SITUATIONS:
        situation = "친구"

    try:
        people = max(1, min(30, int(request.form.get("people", 2))))
    except ValueError:
        people = 2

    place = resolve_place(request.form)
    if not place:
        return render_template("results.html", c={"region": request.form.get("place_query", ""),
                               "situation": situation, "emoji": SITUATIONS[situation]["emoji"],
                               "people": people, "time_slot": "", "budget": "", "kind": "", "wish": ""},
                               candidates=[], ai=None, ai_error=None, has_data=True,
                               place_not_found=True)

    conditions = {
        "region": place["name"],
        "situation": situation,
        "emoji": SITUATIONS[situation]["emoji"],
        "people": people,
        "time_slot": request.form.get("time_slot", "저녁"),
        "budget": request.form.get("budget", ""),
        "kind": request.form.get("kind", ""),
        "wish": request.form.get("wish", "").strip()[:200],
        # 재추천: 지금까지 덧붙인 요청들 (오래된 것 → 최신 순)
        "followups": read_followups(request.form),
    }

    # 검색한 장소 주변 칸 중 아직 없는 칸만 그 자리에서 모은다 (다음 사람부터는 바로 사용)
    collected_now = 0
    collect_note = None
    keys = collector.tiles_covering(place["x"], place["y"], SEARCH_RADIUS_M)
    if collector.missing_tiles(keys, place["x"], place["y"], SEARCH_RADIUS_M):
        allowed = min(AUTO_TILES_PER_SEARCH, tiles_left_today())
        if allowed == 0:
            collect_note = "오늘은 새 지역 수집 한도를 다 썼어요. 이미 모아둔 식당 안에서 찾았어요."
        else:
            try:
                new_tiles, collected_now, skipped = collector.ensure_area(
                    place["x"], place["y"], SEARCH_RADIUS_M, max_new_tiles=allowed, verbose=False)
                _auto_collect_log.extend([time.time()] * new_tiles)
                if skipped:
                    collect_note = "주변 일부는 아직 못 모았어요. 잠시 후 다시 검색하면 더 넓게 찾아드려요."
            except Exception as e:
                print(f"[자동 수집 오류] {e}")
                collect_note = "이 지역 식당 정보를 새로 모으지 못했어요. 이미 모아둔 식당 안에서 찾았어요."
    if not place.get("gps"):  # 현재 위치는 '바로 찾기' 목록에 남기지 않는다 (개인 위치라서)
        collector.remember_place(place["name"], place["x"], place["y"])
    nearby = collector.places_near(place["x"], place["y"], SEARCH_RADIUS_M)

    candidates = find_candidates(nearby, situation, conditions["kind"])

    # 재추천이면 지금까지 보여준 식당을 AI에게 알려준다 (실제 후보에 있는 ID만 인정, 순서 유지·중복 제거)
    # 직전 3곳만 알려주면 두 번째 재추천에서 처음 추천이 다시 나오는 문제가 있었다.
    by_id = {r["id"]: r for r in candidates}
    seen_ids = list(dict.fromkeys(i for i in request.form.getlist("seen") if i in by_id))
    conditions["seen_ids"] = seen_ids
    conditions["previous"] = [by_id[i]["name"] for i in seen_ids[-3:]]   # 직전 추천
    conditions["earlier"] = [by_id[i]["name"] for i in seen_ids[:-3]]    # 그 전에 보여준 곳

    # AI 추천: 실패하면 ai_result는 None이고, 화면은 후보 목록을 그대로 보여준다
    # 재추천이면 이미 보여준 곳은 AI 후보에서 아예 뺀다 (프롬프트로만 막으면 AI가 어길 수 있음)
    fresh = [r for r in candidates if r["id"] not in seen_ids]
    ai_candidates = fresh if len(fresh) >= 3 else candidates
    ai_result, ai_error = None, None
    if candidates:
        ai_error = ai_limit_reason(request.remote_addr or "?")  # ProxyFix 덕분에 실제 접속자 IP
        if not ai_error:
            ai_result, ai_error = recommend_with_ai(conditions, ai_candidates)

    # 지도: AI가 성공하면 추천 3곳, 실패하면 후보 목록을 찍는다
    shown = ai_result["picks"] if ai_result else candidates
    map_data = {"center": {"name": place["name"], "x": place["x"], "y": place["y"]},
                "points": map_points(shown)} if shown else None

    # 만족도 기록: AI 추천을 보여줄 때마다 한 줄 (기록이 실패해도 추천 화면은 그대로 보여준다)
    rec_id = None
    if ai_result:
        try:
            rec_id = feedback_store.record_shown(situation, len(conditions["followups"]),
                                                 bool(conditions["wish"]), bool(place.get("gps")))
        except (db.DBError, OSError) as e:
            print(f"[만족도 기록 오류] {e}")

    # 투표 만들기용 내용 (서명해서 화면에 숨겨 둔다). AI가 실패하면 후보 앞 3곳으로 투표한다.
    vote_payload = None
    if shown:
        vote_payload = _signer.dumps({
            "rec": rec_id,
            "c": conditions,
            "summary": ai_result["summary"] if ai_result else "",
            "center": map_data["center"],
            "options": [vote_option(r) for r in shown[:VOTE_OPTION_COUNT]],
        })

    return render_template(
        "results.html",
        c=conditions,
        candidates=candidates,
        ai=ai_result,
        ai_error=ai_error,
        has_data=True,
        collected_now=collected_now,
        collect_note=collect_note,
        map_data=map_data,
        kakao_js_key=os.getenv("KAKAO_JS_KEY", ""),
        vote_payload=vote_payload,
        rec_id=rec_id,
        feedback_reasons=feedback_store.REASONS,
        place=place,
        followup_chips=FOLLOWUP_CHIPS,
        can_follow_up=len(conditions["followups"]) < MAX_FOLLOWUPS,
    )


def read_followups(form):
    """이전 요청들(followups) + 이번에 새로 적은 요청(followup)을 정리한다."""
    items = form.getlist("followups") + [form.get("followup", "")]
    items = [" ".join(t.split())[:FOLLOWUP_MAX_LEN] for t in items]
    return [t for t in items if t][-MAX_FOLLOWUPS:]


# ---------------------------------------------------------------
# 동행자 투표 (로그인 없음)
# ---------------------------------------------------------------
VOTE_FIELDS = ("id", "name", "kind", "detail", "address", "url", "route_url", "x", "y", "distance")


def vote_option(r):
    """투표에 저장할 식당 정보 (전화번호 등 필요 없는 건 뺀다)"""
    return {**{k: r.get(k) for k in VOTE_FIELDS}, "reason": r.get("reason", "")}


@app.route("/vote/create", methods=["POST"])
def vote_create():
    try:
        data = _signer.loads(request.form.get("payload", ""))
    except BadSignature:
        return render_template("vote.html", vote=None,
                               error="추천 결과가 만료됐어요. 다시 추천을 받은 뒤 투표를 만들어 주세요."), 400
    voter = _voter_token()
    vote_id = vote_store.create_vote(data["c"], data["summary"], data["center"], data["options"], owner=voter)
    if data.get("rec"):  # 통계: 이 추천이 투표로 이어졌다
        try:
            feedback_store.link_vote(data["rec"], vote_id)
        except (db.DBError, OSError) as e:
            print(f"[만족도 기록 오류] {e}")
    resp = redirect(url_for("vote_page", vote_id=vote_id, new=1))
    _set_voter_cookie(resp, voter)  # 만든 사람 브라우저를 기억 → 이 브라우저에서만 '최종 결정' 버튼이 보임
    return resp


def _voter_token():
    """브라우저 표시(쿠키). 없거나 모양이 이상하면 새로 만든다."""
    voter = request.cookies.get(VOTER_COOKIE, "")
    if 16 <= len(voter) <= 64 and voter.replace("-", "").replace("_", "").isalnum():
        return voter
    return secrets.token_urlsafe(16)


@app.route("/vote/<vote_id>", methods=["GET", "POST"])
def vote_page(vote_id):
    vote = vote_store.load_vote(vote_id)
    if not vote:
        return render_template("vote.html", vote=None, error="투표를 찾을 수 없어요. 링크를 다시 확인해 주세요."), 404

    voter = _voter_token()

    error = None
    if request.method == "POST":
        action = request.form.get("action", "vote")
        if action == "decide":      # 만든 사람: 최종 결정
            ok, error = vote_store.decide(vote_id, voter, request.form.get("pick", ""))
            done = {}
        elif action == "reopen":    # 만든 사람: 결정 취소, 다시 투표
            ok, error = vote_store.decide(vote_id, voter, None)
            done = {}
        else:
            ok, error = vote_store.cast_vote(vote_id, voter, request.form.get("name", ""),
                                             request.form.get("pick", ""))
            done = {"voted": 1}
        if ok:
            # 새로고침해도 다시 제출되지 않게 GET으로 보낸다
            resp = redirect(url_for("vote_page", vote_id=vote_id, **done))
            _set_voter_cookie(resp, voter)
            return resp
        vote = vote_store.load_vote(vote_id)

    counts, leaders = vote_store.tally(vote)
    mine = vote["votes"].get(voter)
    decided = vote.get("decided")
    decided_option = next((o for o in vote["options"] if decided and o["id"] == decided["pick"]), None)
    resp = app.make_response(render_template(
        "vote.html",
        vote=vote,
        c=vote["conditions"],
        counts=counts,
        leaders=leaders,
        total=len(vote["votes"]),
        mine=mine,
        decided=decided_option,
        is_owner=bool(vote.get("owner")) and vote.get("owner") == voter,
        error=error,
        form_name=request.form.get("name", "") if error else (mine["name"] if mine else ""),
        is_new=request.args.get("new") == "1",
        just_voted=request.args.get("voted") == "1",
        share_url=url_for("vote_page", vote_id=vote_id, _external=True),
        map_data={"center": vote["center"], "points": map_points(vote["options"])},
        kakao_js_key=os.getenv("KAKAO_JS_KEY", ""),
    ))
    _set_voter_cookie(resp, voter)
    return resp


# ---------------------------------------------------------------
# 추천 만족도 (👍👎) / 관리용 통계
# ---------------------------------------------------------------
@app.route("/api/feedback", methods=["POST"])
def api_feedback():
    body = request.get_json(silent=True) or {}
    reasons = body.get("reasons") if isinstance(body.get("reasons"), list) else []
    ok = feedback_store.rate(str(body.get("rec", ""))[:20], body.get("rating"), [str(r)[:30] for r in reasons[:10]])
    return jsonify({"ok": ok}), (200 if ok else 400)


@app.route("/admin/stats")
def admin_stats():
    """보고서·발표용 통계. Render 환경변수 ADMIN_KEY와 주소의 ?key= 가 같을 때만 보인다."""
    key = os.getenv("ADMIN_KEY", "")
    if not key or not secrets.compare_digest(request.args.get("key", ""), key):
        return render_template("vote.html", vote=None, error="페이지를 찾을 수 없어요."), 404
    return render_template("stats.html", s=feedback_store.stats(),
                           storage="Postgres" if db.enabled() else "파일(data/)")


@app.errorhandler(db.DBError)
def db_error(e):
    """DB가 잠깐 안 될 때 오류 페이지 대신 안내를 보여준다. (식당 추천은 파일로 대신해서 계속 됨)"""
    print(f"[DB 오류] {e}", flush=True)
    return render_template("vote.html", vote=None,
                           error="투표 저장소에 잠깐 연결하지 못했어요. 잠시 후 새로고침해 주세요."), 503


def _set_voter_cookie(resp, voter):
    resp.set_cookie(VOTER_COOKIE, voter, max_age=60 * 60 * 24 * 30, httponly=True, samesite="Lax",
                    secure=request.is_secure)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
