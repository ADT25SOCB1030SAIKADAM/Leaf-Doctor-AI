"""Smart irrigation logic: stores soil readings and decides when the pump should run.

No web code lives here, so it is easy to test. The API in sensor.py calls these functions.
Change the numbers below to suit your plant and your sensor.
"""
import os
import sqlite3
import threading
import time
from typing import Optional

DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(DATA_DIR, "irrigation.db")

PUMP_ON_BELOW = 35.0     # start watering when moisture drops under this (%)
PUMP_OFF_ABOVE = 55.0    # stop watering when moisture rises above this (%)
DRY_BELOW = 35.0         # status "dry" under this (%)
WET_ABOVE = 70.0         # status "wet" over this (%)
MAX_PUMP_SECONDS = 60    # safety: one pump run never lasts longer than this
COOLDOWN_SECONDS = 600   # after a safety cut-off the pump rests this long
FRESH_SECONDS = 120      # a reading older than this means "sensor offline"

_lock = threading.Lock()
_state = {"mode": "auto", "pump": False, "pump_since": None, "locked_until": 0.0}


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS readings "
                 "(ts REAL, device TEXT, moisture REAL, raw INTEGER, pump INTEGER)")
    return conn


def status_for(moisture: float) -> str:
    if moisture < DRY_BELOW:
        return "dry"
    if moisture > WET_ABOVE:
        return "wet"
    return "good"


def decide(moisture: float, now: Optional[float] = None) -> bool:
    """Return True if the pump should be ON. Uses hysteresis so it does not flicker."""
    now = time.time() if now is None else now
    with _lock:
        mode = _state["mode"]
        was_on = _state["pump"]
        if mode == "on":
            want = True
        elif mode == "off":
            want = False
        elif now < _state["locked_until"]:
            want = False                      # resting after a safety cut-off
        elif moisture < PUMP_ON_BELOW:
            want = True
        elif moisture >= PUMP_OFF_ABOVE:
            want = False
        else:
            want = was_on                     # in between: keep doing what we were doing

        # Safety: never let one run go on for too long (stuck sensor, empty tank, etc.)
        if want and was_on and _state["pump_since"] is not None \
                and now - _state["pump_since"] > MAX_PUMP_SECONDS:
            want = False
            _state["locked_until"] = now + COOLDOWN_SECONDS
            if mode == "on":
                _state["mode"] = "auto"

        if want and not was_on:
            _state["pump_since"] = now
        if not want:
            _state["pump_since"] = None
        _state["pump"] = want
        return want


def set_mode(mode: str) -> None:
    with _lock:
        _state["mode"] = mode


def get_state() -> dict:
    with _lock:
        return dict(_state)


def record(device: str, moisture: float, raw: Optional[int], pump: bool,
           now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    conn = _connect()
    with conn:
        conn.execute("INSERT INTO readings VALUES (?,?,?,?,?)",
                     (now, device, moisture, raw, 1 if pump else 0))
    conn.close()


def snapshot(now: Optional[float] = None) -> dict:
    """Latest reading plus pump state, for the dashboard."""
    now = time.time() if now is None else now
    conn = _connect()
    row = conn.execute("SELECT ts, device, moisture FROM readings ORDER BY ts DESC LIMIT 1").fetchone()
    conn.close()
    st = get_state()
    if row is None:
        return {"available": False, "pump": st["pump"], "mode": st["mode"],
                "locked": now < st["locked_until"]}
    age = max(0, int(now - row[0]))
    return {
        "available": True, "device": row[1], "moisture": round(row[2], 1),
        "status": status_for(row[2]), "pump": st["pump"], "mode": st["mode"],
        "locked": now < st["locked_until"], "age_seconds": age, "fresh": age <= FRESH_SECONDS,
    }


def history(minutes: int = 60, max_points: int = 120, now: Optional[float] = None) -> list:
    now = time.time() if now is None else now
    conn = _connect()
    rows = conn.execute("SELECT ts, moisture FROM readings WHERE ts >= ? ORDER BY ts",
                        (now - minutes * 60,)).fetchall()
    conn.close()
    if len(rows) > max_points:
        step = len(rows) / float(max_points)
        rows = [rows[int(i * step)] for i in range(max_points)]
    return [[round(t, 1), round(m, 1)] for t, m in rows]
