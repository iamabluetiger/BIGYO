"""BIGYO API. Only the HTML and explicit API routes are public."""
import hashlib
import json
import os
import random
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent
COOKIE = "bigyo_session"
WELCOME, REWARD, BONUS, BOOST_MS = 1000, 20, 10, 300_000


def now_ms():
    return int(time.time() * 1000)


def load_questions():
    html = (ROOT / "index.html").read_text()
    source = html.split("const QUESTIONS=", 1)[1]
    questions, _ = json.JSONDecoder().raw_decode(source)
    return questions


def make_deck(questions):
    groups = {}
    for q in questions:
        groups.setdefault(q["family"], []).append(q["id"])
    for ids in groups.values():
        random.shuffle(ids)
    deck, previous = [], None
    while groups:
        families = list(groups)
        random.shuffle(families)
        if len(families) > 1 and families[0] == previous:
            families[0], families[1] = families[1], families[0]
        for family in families:
            deck.append(groups[family].pop())
            if not groups[family]:
                del groups[family]
        previous = families[-1]
    return deck


def ensure_demo_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS demo_users (
        user_id TEXT PRIMARY KEY REFERENCES users(id), display_name TEXT NOT NULL
    )""")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VoteInput(Input):
    question_id: str = Field(max_length=100)
    choice: Literal["A", "B"]


class SkipInput(Input):
    question_id: str = Field(max_length=100)


def create_app(db_path=None):
    db_path = Path(db_path or os.environ.get("BIGYO_DB", ROOT / ".data" / "bigyo.sqlite3"))
    questions = load_questions()
    question_ids = {q["id"] for q in questions}

    @contextmanager
    def database(write=False):
        db = sqlite3.connect(db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @asynccontextmanager
    async def lifespan(app):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS questions (
                    id TEXT PRIMARY KEY, content TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, session_hash TEXT UNIQUE NOT NULL,
                    created_at INTEGER NOT NULL, boost_until INTEGER NOT NULL DEFAULT 0,
                    deck TEXT NOT NULL, skipped TEXT NOT NULL DEFAULT '[]'
                );
                CREATE TABLE IF NOT EXISTS votes (
                    user_id TEXT REFERENCES users(id), question_id TEXT REFERENCES questions(id),
                    choice TEXT NOT NULL CHECK(choice IN ('A','B')),
                    reward INTEGER NOT NULL CHECK(reward IN (20,30)), at INTEGER NOT NULL,
                    PRIMARY KEY(user_id,question_id)
                );
                CREATE INDEX IF NOT EXISTS votes_question ON votes(question_id);
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    request_id TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>=500),
                    at INTEGER NOT NULL, UNIQUE(user_id,request_id)
                );
                CREATE TABLE IF NOT EXISTS ledger (
                    id INTEGER PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    kind TEXT NOT NULL CHECK(kind IN ('welcome','vote','withdrawal')),
                    reference TEXT NOT NULL, amount INTEGER NOT NULL, at INTEGER NOT NULL,
                    UNIQUE(user_id,kind,reference)
                );
                CREATE TABLE IF NOT EXISTS referrals (
                    referred_id TEXT PRIMARY KEY REFERENCES users(id),
                    inviter_id TEXT NOT NULL REFERENCES users(id), qualified_at INTEGER,
                    CHECK(referred_id<>inviter_id)
                );
            """)
            ensure_demo_schema(db)
            db.executemany("INSERT INTO questions VALUES (?,?) ON CONFLICT(id) DO UPDATE SET content=excluded.content",
                           [(q["id"], json.dumps(q, ensure_ascii=False)) for q in questions])
        yield

    app = FastAPI(title="BIGYO API", version="0.2.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def web_client(request: Request):
        # Non-simple header + no CORS support blocks cross-origin form/fetch writes.
        if request.headers.get("x-bigyo-client") != "web":
            raise HTTPException(403, "BIGYO 화면에서 다시 시도해 주세요.")
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "다른 사이트에서는 요청할 수 없어요.")

    def user_id(request: Request):
        token = request.cookies.get(COOKIE, "")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with database() as db:
            user = db.execute("SELECT id FROM users WHERE session_hash=?", (digest,)).fetchone()
        if not token or not user:
            raise HTTPException(401, "접속 정보가 만료됐어요. 새로고침해 주세요.")
        return user["id"]

    def balance(db, uid):
        return db.execute("SELECT COALESCE(SUM(amount),0) FROM ledger WHERE user_id=?", (uid,)).fetchone()[0]

    def snapshot(db, uid):
        user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        votes = {v["question_id"]: {"choice": v["choice"], "reward": v["reward"], "at": v["at"]}
                 for v in db.execute("SELECT * FROM votes WHERE user_id=?", (uid,))}
        return {"votes": votes, "withdrawals": [dict(w) for w in db.execute(
                    "SELECT id,amount,at,'received' AS status FROM withdrawals WHERE user_id=? ORDER BY at", (uid,))],
                "boostUntil": user["boost_until"], "order": json.loads(user["deck"]),
                "skipped": json.loads(user["skipped"]), "balance": balance(db, uid),
                "userId": uid, "serverNow": now_ms()}

    def poll(db, qid):
        rows = db.execute("""SELECT v.choice,COUNT(*) AS n,
            SUM(d.user_id IS NOT NULL) AS demo FROM votes v
            LEFT JOIN demo_users d ON d.user_id=v.user_id
            WHERE v.question_id=? GROUP BY v.choice""", (qid,)).fetchall()
        counts = {r["choice"]: r["n"] for r in rows}
        return {"A": counts.get("A", 0), "B": counts.get("B", 0),
                "demoCount": sum(r["demo"] for r in rows)}

    @app.get("/", response_class=HTMLResponse)
    @app.get("/index.html", response_class=HTMLResponse)
    def index():
        html = (ROOT / "index.html").read_text()
        return html.replace("<script>", "<script>window.BIGYO_SERVER=true;", 1)

    @app.get("/api/health")
    def health():
        with database() as db:
            db.execute("SELECT 1").fetchone()
        return {"ok": True, "questions": len(questions), "payoutsEnabled": False}

    @app.post("/api/session", dependencies=[Depends(web_client)])
    def session(request: Request, response: Response):
        token = request.cookies.get(COOKIE, "")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with database(True) as db:
            user = db.execute("SELECT id FROM users WHERE session_hash=?", (digest,)).fetchone()
            if user:
                uid = user["id"]
            else:
                token, uid = secrets.token_urlsafe(32), secrets.token_hex(12)
                digest = hashlib.sha256(token.encode()).hexdigest()
                at = now_ms()
                db.execute("INSERT INTO users(id,session_hash,created_at,deck) VALUES (?,?,?,?)",
                           (uid, digest, at, json.dumps(make_deck(questions))))
                db.execute("INSERT INTO ledger(user_id,kind,reference,amount,at) VALUES (?,'welcome','start',?,?)",
                           (uid, WELCOME, at))
                inviter = request.query_params.get("ref", "")
                if db.execute("""SELECT id FROM users WHERE id=? AND id NOT IN
                    (SELECT user_id FROM demo_users)""", (inviter,)).fetchone():
                    db.execute("INSERT INTO referrals(referred_id,inviter_id) VALUES (?,?)", (uid, inviter))
            data = snapshot(db, uid)
        response.set_cookie(COOKIE, token, max_age=365*86400, httponly=True,
                            secure=request.url.scheme == "https", samesite="lax")
        return data

    @app.get("/api/state")
    def state(uid=Depends(user_id)):
        with database() as db:
            return snapshot(db, uid)

    @app.post("/api/votes", dependencies=[Depends(web_client)])
    def vote(body: VoteInput, uid=Depends(user_id)):
        if body.question_id not in question_ids:
            raise HTTPException(404, "존재하지 않는 카드예요.")
        with database(True) as db:
            old = db.execute("SELECT 1 FROM votes WHERE user_id=? AND question_id=?", (uid, body.question_id)).fetchone()
            if not old:
                at = now_ms()
                boost = db.execute("SELECT boost_until FROM users WHERE id=?", (uid,)).fetchone()[0]
                reward = REWARD + (BONUS if boost > at else 0)
                db.execute("INSERT INTO votes VALUES (?,?,?,?,?)", (uid, body.question_id, body.choice, reward, at))
                db.execute("INSERT INTO ledger(user_id,kind,reference,amount,at) VALUES (?,'vote',?,?,?)",
                           (uid, body.question_id, reward, at))
                referral = db.execute("SELECT inviter_id FROM referrals WHERE referred_id=? AND qualified_at IS NULL", (uid,)).fetchone()
                if referral:
                    db.execute("UPDATE referrals SET qualified_at=? WHERE referred_id=?", (at, uid))
                    db.execute("UPDATE users SET boost_until=? WHERE id=? AND boost_until<=?",
                               (at + BOOST_MS, referral["inviter_id"], at))
            return {"state": snapshot(db, uid), "poll": poll(db, body.question_id), "duplicate": bool(old)}

    @app.get("/api/questions/{qid}/result")
    def result(qid: str, uid=Depends(user_id)):
        if qid not in question_ids:
            raise HTTPException(404, "존재하지 않는 카드예요.")
        with database() as db:
            if not db.execute("SELECT 1 FROM votes WHERE user_id=? AND question_id=?", (uid, qid)).fetchone():
                raise HTTPException(403, "먼저 카드를 골라 주세요.")
            return poll(db, qid)

    @app.post("/api/skip", dependencies=[Depends(web_client)])
    def skip(body: SkipInput, uid=Depends(user_id)):
        if body.question_id not in question_ids:
            raise HTTPException(404, "존재하지 않는 카드예요.")
        with database(True) as db:
            skipped = json.loads(db.execute("SELECT skipped FROM users WHERE id=?", (uid,)).fetchone()[0])
            if body.question_id not in skipped:
                skipped.append(body.question_id)
            voted = {r[0] for r in db.execute("SELECT question_id FROM votes WHERE user_id=?", (uid,))}
            if all(q in skipped or q in voted for q in question_ids):
                skipped = []
            db.execute("UPDATE users SET skipped=? WHERE id=?", (json.dumps(skipped), uid))
            return snapshot(db, uid)

    @app.post("/api/demo/boost", dependencies=[Depends(web_client)])
    def boost(uid=Depends(user_id)):
        raise HTTPException(410, "초대받은 친구가 첫 선택을 완료하면 보너스가 시작돼요.")

    @app.post("/api/withdrawals", dependencies=[Depends(web_client)])
    @app.post("/api/demo/withdrawals", dependencies=[Depends(web_client)])
    def withdraw(uid=Depends(user_id)):
        raise HTTPException(501, "출금 신청은 추후 지원 예정입니다.")

    @app.get("/api/leaderboard")
    def leaderboard(uid=Depends(user_id)):
        with database() as db:
            ranked = """SELECT id,display_name,is_demo,points,RANK() OVER(ORDER BY points DESC) AS rank FROM
                (SELECT u.id,d.display_name,d.user_id IS NOT NULL AS is_demo,
                 COALESCE(SUM(v.reward),0) AS points FROM users u
                 LEFT JOIN votes v ON v.user_id=u.id
                 LEFT JOIN demo_users d ON d.user_id=u.id GROUP BY u.id)"""
            top = [dict(r) for r in db.execute(f"SELECT * FROM ({ranked}) ORDER BY rank,id LIMIT 50")]
            mine = dict(db.execute(f"SELECT * FROM ({ranked}) WHERE id=?", (uid,)).fetchone())
            if all(r["id"] != uid for r in top):
                top.append(mine)
            return {"rows": [{"name": "나" if r["id"] == uid else r["display_name"] or "비교러 " + r["id"][:6],
                              "points": r["points"], "rank": r["rank"], "me": r["id"] == uid,
                              "demo": bool(r["is_demo"])} for r in top],
                    "myRank": mine["rank"],
                    "demoCount": db.execute("SELECT COUNT(*) FROM demo_users").fetchone()[0]}

    @app.post("/api/reset", dependencies=[Depends(web_client)])
    @app.post("/api/demo/reset", dependencies=[Depends(web_client)])
    def reset(uid=Depends(user_id)):
        with database(True) as db:
            for table in ("ledger", "withdrawals", "votes"):
                db.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
            db.execute("UPDATE users SET boost_until=0,deck=?,skipped='[]' WHERE id=?", (json.dumps(make_deck(questions)), uid))
            db.execute("INSERT INTO ledger(user_id,kind,reference,amount,at) VALUES (?,'welcome','start',?,?)", (uid, WELCOME, now_ms()))
            return snapshot(db, uid)

    return app


app = create_app()
