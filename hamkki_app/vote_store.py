"""
함끼(Hamkki) - 동행자 투표 저장소

지금은 data/votes/<투표ID>.json 파일에 저장한다.
배포(Render)할 때 Postgres로 바꾸려면 이 파일의 함수 네 개
(create_vote, load_vote, cast_vote, _save)만 고치면 된다. app.py는 그대로.

투표 파일 모양
{
  "id": "abc123xy",
  "created_at": 1731200000,
  "conditions": {...},           # 조건 요약 (지역, 상황, 인원...)
  "summary": "AI 한 줄 요약",
  "center": {"name", "x", "y"},  # 검색한 장소
  "options": [ {id, name, kind, detail, address, url, route_url, x, y, distance, reason}, ... ],
  "votes": { "<브라우저 표시>": {"name": "유빈", "pick": "<식당 id>", "at": 1731200100} }
}
"""

import json
import os
import re
import secrets
import threading
import time

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOTE_DIR = os.path.join(PROJECT_DIR, "data", "votes")

MAX_VOTERS = 50      # 투표 하나에 참여할 수 있는 최대 인원 (파일이 무한히 커지지 않게)
NAME_MAX = 20        # 이름 최대 글자 수
VOTE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,16}$")

# 두 사람이 동시에 투표해도 한 사람 표가 사라지지 않게, 읽고-고치고-쓰는 동안 잠근다
_lock = threading.Lock()


def _path(vote_id):
    return os.path.join(VOTE_DIR, f"{vote_id}.json")


def _save(vote):
    os.makedirs(VOTE_DIR, exist_ok=True)
    path = _path(vote["id"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(vote, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # 쓰는 도중에 꺼져도 파일이 깨지지 않게


def create_vote(conditions, summary, center, options):
    """새 투표를 만들고 투표 ID를 돌려준다."""
    with _lock:
        while True:
            vote_id = secrets.token_urlsafe(6)  # 8글자, 추측하기 어려운 무작위 ID
            if not os.path.exists(_path(vote_id)):
                break
        _save({
            "id": vote_id,
            "created_at": int(time.time()),
            "conditions": conditions,
            "summary": summary,
            "center": center,
            "options": options,
            "votes": {},
        })
    return vote_id


def load_vote(vote_id):
    """투표를 읽는다. 없거나 ID 모양이 이상하면 None."""
    if not vote_id or not VOTE_ID_RE.match(vote_id):  # ../ 같은 경로 공격 차단
        return None
    path = _path(vote_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def clean_name(name):
    return " ".join((name or "").split())[:NAME_MAX]


def cast_vote(vote_id, voter, name, pick):
    """
    한 표를 기록한다. 성공하면 (True, None), 실패하면 (False, 이유).
    - 같은 브라우저(voter)에서 다시 투표하면 이전 표를 바꾼다.
    - 같은 이름으로 다른 브라우저에서 투표해도 한 사람으로 보고 이전 표를 바꾼다.
    """
    name = clean_name(name)
    if not name:
        return False, "이름을 적어 주세요."
    with _lock:
        vote = load_vote(vote_id)
        if not vote:
            return False, "투표를 찾을 수 없어요."
        if pick not in {o["id"] for o in vote["options"]}:
            return False, "후보 중에서 골라 주세요."

        votes = vote["votes"]
        same_name = [k for k, v in votes.items() if v["name"].casefold() == name.casefold() and k != voter]
        for k in same_name:
            del votes[k]
        if voter not in votes and len(votes) >= MAX_VOTERS:
            return False, f"이 투표는 최대 {MAX_VOTERS}명까지 참여할 수 있어요."
        votes[voter] = {"name": name, "pick": pick, "at": int(time.time())}
        _save(vote)
    return True, None


def tally(vote):
    """식당별 득표 수와 투표한 사람 이름. 1등(동점 포함) ID 목록도 함께."""
    counts = {o["id"]: [] for o in vote["options"]}
    for v in sorted(vote["votes"].values(), key=lambda v: v["at"]):
        if v["pick"] in counts:
            counts[v["pick"]].append(v["name"])
    top = max((len(n) for n in counts.values()), default=0)
    leaders = [k for k, n in counts.items() if top and len(n) == top]
    return counts, leaders
