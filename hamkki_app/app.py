"""
함끼(Hamkki) - Flask 웹 서버 (2순위: 기본 뼈대)

지금 단계에서는 AI 없이, 수집한 식당 데이터에서
'정확한 조건'(음식 종류)과 '상황별 임시 규칙'으로 후보를 골라 보여준다.
12주차에 Gemini를 붙이면 이 후보 목록을 AI에게 넘겨 3곳을 고르게 된다.

실행:  python app.py   →  브라우저에서 http://localhost:5000
"""

import json
import math
import os

from dotenv import load_dotenv
from flask import Flask, render_template, request

load_dotenv()  # 상위 폴더(hamkki)의 .env까지 찾아서 읽는다

from ai_recommend import recommend_with_ai  # noqa: E402  (.env를 먼저 읽어야 키가 잡힘)

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)  # hamkki_app 의 한 단계 위 = hamkki
DATA_FILE = os.path.join(PROJECT_DIR, "data", "restaurants_raw.json")

# 의정부역 대략 위치 (경도, 위도) - 거리 계산 기준점
STATION = {"name": "의정부역", "x": 127.0463, "y": 37.7385}

MAX_CANDIDATES = 20  # 나중에 AI에게 넘길 후보 수

# ---------------------------------------------------------------
# 상황별 임시 규칙 (AI 연결 전까지만 사용)
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
BUDGETS = ["1만원 이하", "1~2만원", "2~4만원", "4만원 이상"]


# ---------------------------------------------------------------
# 데이터 불러오기
# ---------------------------------------------------------------
_cache = {"mtime": None, "places": []}


def load_places():
    """수집 파일을 읽는다. 파일이 바뀌었을 때만 다시 읽는다."""
    if not os.path.exists(DATA_FILE):
        return []
    mtime = os.path.getmtime(DATA_FILE)
    if _cache["mtime"] != mtime:
        with open(DATA_FILE, encoding="utf-8") as f:
            _cache["places"] = json.load(f)
        _cache["mtime"] = mtime
    return _cache["places"]


def get_kind(place):
    """'음식점 > 한식 > 국밥' 에서 두 번째 단계('한식')를 꺼낸다."""
    parts = [s.strip() for s in place.get("category_name", "").split(">")]
    return parts[1] if len(parts) > 1 else "기타"


def get_detail(place):
    """'음식점 > 한식 > 국밥' 에서 마지막 단계('국밥')를 꺼낸다."""
    parts = [s.strip() for s in place.get("category_name", "").split(">")]
    return parts[-1] if len(parts) > 2 else ""


def distance_m(x1, y1, x2, y2):
    """두 좌표 사이 거리(미터) - 하버사인 공식"""
    r = 6371000
    p1, p2 = math.radians(y1), math.radians(y2)
    dp = math.radians(y2 - y1)
    dl = math.radians(x2 - x1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(2 * r * math.asin(math.sqrt(a)))


# ---------------------------------------------------------------
# 후보 고르기 (AI 연결 전 버전)
# ---------------------------------------------------------------
def find_candidates(situation, kind_filter):
    rule = SITUATIONS[situation]
    results = []
    for p in load_places():
        kind = get_kind(p)
        if kind_filter and kind != kind_filter:      # 정확한 조건: 음식 종류
            continue
        if not kind_filter and kind in rule["avoid"]:  # 상황에 안 맞는 종류 제외
            continue
        dist = distance_m(STATION["x"], STATION["y"], float(p["x"]), float(p["y"]))
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
    # 상황에 잘 맞는 종류 먼저, 그다음 역에서 가까운 순
    results.sort(key=lambda r: (not r["preferred"], r["distance"]))
    return results[:MAX_CANDIDATES]


# ---------------------------------------------------------------
# 화면
# ---------------------------------------------------------------
@app.route("/")
def index():
    places = load_places()
    kinds = sorted({get_kind(p) for p in places})
    return render_template(
        "index.html",
        situations=SITUATIONS,
        time_slots=TIME_SLOTS,
        budgets=BUDGETS,
        kinds=kinds,
        place_count=len(places),
        station=STATION["name"],
    )


@app.route("/recommend", methods=["POST"])
def recommend():
    situation = request.form.get("situation", "친구")
    if situation not in SITUATIONS:
        situation = "친구"

    try:
        people = max(1, min(30, int(request.form.get("people", 2))))
    except ValueError:
        people = 2

    conditions = {
        "situation": situation,
        "emoji": SITUATIONS[situation]["emoji"],
        "people": people,
        "time_slot": request.form.get("time_slot", "저녁"),
        "budget": request.form.get("budget", ""),
        "kind": request.form.get("kind", ""),
        "wish": request.form.get("wish", "").strip()[:200],  # 자유 문장 (AI 연결 후 사용)
    }

    candidates = find_candidates(situation, conditions["kind"])

    # AI 추천: 실패하면 ai_result는 None이고, 화면은 후보 목록을 그대로 보여준다
    ai_result, ai_error = recommend_with_ai(conditions, candidates) if candidates else (None, None)

    return render_template(
        "results.html",
        c=conditions,
        candidates=candidates,
        ai=ai_result,
        ai_error=ai_error,
        station=STATION["name"],
        has_data=bool(load_places()),
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
