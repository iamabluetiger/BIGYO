import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from server import COOKIE, create_app

HEADERS = {"X-Bigyo-Client": "web"}


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "test.sqlite3"
    with TestClient(create_app(path), headers=HEADERS) as c:
        c.db_path = path
        yield c


def start(c):
    r = c.post("/api/session")
    assert r.status_code == 200
    return r.json()


def vote(c, qid="commute1", choice="A"):
    return c.post("/api/votes", json={"question_id": qid, "choice": choice})


def test_session_random_order_and_durable_cookie(client):
    first = start(client)
    assert first["balance"] == 1000
    assert len(first["order"]) == len(set(first["order"])) == 1000
    assert start(client)["order"] == first["order"]
    client.cookies.clear()
    other = start(client)
    assert other["userId"] != first["userId"]
    assert other["order"] != first["order"]


def test_votes_authoritative_and_idempotent(client):
    start(client)
    assert vote(client).json()["state"]["balance"] == 1020
    r = vote(client, choice="B").json()
    assert r["duplicate"] and r["state"]["balance"] == 1020
    assert r["state"]["votes"]["commute1"]["choice"] == "A"
    assert vote(client, "invalid").status_code == 404
    assert vote(client, choice="C").status_code == 422
    assert client.post("/api/votes", json={"question_id": "study1", "choice": "A", "reward": 99999}).status_code == 422
    assert client.get("/api/questions/study1/result").status_code == 403


def test_concurrent_duplicate_votes(client):
    start(client)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: vote(client), range(12)))
    assert all(r.status_code == 200 for r in results)
    assert sum(not r.json()["duplicate"] for r in results) == 1
    assert client.get("/api/state").json()["balance"] == 1020


def test_real_poll_and_separate_accounts(client):
    first = start(client)
    vote(client)
    client.cookies.clear()
    second = start(client)
    assert not second["votes"]
    assert vote(client, choice="B").json()["poll"] == {"A": 1, "B": 1, "demoCount": 0}
    assert client.get("/api/questions/commute1/result").json() == {"A": 1, "B": 1, "demoCount": 0}
    assert first["userId"] != second["userId"]


def test_bonus_uses_server_time_and_no_extension(client):
    first = start(client)
    assert client.post("/api/demo/boost").status_code == 410
    assert client.get("/api/state").json()["boostUntil"] == 0
    with TestClient(create_app(client.db_path), headers=HEADERS) as friend:
        friend.post("/api/session?ref=" + first["userId"])
        assert client.get("/api/state").json()["boostUntil"] == 0
        vote(friend)
    boost = client.get("/api/state").json()
    assert boost["boostUntil"] > boost["serverNow"]
    with TestClient(create_app(client.db_path), headers=HEADERS) as another:
        another.post("/api/session?ref=" + first["userId"])
        vote(another)
    assert client.get("/api/state").json()["boostUntil"] == boost["boostUntil"]
    assert vote(client).json()["state"]["votes"]["commute1"]["reward"] == 30
    with sqlite3.connect(client.db_path) as db:
        db.execute("UPDATE users SET boost_until=0 WHERE id=?", (first["userId"],))
    assert vote(client, "study1").json()["state"]["votes"]["study1"]["reward"] == 20


def test_withdrawal_retries_and_atomic_balance(client):
    start(client)
    body = {"amount": 700, "request_id": "test-request-000001"}
    r = client.post("/api/withdrawals", json=body)
    assert r.status_code == 200 and r.json()["state"]["balance"] == 300
    assert r.json()["withdrawal"]["status"] == "received"
    assert client.post("/api/withdrawals", json=body).json()["withdrawal"] == r.json()["withdrawal"]
    assert client.post("/api/withdrawals", json={**body, "amount": 500}).status_code == 409
    assert client.post("/api/withdrawals", json={"amount": 500, "request_id": "test-request-000002"}).status_code == 409
    assert client.post("/api/withdrawals", json={**body, "amount": 1.5}).status_code == 422


def test_concurrent_withdrawals_never_overdraw(client):
    start(client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda i: client.post("/api/withdrawals", json={
            "amount": 700, "request_id": f"concurrent-request-{i}"}), range(4)))
    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409]
    assert client.get("/api/state").json()["balance"] == 300


def test_leaderboard_excludes_welcome_and_ignores_withdrawals(client):
    start(client)
    vote(client)
    client.post("/api/withdrawals", json={"amount": 500, "request_id": "rank-withdraw-001"})
    rows = client.get("/api/leaderboard").json()["rows"]
    assert len(rows) == 1 and rows[0]["points"] == 20 and rows[0]["me"]


def test_skip_reset_and_other_user_isolation(client):
    first = start(client)
    vote(client)
    client.cookies.clear()
    second = start(client)
    r = client.post("/api/skip", json={"question_id": "study1"}).json()
    assert r["skipped"] == ["study1"] and r["balance"] == 1000
    vote(client)
    reset = client.post("/api/reset").json()
    assert reset["balance"] == 1000 and not reset["votes"] and not reset["skipped"]
    assert reset["order"] != second["order"]
    with sqlite3.connect(client.db_path) as db:
        assert db.execute("SELECT count(*) FROM votes WHERE user_id=?", (first["userId"],)).fetchone()[0] == 1


def test_server_restart_keeps_data(client):
    start(client)
    vote(client)
    cookie = client.cookies.get(COOKIE)
    with TestClient(create_app(client.db_path), headers=HEADERS) as restarted:
        restarted.cookies.set(COOKIE, cookie)
        assert restarted.get("/api/state").json()["balance"] == 1020


def test_csrf_session_isolation_and_private_files(client):
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/session", headers={"X-Bigyo-Client": ""}).status_code == 403
    assert client.post("/api/session", headers={"Origin": "https://evil.example"}).status_code == 403
    start(client)
    client.cookies.set(COOKIE, "invented-credential")
    assert client.get("/api/state").status_code == 401
    for path in ("/server.py", "/.data/bigyo.sqlite3", "/.env", "/.venv/pyvenv.cfg", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 404
    assert "window.BIGYO_SERVER=true" in client.get("/").text


def test_https_cookie_flags(tmp_path):
    with TestClient(create_app(tmp_path / "secure.sqlite3"), base_url="https://bigyo.example", headers=HEADERS) as c:
        response = c.post("/api/session")
        cookie = response.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_leaderboard_limits_tied_rows_and_keeps_me(client):
    for _ in range(55):
        client.cookies.clear()
        start(client)
    rows = client.get("/api/leaderboard").json()["rows"]
    assert len(rows) <= 51
    assert sum(row["me"] for row in rows) == 1
    assert all(row["rank"] == 1 for row in rows)


def test_referral_cannot_attach_to_existing_user_or_replay_after_reset(client):
    inviter = start(client)
    with TestClient(create_app(client.db_path), headers=HEADERS) as existing:
        start(existing)
        existing.post("/api/session?ref=" + inviter["userId"])
        vote(existing)
    assert client.get("/api/state").json()["boostUntil"] == 0
    with TestClient(create_app(client.db_path), headers=HEADERS) as friend:
        friend.post("/api/session?ref=" + inviter["userId"])
        vote(friend)
        with sqlite3.connect(client.db_path) as db:
            db.execute("UPDATE users SET boost_until=0 WHERE id=?", (inviter["userId"],))
        friend.post("/api/reset")
        vote(friend)
    assert client.get("/api/state").json()["boostUntil"] == 0
    client.post("/api/session?ref=" + inviter["userId"])
    with sqlite3.connect(client.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM referrals WHERE referred_id=inviter_id").fetchone()[0] == 0
