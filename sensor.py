"""API for the smart irrigation node (Arduino/ESP32 + soil moisture sensor + pump).

POST /api/sensor          device sends a reading, the server answers whether the pump should be ON
GET  /api/sensor/latest   latest reading and pump state (used by the Soil page)
GET  /api/sensor/history  recent readings for the chart
POST /api/pump            choose auto / on / off from the website (needs the admin key if one is set)

Keys (environment variables, optional while developing):
  SENSOR_KEY  the device must send it in the X-API-Key header
  ADMIN_KEY   the website must send it in the X-Admin-Key header to change the pump mode
"""
import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

import guard
import irrigation as irr

router = APIRouter(prefix="/api")

SENSOR_KEY = os.environ.get("SENSOR_KEY")
ADMIN_KEY = os.environ.get("ADMIN_KEY")
device_limiter = guard.RateLimiter(int(os.environ.get("SENSOR_LIMIT_PER_MIN", "240")), 60)


def require_sensor_key(x_api_key: Optional[str] = Header(None)):
    if not guard.key_ok(x_api_key, SENSOR_KEY):
        raise HTTPException(401, "Invalid or missing API key.")


def require_admin_key(x_admin_key: Optional[str] = Header(None)):
    if not guard.key_ok(x_admin_key, ADMIN_KEY):
        raise HTTPException(401, "Admin key required.")


class Reading(BaseModel):
    device: str = Field("esp32-1", max_length=40)
    moisture: float = Field(..., ge=0, le=100)   # percent
    raw: Optional[int] = None                    # raw ADC value, handy for calibration


class PumpMode(BaseModel):
    mode: str


@router.post("/sensor", dependencies=[Depends(require_sensor_key)])
def post_reading(r: Reading):
    if not device_limiter.allow(r.device):
        raise HTTPException(429, "Sending too fast.", headers={"Retry-After": "10"})
    now = time.time()
    pump = irr.decide(r.moisture, now)
    irr.record(r.device, r.moisture, r.raw, pump, now)
    return {"pump": pump, "status": irr.status_for(r.moisture), "mode": irr.get_state()["mode"]}


@router.get("/sensor/latest")
def latest():
    return irr.snapshot()


@router.get("/sensor/history")
def history(minutes: int = Query(60, ge=1, le=1440)):
    return {"points": irr.history(minutes)}


@router.post("/pump", dependencies=[Depends(require_admin_key)])
def set_pump(body: PumpMode):
    if body.mode not in ("auto", "on", "off"):
        raise HTTPException(400, "mode must be auto, on or off")
    irr.set_mode(body.mode)
    return irr.snapshot()
