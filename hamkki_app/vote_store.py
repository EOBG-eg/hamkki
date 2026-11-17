"""
함끼(Hamkki) - 동행자 투표 저장소

 - DATABASE_URL이 있으면(배포 서버) Postgres의 votes 표에 저장한다. 서버가 잠들어도 남는다.
 - 없으면(내 PC) data/votes/<투표ID>.json 파일에 저장한다.
app.py는 어느 쪽인지 몰라도 된다. DB 오류는 db.DBError로 올라가고 app.py가 안내 화면을 보여준다.

투표 모양
{
  "id": "abc123xy",
  "created_at": 1731200000,
  "conditions": {...},           # 조건 요약 (지역, 상황, 인원...)
  "summary": "AI 한 줄 요약",
  "center": {"name", "x", "y"},  # 검색한 장소
  "options": [ {id, name, kind, detail, address, url, route_url, x, y, distance, reason}, ... ],
  "votes": { "<브라우저 표시>": {"name": "유빈", "pick": "<식당 id>", "at": 1731200100} },
  "owner": "<만든 사람의 브라우저 표시>",   # 이 사람만 최종 결정·결정 취소 가능
  "decided": {"pick": "<식당 id>", "at": 1731200200} 또는 null
}
"""

import json
import os
import re
import secrets
import threading
import time

import db

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOTE_DIR = os.path.join(PROJECT_DIR, "data", "votes")

MAX_VOTERS = 50      # 투표 하나에 참여할 수 있는 최대 인원 (무한히 커지지 않게)
NAME_MAX = 20        # 이름 최대 글자 수
VOTE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,16}$")

# 파일 저장일 때: 두 사람이 동시에 투표해도 한 사람 표가 사라지지 않게 읽고-고치고-쓰는 동안 잠근다
# (DB 저장일 때는 DB가 같은 역할을 한다: SELECT ... FOR UPDATE)
_lock = threading.Lock()


# ---------------------------------------------------------------
# 파일 저장 (내 PC)
# ---------------------------------------------------------------
def _path(vote_id):
    return os.path.join(VOTE_DIR, f"{vote_id}.json")


def _save_file(vote):
    os.makedirs(VOTE_DIR, exist_ok=True)
    path = _path(vote["id"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(vote, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # 쓰는 도중에 꺼져도 파일이 깨지지 않게


def _load_file(vote_id):
    path = _path(vote_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------
# 앱이 쓰는 함수
# ---------------------------------------------------------------
def create_vote(conditions, summary, center, options, owner=None):
    """새 투표를 만들고 투표 ID를 돌려준다. owner = 만든 사람의 브라우저 표시"""
    vote = {
        "id": "",
        "created_at": int(time.time()),
        "conditions": conditions,
        "summary": summary,
        "center": center,
        "options": options,
        "votes": {},
        "owner": owner,
        "decided": None,
    }
    while True:
        vote["id"] = secrets.token_urlsafe(6)  # 8글자, 추측하기 어려운 무작위 ID
        if db.enabled():
            if db.insert_vote(vote):  # 같은 ID가 이미 있으면 False → 새 ID로 다시
                return vote["id"]
            continue
        with _lock:
            if not os.path.exists(_path(vote["id"])):
                _save_file(vote)
                return vote["id"]


def load_vote(vote_id):
    """투표를 읽는다. 없거나 ID 모양이 이상하면 None."""
    if not vote_id or not VOTE_ID_RE.match(vote_id):  # ../ 같은 경로 공격 차단
        return None
    if db.enabled():
        return db.get_vote(vote_id)
    return _load_file(vote_id)


def clean_name(name):
    return " ".join((name or "").split())[:NAME_MAX]


def _apply_vote(vote, voter, name, pick):
    """투표 내용(vote)에 한 표를 반영한다. (성공 여부, 오류 문구)"""
    if not vote:
        return False, "투표를 찾을 수 없어요."
    if vote.get("decided"):
        return False, "이미 최종 결정된 투표예요."
    if pick not in {o["id"] for o in vote["options"]}:
        return False, "후보 중에서 골라 주세요."
    votes = vote["votes"]
    same_name = [k for k, v in votes.items() if v["name"].casefold() == name.casefold() and k != voter]
    for k in same_name:
        del votes[k]
    if voter not in votes and len(votes) >= MAX_VOTERS:
        return False, f"이 투표는 최대 {MAX_VOTERS}명까지 참여할 수 있어요."
    votes[voter] = {"name": name, "pick": pick, "at": int(time.time())}
    return True, None


def cast_vote(vote_id, voter, name, pick):
    """
    한 표를 기록한다. 성공하면 (True, None), 실패하면 (False, 이유).
    - 같은 브라우저(voter)에서 다시 투표하면 이전 표를 바꾼다.
    - 같은 이름으로 다른 브라우저에서 투표해도 한 사람으로 보고 이전 표를 바꾼다.
    """
    name = clean_name(name)
    if not name:
        return False, "이름을 적어 주세요."
    if not VOTE_ID_RE.match(vote_id or ""):
        return False, "투표를 찾을 수 없어요."
    return _update(vote_id, lambda vote: _apply_vote(vote, voter, name, pick))


def decide(vote_id, voter, pick):
    """
    만든 사람이 최종 결정한다. pick이 None이면 결정을 취소하고 투표를 다시 연다.
    결정되면 더 이상 투표할 수 없다. (True, None) / (False, 이유)
    """
    if not VOTE_ID_RE.match(vote_id or ""):
        return False, "투표를 찾을 수 없어요."

    def change(vote):
        if not vote:
            return False, "투표를 찾을 수 없어요."
        if not vote.get("owner") or vote["owner"] != voter:
            return False, "투표를 만든 사람만 결정할 수 있어요."
        if pick is None:
            vote["decided"] = None
            return True, None
        if pick not in {o["id"] for o in vote["options"]}:
            return False, "후보 중에서 골라 주세요."
        vote["decided"] = {"pick": pick, "at": int(time.time())}
        return True, None

    return _update(vote_id, change)


def _update(vote_id, change):
    """투표를 읽어서 change(vote)로 고치고, 성공하면 저장한다. 읽고-쓰는 동안 다른 요청은 기다린다."""
    if db.enabled():
        return db.update_vote(vote_id, change)
    with _lock:
        vote = _load_file(vote_id)
        ok, err = change(vote)
        if ok:
            _save_file(vote)
    return ok, err


def tally(vote):
    """식당별 득표 수와 투표한 사람 이름. 1등(동점 포함) ID 목록도 함께."""
    counts = {o["id"]: [] for o in vote["options"]}
    for v in sorted(vote["votes"].values(), key=lambda v: v["at"]):
        if v["pick"] in counts:
            counts[v["pick"]].append(v["name"])
    top = max((len(n) for n in counts.values()), default=0)
    leaders = [k for k, n in counts.items() if top and len(n) == top]
    return counts, leaders
