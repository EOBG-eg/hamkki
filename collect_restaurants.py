"""
함끼(Hamkki) - 식당 자동 수집 스크립트 (1순위)

카카오 로컬 API의 '카테고리로 장소 검색'으로 지정한 범위 안의 음식점을 모읍니다.
한 번 검색에 최대 45개까지만 받을 수 있어서, 결과가 꽉 찬 구역은
4칸으로 잘게 나눠 다시 검색합니다.

실행 방법:
    1) .env 파일에 KAKAO_REST_KEY=... 를 적어 둔다
    2) 가상환경을 켠 뒤: python collect_restaurants.py
"""

import json
import os
import time
from collections import Counter

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------
# 설정
# ---------------------------------------------------------------
load_dotenv()
KAKAO_REST_KEY = os.environ.get("KAKAO_REST_KEY")

CATEGORY_URL = "https://dapi.kakao.com/v2/local/search/category.json"
CATEGORY_CODE = "FD6"   # FD6 = 음식점 (카페는 CE7)

# 수집 범위: 의정부역 주변 약 1km (좌측하단 경도, 위도, 우측상단 경도, 위도)
# ※ 대략적인 값입니다. 카카오맵에서 직접 확인하고 필요하면 고치세요.
AREA = {
    "name": "의정부역 일대",
    "min_x": 127.0360,  # 서쪽 경도
    "min_y": 37.7300,   # 남쪽 위도
    "max_x": 127.0570,  # 동쪽 경도
    "max_y": 37.7470,   # 북쪽 위도
}

PAGE_SIZE = 15          # 카테고리 검색 한 페이지 최대 개수
MAX_PAGES = 3           # 15 x 3 = 45개 (노출 가능한 최대치)
MIN_CELL_SIZE = 0.002   # 이보다 작게는 더 나누지 않음 (약 200m)
REQUEST_DELAY = 0.2     # 요청 사이 대기 시간(초), 너무 빠르게 호출하지 않도록

OUTPUT_DIR = "data"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "restaurants_raw.json")

# 추천 대상에서 뺄 종류 (category_name의 두 번째 단계 기준)
# 함께 앉아 식사하기 어려운 업종이라 제외
EXCLUDE_KINDS = {"간식", "식품판매", "도시락"}


def get_kind(place):
    """'음식점 > 한식 > 국밥' 에서 두 번째 단계('한식')를 꺼낸다."""
    parts = [s.strip() for s in place.get("category_name", "").split(">")]
    return parts[1] if len(parts) > 1 else "기타"


# ---------------------------------------------------------------
# 카카오 API 호출
# ---------------------------------------------------------------
def search_rect(min_x, min_y, max_x, max_y, page):
    """사각형 범위 안의 음식점을 한 페이지 검색한다."""
    headers = {"Authorization": f"KakaoAK {KAKAO_REST_KEY}"}
    params = {
        "category_group_code": CATEGORY_CODE,
        "rect": f"{min_x},{min_y},{max_x},{max_y}",
        "page": page,
        "size": PAGE_SIZE,
    }
    response = requests.get(CATEGORY_URL, headers=headers, params=params, timeout=10)

    if response.status_code == 429:
        raise RuntimeError("카카오 API 쿼터를 초과했습니다(429). 내일 다시 시도하세요.")
    if response.status_code != 200:
        raise RuntimeError(f"카카오 API 오류 {response.status_code}: {response.text}")

    return response.json()


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

    # 이 구역은 45개 이하이므로 페이지를 넘기며 전부 받는다
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
# 실행
# ---------------------------------------------------------------
def main():
    if not KAKAO_REST_KEY:
        print("KAKAO_REST_KEY가 없습니다. .env 파일을 확인하세요.")
        return

    print(f"[{AREA['name']}] 음식점 수집 시작")
    results = {}
    collect_cell(AREA["min_x"], AREA["min_y"], AREA["max_x"], AREA["max_y"], results)

    all_places = list(results.values())
    places = [p for p in all_places if get_kind(p) not in EXCLUDE_KINDS]
    excluded = len(all_places) - len(places)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(places, f, ensure_ascii=False, indent=2)

    # 간단한 결과 점검
    print("\n========== 수집 결과 ==========")
    print(f"수집 {len(all_places)}곳 중 {excluded}곳 제외 ({', '.join(sorted(EXCLUDE_KINDS))})")
    print(f"총 {len(places)}곳 → {OUTPUT_FILE} 에 저장")

    # category_name 예: "음식점 > 한식 > 육류,고기"  → 두 번째 단계로 묶어서 집계
    kinds = Counter(get_kind(p) for p in places)

    print("\n[종류별 개수]")
    for kind, count in kinds.most_common():
        print(f"  {kind}: {count}곳")

    print("\n[예시 5곳]")
    for p in places[:5]:
        print(f"  - {p['place_name']} | {p['category_name']} | {p['road_address_name'] or p['address_name']}")


if __name__ == "__main__":
    main()