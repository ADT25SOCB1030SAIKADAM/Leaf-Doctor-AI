"""Stores 'Was this result correct?' answers (no photos, no personal data)."""
import os
import re
import sqlite3
import time
from typing import Optional

DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(DATA_DIR, "feedback.db")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS feedback (ts REAL, plant TEXT, disease TEXT, confidence REAL, "
                 "correct INTEGER, note TEXT, model_version TEXT)")
    return conn


def _clean(text: Optional[str], limit: int) -> str:
    return _CONTROL.sub(" ", text or "").strip()[:limit]


def record(plant: str, disease: str, confidence: float, correct: bool, note: Optional[str] = None,
           model_version: str = "unknown", now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    conn = _connect()
    with conn:
        conn.execute("INSERT INTO feedback VALUES (?,?,?,?,?,?,?)",
                     (now, _clean(plant, 80), _clean(disease, 80), float(confidence), 1 if correct else 0,
                      _clean(note, 200), _clean(model_version, 40)))
    conn.close()


def summary() -> dict:
    conn = _connect()
    total, right = conn.execute("SELECT COUNT(*), COALESCE(SUM(correct), 0) FROM feedback").fetchone()
    worst = conn.execute("SELECT plant, disease, COUNT(*) AS n FROM feedback WHERE correct = 0 "
                         "GROUP BY plant, disease ORDER BY n DESC LIMIT 5").fetchall()
    conn.close()
    return {"total": total, "marked_correct": int(right),
            "percent_correct": round(100.0 * right / total, 1) if total else None,
            "most_reported_wrong": [{"plant": p, "disease": d, "count": n} for p, d, n in worst]}
