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

# 학교·회사 네트워크처럼 HTTPS를 중간에서 검사하는 곳에서는
# 파이썬 기본 인증서 목록으로는 연결이 막힌다(CERTIFICATE_VERIFY_FAILED).
# truststore를 쓰면 운영체제(Windows)가 믿는 인증서 목록을 그대로 써서 해결된다.
# 보안 검사를 끄는 게 아니라 '어떤 인증서를 믿을지'를 OS에 맡기는 방식이다.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

from dotenv import load_dotenv
from flask import Flask, render_template, request

load_dotenv()  # 상위 폴더(hamkki)의 .env까지 찾아서 읽는다

from ai_recommend import recommend_with_ai  # noqa: E402  (.env를 먼저 읽어야 키가 잡힘)

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)  # hamkki_app 의 한 단계 위 = hamkki
DATA_DIR = os.path.join(PROJECT_DIR, "data")
REGION_DIR = os.path.join(DATA_DIR, "regions")
INDEX_FILE = os.path.join(DATA_DIR, "regions.json")  # 수집 스크립트가 만드는 지역 목록

# 지역별 수집 이전에 만든 예전 파일 (있으면 '의정부역'으로 취급)
LEGACY_FILE = os.path.join(DATA_DIR, "restaurants_raw.json")
LEGACY_REGION = {"name": "의정부역", "x": 127.0463, "y": 37.7385}

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
# 데이터 불러오기 (지역별)
# ---------------------------------------------------------------
_cache = {}  # 파일 경로 → (수정 시각, 내용). 파일이 바뀌었을 때만 다시 읽는다.


def _read_json(path):
    mtime = os.path.getmtime(path)
    cached = _cache.get(path)
    if not cached or cached[0] != mtime:
        with open(path, encoding="utf-8") as f:
            _cache[path] = (mtime, json.load(f))
    return _cache[path][1]


def load_regions():
    """
    수집된 지역 목록을 돌려준다. {지역이름: {"x", "y", "file", "count", ...}}
    지역별 수집 이전의 예전 파일만 있으면 그것을 '의정부역'으로 쓴다.
    """
    regions = {}
    if os.path.exists(INDEX_FILE):
        for name, info in _read_json(INDEX_FILE).items():
            path = os.path.join(REGION_DIR, info["file"])
            if os.path.exists(path):
                regions[name] = {**info, "path": path}
    if not regions and os.path.exists(LEGACY_FILE):
        regions[LEGACY_REGION["name"]] = {**LEGACY_REGION, "path": LEGACY_FILE}
    return regions


def load_places(region):
    """한 지역의 식당 목록을 읽는다."""
    info = load_regions().get(region)
    return _read_json(info["path"]) if info else []


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
def find_candidates(region, situation, kind_filter):
    center = load_regions()[region]
    rule = SITUATIONS[situation]
    results = []
    for p in load_places(region):
        kind = get_kind(p)
        if kind_filter and kind != kind_filter:      # 정확한 조건: 음식 종류
            continue
        if not kind_filter and kind in rule["avoid"]:  # 상황에 안 맞는 종류 제외
            continue
        dist = distance_m(center["x"], center["y"], float(p["x"]), float(p["y"]))
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
    # 상황에 잘 맞는 종류 먼저, 그다음 지역 중심에서 가까운 순
    results.sort(key=lambda r: (not r["preferred"], r["distance"]))
    return results[:MAX_CANDIDATES]


# ---------------------------------------------------------------
# 화면
# ---------------------------------------------------------------
@app.route("/")
def index():
    regions = load_regions()
    # 음식 종류 선택지는 모든 지역의 종류를 합쳐서 만든다
    kinds = sorted({get_kind(p) for name in regions for p in load_places(name)})
    return render_template(
        "index.html",
        situations=SITUATIONS,
        time_slots=TIME_SLOTS,
        budgets=BUDGETS,
        kinds=kinds,
        regions=regions,
        place_count=sum(r.get("count", 0) or len(load_places(n)) for n, r in regions.items()),
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

    regions = load_regions()
    region = request.form.get("region", "")
    if region not in regions:
        region = next(iter(regions), "")

    conditions = {
        "region": region,
        "situation": situation,
        "emoji": SITUATIONS[situation]["emoji"],
        "people": people,
        "time_slot": request.form.get("time_slot", "저녁"),
        "budget": request.form.get("budget", ""),
        "kind": request.form.get("kind", ""),
        "wish": request.form.get("wish", "").strip()[:200],  # 자유 문장 (AI 연결 후 사용)
    }

    candidates = find_candidates(region, situation, conditions["kind"]) if region else []

    # AI 추천: 실패하면 ai_result는 None이고, 화면은 후보 목록을 그대로 보여준다
    ai_result, ai_error = recommend_with_ai(conditions, candidates) if candidates else (None, None)

    return render_template(
        "results.html",
        c=conditions,
        candidates=candidates,
        ai=ai_result,
        ai_error=ai_error,
        has_data=bool(regions),
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
