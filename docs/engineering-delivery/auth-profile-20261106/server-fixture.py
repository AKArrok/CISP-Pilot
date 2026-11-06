"""Real auth HTTP fixture; sample the original password admission gate."""
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from storage import auth  # noqa: E402


if __name__ == "__main__":
    target = Path(os.environ["CISP_DB_PATH"]).resolve()
    private = (ROOT / "data/review_packets/auth-profile-20261106").resolve()
    if target.parent != private:
        raise ValueError("Explicit isolated profile database required")
    original_gate = auth._PASSWORD_WORK
    lock = threading.Lock()
    local = threading.local()
    active = 0
    phase = "setup"
    trace = Path(sys.argv[2])
    (trace.parent / "security-constants.json").write_text(json.dumps({
        "scrypt_n": auth._SCRYPT_N, "scrypt_r": auth._SCRYPT_R, "scrypt_p": auth._SCRYPT_P,
        "original_gate_capacity": original_gate._initial_value,
        "gate_identity_preserved": True}), encoding="utf-8")

    class TimedGate:
        def __enter__(self):
            global active
            started = time.perf_counter()
            local.started = started
            original_gate.acquire()
            local.admitted = time.perf_counter()
            local.wait = local.admitted - started
            with lock:
                active += 1
                local.active = active

        def __exit__(self, *error):
            global active
            finished = time.perf_counter()
            with lock:
                active -= 1
                with trace.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"phase": phase, "wait_seconds": local.wait,
                        "started_monotonic": local.started, "admitted_monotonic": local.admitted,
                        "finished_monotonic": finished,
                        "derive_seconds": finished - local.admitted, "active_at_admission": local.active,
                        "error": bool(error[0])}) + "\n")
            original_gate.release()

    auth._PASSWORD_WORK = TimedGate()
    accounts = []
    for index in range(8):
        account = {"username": f"profile-user-{index}", "password": secrets.token_urlsafe(24)}
        auth.register(**account)
        accounts.append(account)
    (private / "accounts-private.json").write_text(json.dumps(accounts), encoding="utf-8")
    phase = "profile"
    from data_ingest import manifest
    manifest.validate_index = lambda **_: None
    import server
    import uvicorn
    uvicorn.run(server.app, host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
