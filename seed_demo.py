"""Add 20 fictional participants once. Existing participants are never changed."""
import argparse
import hashlib
import json
import math
import random
import secrets
import sqlite3

from server import ROOT, WELCOME, ensure_demo_schema, now_ms

NAMES = [
    "선택의달인", "퇴근길스와이퍼", "오늘도비교중", "민트초코파", "소소한선택",
    "고민은짧게", "점심뭐먹지", "산책하는고양이", "새벽의선택", "첫스와이프",
    "집순이의도전", "커피한잔더", "여행가는곰", "월급루팡꿈나무", "공부하다잠깐",
    "계획적인즉흥파", "돈모으는햄스터", "취향수집가", "퇴근하고싶다", "호기심한스푼",
]


def seed_demo_users(db_path):
    rng = random.Random(20261123)
    ids = [f"demo-seed-{i+1:02d}" for i in range(len(NAMES))]
    at = now_ms()
    with sqlite3.connect(db_path, timeout=10) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        ensure_demo_schema(db)
        questions = [r[0] for r in db.execute("SELECT id FROM questions ORDER BY id")]
        if not questions:
            raise ValueError("Run the FastAPI server once before seeding its database.")
        existing = {r[0] for r in db.execute("SELECT user_id FROM demo_users")}
        added = set(ids) - existing
        if not added:
            return {"addedUsers": 0, "addedVotes": 0}
        for uid, name in zip(ids, NAMES):
            if uid not in added:
                continue
            # Unretained random credentials make these display-only participants.
            digest = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
            db.execute("INSERT INTO users(id,session_hash,created_at,deck) VALUES (?,?,?,?)",
                       (uid, digest, at - 10*86400000, json.dumps(questions)))
            db.execute("INSERT INTO demo_users VALUES (?,?)", (uid, name))
            db.execute("INSERT INTO ledger(user_id,kind,reference,amount,at) VALUES (?,'welcome','start',?,?)",
                       (uid, WELCOME, at - 10*86400000))
        votes = []
        for index, qid in enumerate(questions):
            count = rng.randint(6, 16)
            # Different activity levels produce an approachable, varied leaderboard.
            participants = sorted(range(20), key=lambda i: -math.log(rng.random()) / (20-i)**3)[:count]
            if index < 20 and index not in participants:
                participants[-1] = index
            rng.shuffle(participants)
            a_count = rng.randint(2, count-2)
            for position, person in enumerate(participants):
                uid = ids[person]
                choice = "A" if position < a_count else "B"
                reward = 30 if rng.random() < .12 else 20
                voted_at = at - rng.randint(60_000, 7*86400000)
                if uid in added:
                    votes.append((uid, qid, choice, reward, voted_at))
        db.executemany("INSERT INTO votes VALUES (?,?,?,?,?)", votes)
        db.executemany("INSERT INTO ledger(user_id,kind,reference,amount,at) VALUES (?,'vote',?,?,?)",
                       [(uid, qid, reward, voted_at) for uid, qid, _, reward, voted_at in votes])
        return {"addedUsers": len(added), "addedVotes": len(votes)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / ".data" / "bigyo.sqlite3"))
    args = parser.parse_args()
    print(json.dumps(seed_demo_users(args.db), ensure_ascii=False))
