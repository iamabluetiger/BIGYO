"""Start ngrok with an in-memory token; never write the token to disk."""
import getpass
import os
import shutil
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent
runtime = root / ".data"
runtime.mkdir(exist_ok=True)
token = getpass.getpass("ngrok authtoken (hidden): ").strip()
if not token or any(c.isspace() for c in token):
    raise SystemExit("Invalid token format; nothing started.")
executable = shutil.which("ngrok") or "/opt/homebrew/bin/ngrok"
with (runtime / "ngrok.log").open("a") as log:
    process = subprocess.Popen(
        [executable, "http", "http://127.0.0.1:8767", "--log", "stdout", "--log-format", "json"],
        env={**os.environ, "NGROK_AUTHTOKEN": token},
        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
    )
(runtime / "ngrok.pid").write_text(str(process.pid))
print(f"ngrok started (PID {process.pid}). Token kept only in process memory.")
