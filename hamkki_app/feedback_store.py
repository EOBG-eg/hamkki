"""
함끼(Hamkki) - 추천 만족도 기록과 통계 (보고서·발표용 근거)

AI 추천을 한 번 보여줄 때마다 한 줄을 남기고(rec_id), 사용자가 👍👎을 누르면 그 줄에 평가를 붙인다.
그 추천으로 투표를 만들면 투표 ID도 붙인다. → "만족 ○%", "재추천 후 만족도", "투표로 이어진 비율"

개인정보를 남기지 않으려고 장소 이름·좌표·분위기 문장은 저장하지 않는다. 상황과 재추천 횟수 정도만.
저장 위치는 투표와 같다: DATABASE_URL이 있으면 Postgres feedback 표, 없으면 data/feedback.json
"""

import json
import os
import re
import secrets
import threading
import time
from collections import Counter

import db
import vote_store

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEEDBACK_FILE = os.path.join(PROJECT_DIR, "data", "feedback.json")
REC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,16}$")

# 👎일 때 고를 수 있는 이유 (자유 입력은 받지 않는다: 저장할 글이 없도록)
REASONS = ["너무 멀어요", "상황·분위기에 안 맞아요", "음식 종류가 별로예요",
           "가격대가 안 맞을 것 같아요", "이미 아는 곳이에요"]

_lock = threading.Lock()


def _read_file():
    if not os.path.exists(FEEDBACK_FILE):
        return {}
    with open(FEEDBACK_FILE, encoding="utf-8") as f:
        return json.load(f)


def _write_file(data):
    os.makedirs(os.path.dirname(FEEDBACK_FILE), exist_ok=True)
    tmp = FEEDBACK_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FEEDBACK_FILE)


def _update(rec_id, change):
    if not REC_ID_RE.match(rec_id or ""):
        return False
    if db.enabled():
        return db.update_feedback(rec_id, change)
    with _lock:
        data = _read_file()
        ok = change(data.get(rec_id))
        if ok:
            _write_file(data)
    return ok


def record_shown(situation, round_no, has_wish, gps):
    """AI 추천을 보여줄 때 한 줄 남기고 rec_id를 돌려준다."""
    rec_id = secrets.token_urlsafe(8)
    row = {"at": int(time.time()), "situation": situation, "round": round_no,
           "has_wish": has_wish, "gps": gps, "rating": None, "reasons": [], "vote": None}
    if db.enabled():
        db.insert_feedback(rec_id, row)
    else:
        with _lock:
            data = _read_file()
            data[rec_id] = row
            _write_file(data)
    return rec_id


def rate(rec_id, rating, reasons):
    """👍(up) / 👎(down)과 이유. 다시 누르면 바꾼다. 서버가 만든 rec_id만 받는다(아무 값이나 쌓이지 않게)."""
    if rating not in ("up", "down"):
        return False
    reasons = [r for r in dict.fromkeys(reasons or []) if r in REASONS] if rating == "down" else []

    def change(row):
        if row is None:
            return False
        row["rating"], row["reasons"], row["rated_at"] = rating, reasons, int(time.time())
        return True

    return _update(rec_id, change)


def link_vote(rec_id, vote_id):
    """이 추천으로 투표를 만들었다고 표시"""
    def change(row):
        if row is None or row.get("vote"):
            return False
        row["vote"] = vote_id
        return True

    return _update(rec_id, change)


def _all_rows():
    if db.enabled():
        return db.all_feedback()
    with _lock:
        return list(_read_file().values())


def _kst(ts):
    """한국 날짜 (Render 서버는 UTC라서 9시간을 더한다)"""
    return time.strftime("%Y-%m-%d", time.gmtime(ts + 9 * 3600))


def _pct(a, b):
    return round(a * 100 / b) if b else None


def stats():
    """관리용 통계 페이지에 보여줄 숫자들"""
    rows = _all_rows()
    votes = vote_store.all_votes()
    rated = [r for r in rows if r.get("rating")]
    up = [r for r in rated if r["rating"] == "up"]

    by_round = []
    for n, label in [(0, "처음 추천"), (1, "재추천 1회"), (2, "재추천 2회"), (3, "재추천 3회")]:
        rs = [r for r in rows if r.get("round") == n]
        rr = [r for r in rs if r.get("rating")]
        by_round.append({"label": label, "shown": len(rs), "rated": len(rr),
                         "up_pct": _pct(sum(r["rating"] == "up" for r in rr), len(rr))})

    by_situation = []
    for sit, n in Counter(r.get("situation") for r in rows).most_common():
        rr = [r for r in rows if r.get("situation") == sit and r.get("rating")]
        by_situation.append({"label": sit, "shown": n, "rated": len(rr),
                             "up_pct": _pct(sum(r["rating"] == "up" for r in rr), len(rr))})

    reasons = Counter(x for r in rated for x in r.get("reasons", []))
    ats = [r["at"] for r in rows if r.get("at")]
    voters = [len(v.get("votes", {})) for v in votes]
    return {
        "shown": len(rows),
        "rated": len(rated),
        "rated_pct": _pct(len(rated), len(rows)),
        "up": len(up),
        "up_pct": _pct(len(up), len(rated)),
        "by_round": by_round,
        "by_situation": by_situation,
        "reasons": [(x, reasons.get(x, 0)) for x in REASONS],
        "to_vote": sum(1 for r in rows if r.get("vote")),
        "to_vote_pct": _pct(sum(1 for r in rows if r.get("vote")), len(rows)),
        "gps": sum(1 for r in rows if r.get("gps")),
        "votes": len(votes),
        "voters_avg": round(sum(voters) / len(voters), 1) if voters else None,
        "voted_any": sum(1 for n in voters if n),
        "decided": sum(1 for v in votes if v.get("decided")),
        "decided_pct": _pct(sum(1 for v in votes if v.get("decided")), len(votes)),
        "since": _kst(min(ats)) if ats else None,
        "until": _kst(max(ats)) if ats else None,
    }
