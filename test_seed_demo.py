import sqlite3

from fastapi.testclient import TestClient

from seed_demo import NAMES, seed_demo_users
from server import create_app

HEADERS = {"X-Bigyo-Client": "web"}


def test_seed_covers_cards_keeps_real_data_and_is_idempotent(tmp_path):
    path = tmp_path / "seed.sqlite3"
    with TestClient(create_app(path), headers=HEADERS) as client:
        uid = client.post("/api/session").json()["userId"]
        client.post("/api/votes", json={"question_id": "commute1", "choice": "A"})
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO withdrawals VALUES (?,?,?,?,?)", ("old-withdrawal", uid, "seed-test-existing-user", 500, 1))
            db.execute("INSERT INTO ledger(user_id,kind,reference,amount,at) VALUES (?,'withdrawal',?,-500,1)", (uid, "old-withdrawal"))
        before = client.get("/api/state").json()
        result = seed_demo_users(path)
        assert result["addedUsers"] == 20
        assert result["addedVotes"] >= 6000
        after = client.get("/api/state").json()
        assert {k: v for k, v in before.items() if k != "serverNow"} == {
            k: v for k, v in after.items() if k != "serverNow"}
        assert seed_demo_users(path) == {"addedUsers": 0, "addedVotes": 0}
        with sqlite3.connect(path) as db:
            rows = db.execute("""SELECT question_id,COUNT(*),SUM(choice='A'),SUM(choice='B')
                FROM votes JOIN demo_users USING(user_id) GROUP BY question_id""").fetchall()
            assert len(rows) == 1000
            assert all(6 <= n <= 16 and a >= 2 and b >= 2 for _, n, a, b in rows)
            totals = dict(db.execute("SELECT user_id,SUM(reward) FROM votes JOIN demo_users USING(user_id) GROUP BY user_id"))
            assert len(totals) == 20 and len(set(totals.values())) >= 15
            for uid, earned in totals.items():
                ledger = db.execute("SELECT SUM(amount) FROM ledger WHERE user_id=?", (uid,)).fetchone()[0]
                assert ledger == 1000 + earned


def test_poll_and_leaderboard_disclose_seed_data(tmp_path):
    path = tmp_path / "display.sqlite3"
    with TestClient(create_app(path), headers=HEADERS) as client:
        client.post("/api/session")
        seed_demo_users(path)
        response = client.post("/api/votes", json={"question_id": "commute1", "choice": "A"}).json()
        poll = response["poll"]
        assert poll["demoCount"] == poll["A"] + poll["B"] - 1
        assert poll["A"] > 0 and poll["B"] > 0
        ranking = client.get("/api/leaderboard").json()
        assert ranking["demoCount"] == 20
        assert {r["name"] for r in ranking["rows"] if r["demo"]} == set(NAMES)
        assert not next(r for r in ranking["rows"] if r["me"])["demo"]
        assert all(a["points"] >= b["points"] for a, b in zip(ranking["rows"], ranking["rows"][1:]))
        client.post("/api/reset")
        assert client.get("/api/leaderboard").json()["demoCount"] == 20
        assert client.get("/api/state").json()["balance"] == 1000
