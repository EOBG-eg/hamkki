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

import sys
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()  # 상위 폴더(hamkki)의 .env까지 찾아서 읽는다

from ai_recommend import recommend_with_ai  # noqa: E402  (.env를 먼저 읽어야 키가 잡힘)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)  # hamkki_app 의 한 단계 위 = hamkki

# 수집 스크립트(hamkki/collect_restaurants.py)의 함수를 웹에서도 같이 쓴다
sys.path.insert(0, PROJECT_DIR)
import collect_restaurants as collector  # noqa: E402

app = Flask(__name__)

MAX_CANDIDATES = 20     # AI에게 넘길 후보 수
SEARCH_RADIUS_M = 1000  # 검색한 장소에서 이 거리 안의 식당만 후보로
AUTO_TILE_DAILY_LIMIT = 60       # 하루에 새로 모을 수 있는 칸 수 (카카오 쿼터 보호)
AUTO_TILES_PER_SEARCH = 9        # 검색 한 번에 새로 모을 수 있는 칸 수 (기다리는 시간 제한)
_auto_collect_log = []           # 자동으로 칸을 모은 시각 기록

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


# ---------------------------------------------------------------
# 데이터 (칸 단위 저장은 collect_restaurants.py가 담당)
# ---------------------------------------------------------------
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
            "url": p.get("place_url", ""),
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


def resolve_place(form):
    """
    폼에서 검색한 장소를 좌표로 바꾼다.
    자동완성에서 골랐으면 그 좌표를, 이름만 입력했으면 카카오 검색 1순위를 쓴다.
    """
    name = form.get("place_name", "").strip()[:40]
    try:
        x, y = float(form.get("place_x", "")), float(form.get("place_y", ""))
        if name and 124 < x < 132 and 33 < y < 39:   # 한국 범위 안인지 확인
            return {"name": name, "x": x, "y": y}
    except ValueError:
        pass

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
    collector.remember_place(place["name"], place["x"], place["y"])
    nearby = collector.places_near(place["x"], place["y"], SEARCH_RADIUS_M)

    candidates = find_candidates(nearby, situation, conditions["kind"])

    # AI 추천: 실패하면 ai_result는 None이고, 화면은 후보 목록을 그대로 보여준다
    ai_result, ai_error = recommend_with_ai(conditions, candidates) if candidates else (None, None)

    return render_template(
        "results.html",
        c=conditions,
        candidates=candidates,
        ai=ai_result,
        ai_error=ai_error,
        has_data=True,
        collected_now=collected_now,
        collect_note=collect_note,
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
