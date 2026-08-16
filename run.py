"""KEEL launcher: seed the reference deployment if needed, then serve.

    .venv/bin/python run.py            # http://127.0.0.1:8347
"""
import os

import uvicorn

if __name__ == "__main__":
    print("KEEL — the runtime trust layer for agentic AI")
    port = int(os.environ.get("KEEL_PORT", "8347"))
    if os.environ.get("KEEL_SANDBOX") == "1":
        print("sandbox demo worlds ENABLED (KEEL_SANDBOX=1) — for evaluation only")
    else:
        print("clean start: no demo data. Connect your data (#/connect) or "
              "register agents (#/gateway).")
    print(f"ready → http://127.0.0.1:{port}")
    uvicorn.run("keel.server.app:app", host="127.0.0.1", port=port,
                log_level="warning")
