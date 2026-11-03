"""
함끼(Hamkki) - 식당 자동 수집 스크립트

지역 이름만 넣으면 카카오에서 그 위치를 찾아, 주변 음식점을 자동으로 모은다.
지역마다 data/regions/<지역이름>.json 으로 따로 저장하고,
data/regions.json 에 수집한 지역 목록을 기록한다. 웹 화면은 이 목록을 보고 지역 선택지를 만든다.

실행 예시 (hamkki 폴더에서):
    python collect_restaurants.py                      → 의정부역 (기본값)
    python collect_restaurants.py 홍대입구역            → 홍대입구역 반경 1km
    python collect_restaurants.py 강남역 건대입구역     → 여러 지역을 한 번에
    python collect_restaurants.py 수원역 --radius 700   → 반경을 700m로

카카오 '카테고리로 장소 검색'은 한 번에 최대 45개까지만 받을 수 있어서,
결과가 꽉 찬 구역은 4칸으로 잘게 나눠 다시 검색한다.
"""

import argparse
import json
import math
import os
import time
from collections import Counter
from datetime import datetime

import requests
from dotenv import load_dotenv

# 학교·회사 네트워크에서 인증서 오류가 나는 경우를 위해 OS 인증서 목록을 쓴다 (app.py와 같은 방식)
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

# ---------------------------------------------------------------
# 설정
# ---------------------------------------------------------------
load_dotenv()
KAKAO_REST_KEY = os.environ.get("KAKAO_REST_KEY")

KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
CATEGORY_URL = "https://dapi.kakao.com/v2/local/search/category.json"
CATEGORY_CODE = "FD6"   # FD6 = 음식점 (카페는 CE7)

DEFAULT_REGION = "의정부역"
DEFAULT_RADIUS_M = 1000  # 지역 중심에서 사방으로 이만큼(미터)

PAGE_SIZE = 15          # 카테고리 검색 한 페이지 최대 개수
MAX_PAGES = 3           # 15 x 3 = 45개 (노출 가능한 최대치)
MIN_CELL_SIZE = 0.002   # 이보다 작게는 더 나누지 않음 (약 200m)
REQUEST_DELAY = 0.2     # 요청 사이 대기 시간(초)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
REGION_DIR = os.path.join(DATA_DIR, "regions")
INDEX_FILE = os.path.join(DATA_DIR, "regions.json")

# 추천 대상에서 뺄 종류 (category_name의 두 번째 단계 기준)
# 함께 앉아 식사하기 어려운 업종이라 제외
EXCLUDE_KINDS = {"간식", "식품판매", "도시락"}


def get_kind(place):
    """'음식점 > 한식 > 국밥' 에서 두 번째 단계('한식')를 꺼낸다."""
    parts = [s.strip() for s in place.get("category_name", "").split(">")]
    return parts[1] if len(parts) > 1 else "기타"


def safe_filename(name):
    """파일 이름에 쓸 수 없는 문자를 바꾼다."""
    return "".join("_" if ch in '\\/:*?"<>| ' else ch for ch in name)


# ---------------------------------------------------------------
# 카카오 API 호출
# ---------------------------------------------------------------
def kakao_get(url, params):
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    response = requests.get(url, headers=headers, params=params, timeout=10)
    if response.status_code == 429:
        raise RuntimeError("카카오 API 쿼터를 초과했습니다(429). 내일 다시 시도하세요.")
    if response.status_code != 200:
        raise RuntimeError(f"카카오 API 오류 {response.status_code}: {response.text}")
    return response.json()


def find_center(region_name):
    """지역 이름(역, 동네, 건물 등)으로 중심 좌표를 찾는다."""
    data = kakao_get(KEYWORD_URL, {"query": region_name, "size": 1})
    docs = data.get("documents", [])
    if not docs:
        return None
    top = docs[0]
    return {
        "x": float(top["x"]),
        "y": float(top["y"]),
        "matched": top["place_name"],
        "address": top.get("road_address_name") or top.get("address_name", ""),
    }


def search_rect(min_x, min_y, max_x, max_y, page):
    """사각형 범위 안의 음식점을 한 페이지 검색한다."""
    return kakao_get(CATEGORY_URL, {
        "category_group_code": CATEGORY_CODE,
        "rect": f"{min_x},{min_y},{max_x},{max_y}",
        "page": page,
        "size": PAGE_SIZE,
    })


def collect_cell(min_x, min_y, max_x, max_y, results, depth=0):
    """
    한 구역을 수집한다.
    결과가 45개를 넘으면(= 다 못 받음) 구역을 4칸으로 나눠 다시 수집한다.
    """
    indent = "  " * depth
    first = search_rect(min_x, min_y, max_x, max_y, page=1)
    time.sleep(REQUEST_DELAY)

    total = first["meta"]["total_count"]
    too_many = total > PAGE_SIZE * MAX_PAGES
    can_split = (max_x - min_x) > MIN_CELL_SIZE and (max_y - min_y) > MIN_CELL_SIZE

    if too_many and can_split:
        print(f"{indent}구역 결과 {total}개 → 4칸으로 나눠 다시 검색")
        mid_x = (min_x + max_x) / 2
        mid_y = (min_y + max_y) / 2
        collect_cell(min_x, min_y, mid_x, mid_y, results, depth + 1)  # 좌하
        collect_cell(mid_x, min_y, max_x, mid_y, results, depth + 1)  # 우하
        collect_cell(min_x, mid_y, mid_x, max_y, results, depth + 1)  # 좌상
        collect_cell(mid_x, mid_y, max_x, max_y, results, depth + 1)  # 우상
        return

    if too_many:
        print(f"{indent}⚠ 더 나눌 수 없는 작은 구역인데 {total}개 → 일부 누락 가능")

    data = first
    page = 1
    while True:
        for place in data["documents"]:
            results[place["id"]] = place  # 장소 ID 기준으로 중복 제거
        if data["meta"]["is_end"] or page >= MAX_PAGES:
            break
        page += 1
        data = search_rect(min_x, min_y, max_x, max_y, page=page)
        time.sleep(REQUEST_DELAY)

    print(f"{indent}구역 수집 완료: {total}개 (누적 {len(results)}곳)")


# ---------------------------------------------------------------
# 지역 목록 관리
# ---------------------------------------------------------------
def load_index():
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_index(index):
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------
# 한 지역 수집
# ---------------------------------------------------------------
def collect_region(region_name, radius_m):
    print(f"\n========== [{region_name}] 수집 시작 ==========")
    center = find_center(region_name)
    if not center:
        print(f"'{region_name}'을(를) 카카오에서 찾지 못했어요. 이름을 바꿔 보세요. (예: '홍대' → '홍대입구역')")
        return False
    print(f"중심: {center['matched']} ({center['address']}) / 반경 {radius_m}m")

    # 반경(미터)을 위도·경도 차이로 바꿔 사각형 범위를 만든다
    d_lat = radius_m / 111_000
    d_lng = radius_m / (111_000 * math.cos(math.radians(center["y"])))
    results = {}
    collect_cell(center["x"] - d_lng, center["y"] - d_lat,
                 center["x"] + d_lng, center["y"] + d_lat, results)

    all_places = list(results.values())
    places = [p for p in all_places if get_kind(p) not in EXCLUDE_KINDS]
    excluded = len(all_places) - len(places)

    os.makedirs(REGION_DIR, exist_ok=True)
    filename = safe_filename(region_name) + ".json"
    with open(os.path.join(REGION_DIR, filename), "w", encoding="utf-8") as f:
        json.dump(places, f, ensure_ascii=False, indent=2)

    index = load_index()
    index[region_name] = {
        "file": filename,
        "x": center["x"],
        "y": center["y"],
        "matched": center["matched"],
        "radius_m": radius_m,
        "count": len(places),
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_index(index)

    # 간단한 결과 점검
    print(f"\n수집 {len(all_places)}곳 중 {excluded}곳 제외 ({', '.join(sorted(EXCLUDE_KINDS))})")
    print(f"총 {len(places)}곳 → data/regions/{filename} 에 저장")
    kinds = Counter(get_kind(p) for p in places)
    print("[종류별 개수] " + ", ".join(f"{k} {c}" for k, c in kinds.most_common(8)))
    for p in places[:3]:
        print(f"  예) {p['place_name']} | {p['category_name']} | {p['road_address_name'] or p['address_name']}")
    return True


# ---------------------------------------------------------------
# 실행
# ---------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="함끼 식당 자동 수집")
    parser.add_argument("regions", nargs="*", default=[DEFAULT_REGION],
                        help="수집할 지역 이름들 (예: 홍대입구역 강남역)")
    parser.add_argument("--radius", type=int, default=DEFAULT_RADIUS_M,
                        help=f"지역 중심에서의 반경(미터), 기본 {DEFAULT_RADIUS_M}")
    args = parser.parse_args()

    if not KAKAO_REST_KEY:
        print("KAKAO_REST_KEY가 없습니다. .env 파일을 확인하세요.")
        return

    radius = max(300, min(3000, args.radius))
    done = [r for r in args.regions if collect_region(r, radius)]

    print("\n========== 완료 ==========")
    index = load_index()
    print(f"이번에 수집: {', '.join(done) if done else '없음'}")
    print("수집된 전체 지역: " + ", ".join(f"{k}({v['count']}곳)" for k, v in index.items()))


if __name__ == "__main__":
    main()
