"""Tiny mock KB REST server for local testing of ELTool.

Run:
    python scripts/mock_kb.py

POST http://localhost:8765/query
Body: {"label": "...", "top_k": N}
Returns: {"results": [{"id": "...", "label": "...", "score": ...}, ...]}
"""
from __future__ import annotations

import hashlib
import random

from flask import Flask, jsonify, request

app = Flask(__name__)


def _fake_candidates(label: str, top_k: int) -> list[dict]:
    seed = int(hashlib.md5(label.encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    out = []
    for i in range(top_k):
        out.append(
            {
                "id": f"Q{rng.randint(1000, 9_999_999)}",
                "label": f"{label} (sense {i + 1})",
                "score": round(1.0 - i * 0.07 + rng.random() * 0.02, 4),
            }
        )
    return out


@app.post("/query")
def query():
    data = request.get_json(force=True) or {}
    label = str(data.get("label", ""))
    top_k = int(data.get("top_k", 10))
    return jsonify({"results": _fake_candidates(label, top_k)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765, debug=False)
