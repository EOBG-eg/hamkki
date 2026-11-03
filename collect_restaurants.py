"""
함끼(Hamkki) - 식당 자동 수집 (격자 방식)

지도를 약 1km x 1km 크기의 바둑판 칸(타일)으로 나눠서, 칸 단위로 식당을 모으고 저장한다.
 - 어떤 장소를 검색하든, 그 주변 칸 중 아직 없는 칸만 새로 모은다 → 중복 수집이 없다
 - 칸 번호는 좌표만으로 정해진다 → 누가 어떤 이름으로 검색했는지와 상관없이 같은 칸에 저장된다
 - 식당이 하나도 없는 칸(산, 강 등)도 '0곳'으로 기록해서 다시 수집하지 않는다

저장 구조
    data/tiles/<칸번호>.json   칸 하나의 식당 목록
    data/tiles.json             수집한 칸 목록 (식당 수, 수집 시각)
    data/places.json            사람들이 검색한 장소 이름과 좌표 (첫 화면 '바로 찾기'용)

실행 예시 (hamkki 폴더에서):
    python collect_restaurants.py 홍대입구역            → 홍대입구역 반경 1km를 덮는 칸 수집
    python collect_restaurants.py 강남역 건대입구역     → 여러 곳 한 번에
    python collect_restaurants.py 수원역 --radius 1500  → 반경 1.5km
    python collect_restaurants.py 의정부역 --refresh    → 이미 있는 칸도 새로 수집(최신화)
"""

import argparse
import json
import math
import os
import threading
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

# 칸 크기: 위도 0.009도 ≈ 1km, 경도 0.0113도 ≈ 1km (한국 위도 기준)
LAT_STEP = 0.009
LNG_STEP = 0.0113

DEFAULT_RADIUS_M = 1000

PAGE_SIZE = 15          # 카테고리 검색 한 페이지 최대 개수
MAX_PAGES = 3           # 15 x 3 = 45개 (노출 가능한 최대치)
MIN_CELL_SIZE = 0.0005  # 이보다 작게는 더 나누지 않음 (약 50m, 홍대처럼 식당이 빽빽한 곳 대비)
REQUEST_DELAY = 0.15    # 요청 사이 대기 시간(초)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TILE_DIR = os.path.join(DATA_DIR, "tiles")
TILE_INDEX_FILE = os.path.join(DATA_DIR, "tiles.json")
PLACES_FILE = os.path.join(DATA_DIR, "places.json")

# 예전 방식(장소 이름별 파일)의 데이터 - 처음 한 번 칸 방식으로 옮긴다
LEGACY_INDEX_FILE = os.path.join(DATA_DIR, "regions.json")
LEGACY_REGION_DIR = os.path.join(DATA_DIR, "regions")
LEGACY_RAW_FILE = os.path.join(DATA_DIR, "restaurants_raw.json")
LEGACY_RAW_BOX = (127.0360, 37.7300, 127.0570, 37.7470)  # 처음 수집 때 쓴 의정부역 범위

# 추천 대상에서 뺄 종류 (category_name의 두 번째 단계 기준)
# 함께 앉아 식사하기 어려운 업종이라 제외
EXCLUDE_KINDS = {"간식", "식품판매", "도시락"}

_lock = threading.Lock()  # 웹에서 두 사람이 동시에 수집해도 목록 파일이 꼬이지 않게


def get_kind(place):
    """'음식점 > 한식 > 국밥' 에서 두 번째 단계('한식')를 꺼낸다."""
    parts = [s.strip() for s in place.get("category_name", "").split(">")]
    return parts[1] if len(parts) > 1 else "기타"


def _read_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # 쓰는 도중에 꺼져도 파일이 깨지지 않게 한 번에 바꿔치기


# ---------------------------------------------------------------
# 칸(타일) 계산
# ---------------------------------------------------------------
def tile_key(x, y):
    """좌표가 속한 칸 번호. 예: '4192_11243'"""
    return f"{math.floor(y / LAT_STEP)}_{math.floor(x / LNG_STEP)}"


def tile_bounds(key):
    """칸 번호 → (서쪽 경도, 남쪽 위도, 동쪽 경도, 북쪽 위도)"""
    iy, ix = map(int, key.split("_"))
    return (ix * LNG_STEP, iy * LAT_STEP, (ix + 1) * LNG_STEP, (iy + 1) * LAT_STEP)


def tiles_covering(x, y, radius_m):
    """좌표에서 반경 안을 덮는 칸 번호들. (원에 실제로 닿는 칸만)"""
    d_lat = radius_m / 111_000
    d_lng = radius_m / (111_000 * math.cos(math.radians(y)))
    iy0, iy1 = math.floor((y - d_lat) / LAT_STEP), math.floor((y + d_lat) / LAT_STEP)
    ix0, ix1 = math.floor((x - d_lng) / LNG_STEP), math.floor((x + d_lng) / LNG_STEP)
    keys = []
    for iy in range(iy0, iy1 + 1):
        for ix in range(ix0, ix1 + 1):
            key = f"{iy}_{ix}"
            w, s, e, n = tile_bounds(key)
            # 칸 안에서 중심점에 가장 가까운 점까지의 거리가 반경 이내면 닿는 칸
            cx, cy = min(max(x, w), e), min(max(y, s), n)
            if distance_m(x, y, cx, cy) <= radius_m:
                keys.append(key)
    return keys


def distance_m(x1, y1, x2, y2):
    """두 좌표 사이 거리(미터) - 하버사인 공식"""
    r = 6371000
    p1, p2 = math.radians(y1), math.radians(y2)
    dp = math.radians(y2 - y1)
    dl = math.radians(x2 - x1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------
# 저장된 칸 읽기
# ---------------------------------------------------------------
def load_tile_index():
    return _read_json(TILE_INDEX_FILE, {})


def load_tile(key):
    return _read_json(os.path.join(TILE_DIR, f"{key}.json"), [])


def load_named_places():
    return _read_json(PLACES_FILE, {})


def remember_place(name, x, y):
    """검색된 장소 이름을 기록한다. (첫 화면 '바로 찾기' 버튼용)"""
    with _lock:
        places = load_named_places()
        info = places.get(name, {"x": x, "y": y, "searches": 0})
        info["searches"] = info.get("searches", 0) + 1
        info["last"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        places[name] = info
        _write_json(PLACES_FILE, places)


def missing_tiles(keys, x=None, y=None, radius_m=None):
    """
    새로 모아야 하는 칸.
     - 아직 없는 칸은 무조건 포함
     - 일부만 모은 칸(partial)은, 검색 범위가 이미 모은 부분 밖으로 나갈 때만 포함
    """
    index = load_tile_index()
    need = []
    for k in keys:
        entry = index.get(k)
        if entry is None:
            need.append(k)
        elif entry.get("partial"):
            covered = entry.get("covered")
            if not covered or x is None or not _rect_inside(_circle_part_in_tile(k, x, y, radius_m), covered):
                need.append(k)
    return need


def _circle_part_in_tile(key, x, y, radius_m):
    """검색 원이 칸 안에서 차지하는 부분을 감싸는 사각형"""
    d_lat = radius_m / 111_000
    d_lng = radius_m / (111_000 * math.cos(math.radians(y)))
    w, s, e, n = tile_bounds(key)
    return (max(w, x - d_lng), max(s, y - d_lat), min(e, x + d_lng), min(n, y + d_lat))


def _rect_inside(inner, outer, eps=1e-9):
    return (outer[0] - eps <= inner[0] and outer[1] - eps <= inner[1]
            and inner[2] <= outer[2] + eps and inner[3] <= outer[3] + eps)


def _rect_minus(outer, inner):
    """outer 사각형에서 inner 사각형을 뺀 나머지를 최대 4개의 사각형으로"""
    w, s, e, n = outer
    iw, is_, ie, in_ = max(w, inner[0]), max(s, inner[1]), min(e, inner[2]), min(n, inner[3])
    if iw >= ie or is_ >= in_:
        return [outer]
    parts = [
        (w, s, iw, n),        # 왼쪽 띠
        (ie, s, e, n),        # 오른쪽 띠
        (iw, s, ie, is_),     # 가운데 아래
        (iw, in_, ie, n),     # 가운데 위
    ]
    return [p for p in parts if p[2] - p[0] > 1e-7 and p[3] - p[1] > 1e-7]


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


def search_places(query, size=6):
    """검색창 자동완성용: 장소 이름으로 후보 위치 몇 개를 찾는다."""
    data = kakao_get(KEYWORD_URL, {"query": query, "size": size})
    return [{
        "name": d["place_name"],
        "address": d.get("road_address_name") or d.get("address_name", ""),
        "category": d.get("category_group_name", ""),
        "x": float(d["x"]),
        "y": float(d["y"]),
    } for d in data.get("documents", [])]


def find_center(region_name):
    """지역 이름(역, 동네, 건물 등)으로 중심 좌표를 찾는다."""
    found = search_places(region_name, size=1)
    return found[0] if found else None


def search_rect(min_x, min_y, max_x, max_y, page):
    """사각형 범위 안의 음식점을 한 페이지 검색한다."""
    return kakao_get(CATEGORY_URL, {
        "category_group_code": CATEGORY_CODE,
        "rect": f"{min_x},{min_y},{max_x},{max_y}",
        "page": page,
        "size": PAGE_SIZE,
    })


def collect_cell(min_x, min_y, max_x, max_y, results, depth=0, verbose=True):
    """
    한 구역을 수집한다.
    결과가 45개를 넘으면(= 다 못 받음) 구역을 4칸으로 나눠 다시 수집한다.
    """
    indent = "  " * (depth + 1)
    first = search_rect(min_x, min_y, max_x, max_y, page=1)
    time.sleep(REQUEST_DELAY)

    total = first["meta"]["total_count"]
    too_many = total > PAGE_SIZE * MAX_PAGES
    can_split = (max_x - min_x) > MIN_CELL_SIZE and (max_y - min_y) > MIN_CELL_SIZE

    if too_many and can_split:
        mid_x = (min_x + max_x) / 2
        mid_y = (min_y + max_y) / 2
        collect_cell(min_x, min_y, mid_x, mid_y, results, depth + 1, verbose)  # 좌하
        collect_cell(mid_x, min_y, max_x, mid_y, results, depth + 1, verbose)  # 우하
        collect_cell(min_x, mid_y, mid_x, max_y, results, depth + 1, verbose)  # 좌상
        collect_cell(mid_x, mid_y, max_x, max_y, results, depth + 1, verbose)  # 우상
        return

    if too_many and verbose:
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


# ---------------------------------------------------------------
# 칸 수집
# ---------------------------------------------------------------
def _save_tile(key, places, partial=False, source="kakao", covered=None):
    _write_json(os.path.join(TILE_DIR, f"{key}.json"), places)
    with _lock:
        index = load_tile_index()
        entry = {
            "count": len(places),
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": source,
        }
        if partial:
            entry["partial"] = True    # 일부만 들어 있음 → 필요할 때 나머지만 수집
            entry["covered"] = covered  # 이미 모은 범위 (서, 남, 동, 북)
        index[key] = entry
        _write_json(TILE_INDEX_FILE, index)


def collect_tile(key, verbose=True):
    """
    칸 하나를 카카오에서 수집해 저장한다. 새로 저장한 식당 수를 돌려준다.
    일부만 모은 칸이면 비어 있는 부분만 수집해서 기존 데이터와 합친다.
    """
    bounds = tile_bounds(key)
    entry = load_tile_index().get(key)
    if entry and entry.get("partial") and entry.get("covered"):
        rects = _rect_minus(bounds, entry["covered"])
        existing = {p["id"]: p for p in load_tile(key)}
    else:
        rects = [bounds]
        existing = {}

    results = {}
    for r in rects:
        collect_cell(*r, results, verbose=verbose)
    new = {
        p["id"]: p for p in results.values()
        if get_kind(p) not in EXCLUDE_KINDS
        and tile_key(float(p["x"]), float(p["y"])) == key  # 경계에 걸친 식당은 한 칸에만
    }
    added = len(set(new) - set(existing))
    existing.update(new)
    _save_tile(key, list(existing.values()))
    if verbose:
        print(f"  칸 {key}: {len(existing)}곳 (새로 {added}곳)")
    return added


def ensure_area(x, y, radius_m, max_new_tiles=None, refresh=False, verbose=True):
    """
    좌표 반경을 덮는 칸 중 없는 칸만 수집한다.
    돌려주는 값: (새로 수집한 칸 수, 새로 저장한 식당 수, 한도 때문에 못 모은 칸 수)
    """
    keys = tiles_covering(x, y, radius_m)
    todo = keys if refresh else missing_tiles(keys, x, y, radius_m)
    # 가운데 칸부터 모은다 (한도에 걸려도 가장 가까운 곳은 확보)
    todo.sort(key=lambda k: distance_m(x, y, *_tile_center(k)))
    skipped = 0
    if max_new_tiles is not None and len(todo) > max_new_tiles:
        skipped = len(todo) - max_new_tiles
        todo = todo[:max_new_tiles]
    added = 0
    for key in todo:
        if refresh:
            _forget_tile(key)
        added += collect_tile(key, verbose=verbose)
    return len(todo), added, skipped


def _forget_tile(key):
    with _lock:
        index = load_tile_index()
        index.pop(key, None)
        _write_json(TILE_INDEX_FILE, index)


def _tile_center(key):
    w, s, e, n = tile_bounds(key)
    return (w + e) / 2, (s + n) / 2


def places_near(x, y, radius_m):
    """좌표 반경 안의 식당과 거리(미터). 필요한 칸만 읽는다."""
    found = []
    for key in tiles_covering(x, y, radius_m):
        for p in load_tile(key):
            d = distance_m(x, y, float(p["x"]), float(p["y"]))
            if d <= radius_m:
                found.append((p, int(d)))
    return found


# ---------------------------------------------------------------
# 예전 데이터(장소 이름별 파일) → 칸 방식으로 옮기기
# ---------------------------------------------------------------
def migrate_legacy(verbose=True):
    """
    예전 방식으로 모은 데이터를 칸으로 옮긴다. 카카오를 다시 호출하지 않는다.
    예전 수집 범위 안에 완전히 들어가는 칸은 '완성', 걸쳐 있는 칸은 '일부(partial)'로 기록해서
    나중에 필요하면 그 칸만 다시 수집한다. 예전 파일은 지우지 않는다.
    """
    if os.path.exists(TILE_INDEX_FILE):
        return False
    boxes, sources = [], []
    for name, info in _read_json(LEGACY_INDEX_FILE, {}).items():
        path = os.path.join(LEGACY_REGION_DIR, info["file"])
        if not os.path.exists(path):
            continue
        r = info.get("radius_m", DEFAULT_RADIUS_M)
        d_lat = r / 111_000
        d_lng = r / (111_000 * math.cos(math.radians(info["y"])))
        boxes.append((info["x"] - d_lng, info["y"] - d_lat, info["x"] + d_lng, info["y"] + d_lat))
        sources.append(path)
        remember_place(name, info["x"], info["y"])
    if os.path.exists(LEGACY_RAW_FILE):
        boxes.append(LEGACY_RAW_BOX)
        sources.append(LEGACY_RAW_FILE)
    if not sources:
        return False

    tiles = {}
    for path in sources:
        for p in _read_json(path, []):
            if get_kind(p) in EXCLUDE_KINDS:
                continue
            tiles.setdefault(tile_key(float(p["x"]), float(p["y"])), {})[p["id"]] = p

    def best_overlap(key):
        """칸과 예전 수집 범위가 겹치는 부분 중 가장 큰 것"""
        w, s_, e, n = tile_bounds(key)
        best, best_area = None, 0
        for bw, bs, be, bn in boxes:
            ow, os_, oe, on = max(w, bw), max(s_, bs), min(e, be), min(n, bn)
            area = max(0, oe - ow) * max(0, on - os_)
            if area > best_area:
                best, best_area = (ow, os_, oe, on), area
        return best

    full = 0
    for key, places in tiles.items():
        covered = best_overlap(key)
        complete = covered is not None and _rect_inside(tile_bounds(key), covered)
        full += complete
        _save_tile(key, list(places.values()), partial=not complete,
                   source="예전 데이터에서 이동", covered=None if complete else covered)
    if verbose:
        total = sum(len(v) for v in tiles.values())
        print(f"[이동] 예전 데이터 {total}곳 → 칸 {len(tiles)}개 (완성 {full}, 일부 {len(tiles) - full})")
    return True


# ---------------------------------------------------------------
# 실행 (명령 프롬프트)
# ---------------------------------------------------------------
def collect_region(region_name, radius_m, refresh=False):
    print(f"\n========== [{region_name}] ==========")
    center = find_center(region_name)
    if not center:
        print(f"'{region_name}'을(를) 카카오에서 찾지 못했어요. 이름을 바꿔 보세요. (예: '홍대' → '홍대입구역')")
        return False
    print(f"중심: {center['name']} ({center['address']}) / 반경 {radius_m}m")
    remember_place(region_name, center["x"], center["y"])

    keys = tiles_covering(center["x"], center["y"], radius_m)
    new_tiles, added, _ = ensure_area(center["x"], center["y"], radius_m, refresh=refresh)
    print(f"덮는 칸 {len(keys)}개 중 {new_tiles}개 새로 수집 ({added}곳), "
          f"{len(keys) - new_tiles}개는 이미 있어서 건너뜀")

    near = places_near(center["x"], center["y"], radius_m)
    kinds = Counter(get_kind(p) for p, _ in near)
    print(f"반경 안 식당 {len(near)}곳 | " + ", ".join(f"{k} {c}" for k, c in kinds.most_common(6)))
    return True


def main():
    parser = argparse.ArgumentParser(description="함끼 식당 자동 수집 (격자 방식)")
    parser.add_argument("regions", nargs="*", default=["의정부역"],
                        help="수집할 지역 이름들 (예: 홍대입구역 강남역)")
    parser.add_argument("--radius", type=int, default=DEFAULT_RADIUS_M,
                        help=f"지역 중심에서의 반경(미터), 기본 {DEFAULT_RADIUS_M}")
    parser.add_argument("--refresh", action="store_true", help="이미 있는 칸도 새로 수집")
    args = parser.parse_args()

    if not KAKAO_REST_KEY:
        print("KAKAO_REST_KEY가 없습니다. .env 파일을 확인하세요.")
        return

    migrate_legacy()
    radius = max(300, min(3000, args.radius))
    for r in args.regions:
        collect_region(r, radius, refresh=args.refresh)

    index = load_tile_index()
    print(f"\n========== 완료 ==========\n저장된 칸 {len(index)}개, "
          f"식당 {sum(v['count'] for v in index.values())}곳")


if __name__ == "__main__":
    main()
