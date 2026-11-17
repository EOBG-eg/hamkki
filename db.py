"""
함끼(Hamkki) - Postgres 저장소

환경변수 DATABASE_URL이 있을 때만 쓴다. 없으면(내 PC) 지금처럼 data/ 폴더 파일에 저장한다.
Render는 서버가 잠들거나 재시작하면 실행 중에 만든 파일이 사라지므로, 사라지면 안 되는 것만 DB에 둔다.
  - votes        : 동행자 투표
  - tiles        : 검색할 때 그 자리에서 모은 칸 (빌드 때 미리 모은 의정부시는 이미지 안 파일에 있음)
  - named_places : 검색된 장소 이름 ('바로 찾기' 버튼)

드라이버는 pg8000(파이썬으로만 된 드라이버)을 쓴다.
psycopg는 DLL을 쓰는데 학교 PC 보안 정책이 그 DLL을 막아서 바꿨다.
"""

import json
import os
import ssl
import threading
from contextlib import contextmanager
from urllib.parse import parse_qs, unquote, urlsplit

DB_TIMEOUT = 10  # 초. DB가 응답하지 않아도 화면이 멈추지 않게

_schema_ready = False
_schema_lock = threading.Lock()

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS votes (
           id TEXT PRIMARY KEY,
           data JSONB NOT NULL,
           created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS tiles (
           key TEXT PRIMARY KEY,
           info JSONB NOT NULL,
           places JSONB NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS named_places (
           name TEXT PRIMARY KEY,
           info JSONB NOT NULL)""",
]


class DBError(Exception):
    """DB에 연결하지 못했거나 DB가 오류를 냈을 때"""


def enabled():
    return bool(os.environ.get("DATABASE_URL", "").strip())


def _connect_args():
    url = urlsplit(os.environ["DATABASE_URL"].strip())
    sslmode = parse_qs(url.query).get("sslmode", [""])[0]
    host = url.hostname or "localhost"
    # Render 내부 주소(dpg-xxxx-a처럼 점이 없는 이름)는 같은 데이터센터 안이라 암호화 없이 연결한다.
    # 외부 주소(Neon, Render External URL)는 암호화 연결. 인증서 검사는 끄지 않는다.
    if sslmode == "disable" or (not sslmode and "." not in host):
        ctx = None
    else:
        ctx = ssl.create_default_context()
    return dict(
        user=unquote(url.username or ""),
        password=unquote(url.password) if url.password else None,
        host=host,
        port=url.port or 5432,
        database=url.path.lstrip("/") or None,
        ssl_context=ctx,
        timeout=DB_TIMEOUT,
        application_name="hamkki",
    )


@contextmanager
def connect():
    """요청 하나 동안 쓸 연결. with 블록이 끝나면 닫힌다(COMMIT 안 한 내용은 취소됨)."""
    global _schema_ready
    try:
        import pg8000.native
        from pg8000.exceptions import Error as PgError
        con = pg8000.native.Connection(**_connect_args())
    except Exception as e:  # 패키지 없음, 주소 오류, 연결 실패
        raise DBError(f"DB 연결 실패: {type(e).__name__}: {e}") from e
    try:
        if not _schema_ready:
            with _schema_lock:
                if not _schema_ready:
                    for sql in SCHEMA:
                        con.run(sql)
                    _schema_ready = True
        yield con
    except (PgError, OSError) as e:
        raise DBError(f"DB 오류: {type(e).__name__}: {e}") from e
    finally:
        try:
            con.close()
        except Exception:
            pass


def _j(data):
    return json.dumps(data, ensure_ascii=False)


def warn(e):
    print(f"[DB] {e} → 파일 저장으로 대신합니다", flush=True)


# ---------------------------------------------------------------
# 투표
# ---------------------------------------------------------------
def insert_vote(vote):
    """같은 ID가 이미 있으면 False (호출한 쪽에서 새 ID로 다시 시도)"""
    with connect() as con:
        rows = con.run("INSERT INTO votes (id, data) VALUES (:id, CAST(:d AS jsonb)) "
                       "ON CONFLICT (id) DO NOTHING RETURNING id", id=vote["id"], d=_j(vote))
    return bool(rows)


def get_vote(vote_id):
    with connect() as con:
        rows = con.run("SELECT data FROM votes WHERE id = :id", id=vote_id)
    return rows[0][0] if rows else None


def update_vote(vote_id, change):
    """
    투표를 잠그고(FOR UPDATE) 읽어서 change(vote)로 고친 뒤 저장한다.
    두 사람이 동시에 투표해도 한 사람 표가 사라지지 않는다.
    change는 (성공 여부, 오류 문구)를 돌려주고, 성공일 때만 저장한다.
    """
    with connect() as con:
        con.run("START TRANSACTION")
        rows = con.run("SELECT data FROM votes WHERE id = :id FOR UPDATE", id=vote_id)
        result = change(rows[0][0] if rows else None)
        if result[0]:
            con.run("UPDATE votes SET data = CAST(:d AS jsonb) WHERE id = :id", id=vote_id, d=_j(rows[0][0]))
        con.run("COMMIT")
    return result


# ---------------------------------------------------------------
# 칸(타일)
# ---------------------------------------------------------------
def tile_index():
    with connect() as con:
        return {k: info for k, info in con.run("SELECT key, info FROM tiles")}


def tiles(keys):
    if not keys:
        return {}
    with connect() as con:
        return {k: p for k, p in con.run("SELECT key, places FROM tiles WHERE key = ANY(:keys)",
                                         keys=list(keys))}


def save_tile(key, info, places):
    with connect() as con:
        con.run("INSERT INTO tiles (key, info, places) VALUES (:k, CAST(:i AS jsonb), CAST(:p AS jsonb)) "
                "ON CONFLICT (key) DO UPDATE SET info = EXCLUDED.info, places = EXCLUDED.places",
                k=key, i=_j(info), p=_j(places))


def delete_tile(key):
    with connect() as con:
        con.run("DELETE FROM tiles WHERE key = :k", k=key)


# ---------------------------------------------------------------
# 검색된 장소 이름
# ---------------------------------------------------------------
def named_places():
    with connect() as con:
        return {n: info for n, info in con.run("SELECT name, info FROM named_places")}


def remember_place(name, info):
    """처음이면 info로 저장, 이미 있으면 검색 횟수 +1과 마지막 검색 시각만 바꾼다."""
    with connect() as con:
        con.run("INSERT INTO named_places (name, info) VALUES (:n, CAST(:i AS jsonb)) "
                "ON CONFLICT (name) DO UPDATE SET info = named_places.info || jsonb_build_object("
                "'searches', COALESCE((named_places.info->>'searches')::int, 0) + 1, "
                "'last', CAST(:last AS text))",
                n=name, i=_j(info), last=info["last"])


def check():
    """앱 시작 때 연결 확인용. (성공 여부, 설명)"""
    try:
        with connect() as con:
            n = con.run("SELECT count(*) FROM votes")[0][0]
        return True, f"Postgres 연결됨 (투표 {n}개)"
    except DBError as e:
        return False, str(e)
