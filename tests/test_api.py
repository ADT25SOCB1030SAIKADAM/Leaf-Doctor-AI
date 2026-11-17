"""API tests. The model is replaced by a tiny fake one, so TensorFlow is not needed.

Run:
    pip install pytest httpx
    python -m pytest -q
"""
import io
import os
import sys
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import guard  # noqa: E402
import irrigation as irr  # noqa: E402
import main  # noqa: E402
import sensor  # noqa: E402

CLASSES = ["Apple___healthy", "Tomato___Early_blight", "Potato___Late_blight", "dataset_small"]


class FakeModel:
    probs = [0.03, 0.90, 0.04, 0.03]

    def predict(self, x, verbose=0):
        return np.array([self.probs], dtype="float32")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(FakeModel, "probs", [0.03, 0.90, 0.04, 0.03])
    monkeypatch.setattr(main, "_model", FakeModel())
    monkeypatch.setattr(main, "_classes", CLASSES)
    monkeypatch.setattr(main, "predict_limiter", guard.RateLimiter(1000, 60))
    monkeypatch.setattr(main, "ADMIN_KEY", None)
    monkeypatch.setattr(sensor, "SENSOR_KEY", None)
    monkeypatch.setattr(sensor, "ADMIN_KEY", None)
    irr.set_mode("auto")
    with TestClient(main.app) as c:
        yield c


def photo(size=(200, 160), fmt="JPEG"):
    buf = io.BytesIO()
    Image.new("RGB", size, (60, 140, 70)).save(buf, fmt)
    return buf.getvalue()


def send(client, data, ctype="image/jpeg", name="leaf.jpg"):
    return client.post("/predict", files={"file": (name, data, ctype)})


# ---------------------------------------------------------------- health and security headers
def test_health_and_ready(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["model_loaded"] is True
    assert client.get("/api/ready").status_code == 200


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]


# ---------------------------------------------------------------- /predict
def test_predict_ok(client):
    r = send(client, photo())
    assert r.status_code == 200
    body = r.json()
    assert body["prediction"]["plant"] == "Tomato" and body["prediction"]["disease"] == "Early blight"
    assert body["prediction"]["confidence"] > 90 and body["low_confidence"] is False
    assert len(body["top3"]) == 3 and "advice" in body["prediction"]


def test_predict_never_answers_with_stray_class(client, monkeypatch):
    monkeypatch.setattr(FakeModel, "probs", [0.10, 0.20, 0.10, 0.60])   # the stray class is on top
    body = send(client, photo()).json()
    assert body["prediction"]["plant"] == "Tomato"
    assert body["low_confidence"] is True                                  # 50 % is not convincing


def test_predict_png_and_webp_accepted(client):
    assert send(client, photo(fmt="PNG"), "image/png", "a.png").status_code == 200
    assert send(client, photo(fmt="WEBP"), "image/webp", "a.webp").status_code == 200


def test_predict_rejects_non_image(client):
    assert send(client, b"hello", "text/plain", "a.txt").status_code == 400
    assert send(client, b"not really a jpeg", "image/jpeg").status_code == 400


def test_predict_rejects_too_large(client, monkeypatch):
    monkeypatch.setattr(guard, "MAX_UPLOAD_BYTES", 500)
    buf = io.BytesIO()
    Image.frombytes("RGB", (100, 100), os.urandom(30000)).save(buf, "PNG")
    assert send(client, buf.getvalue(), "image/png", "big.png").status_code == 413


def test_predict_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "predict_limiter", guard.RateLimiter(2, 60))
    codes = [send(client, photo()).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ---------------------------------------------------------------- feedback
def test_feedback_flow(client, monkeypatch):
    r = client.post("/api/feedback", json={"plant": "Tomato", "disease": "Early blight", "confidence": 90.5, "correct": False})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert client.post("/api/feedback", json={"plant": "Tomato"}).status_code == 422
    monkeypatch.setattr(main, "ADMIN_KEY", "adminkey")
    assert client.get("/api/feedback/summary").status_code == 401
    s = client.get("/api/feedback/summary", headers={"X-Admin-Key": "adminkey"}).json()
    assert s["total"] >= 1


# ---------------------------------------------------------------- soil sensor and pump
def test_sensor_open_in_development(client):
    r = client.post("/api/sensor", json={"device": "t1", "moisture": 20.0, "raw": 700})
    assert r.status_code == 200 and r.json()["pump"] is True
    latest = client.get("/api/sensor/latest").json()
    assert latest["available"] is True and latest["moisture"] == 20.0
    assert len(client.get("/api/sensor/history?minutes=5").json()["points"]) >= 1


def test_sensor_validation(client):
    assert client.post("/api/sensor", json={"device": "t1", "moisture": 150}).status_code == 422
    assert client.post("/api/sensor", json={"device": "t1"}).status_code == 422


def test_sensor_key_required(client, monkeypatch):
    monkeypatch.setattr(sensor, "SENSOR_KEY", "devkey")
    body = {"device": "t2", "moisture": 50.0}
    assert client.post("/api/sensor", json=body).status_code == 401
    assert client.post("/api/sensor", json=body, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post("/api/sensor", json=body, headers={"X-API-Key": "devkey"}).status_code == 200


def test_pump_needs_admin_key(client, monkeypatch):
    monkeypatch.setattr(sensor, "ADMIN_KEY", "adminkey")
    assert client.post("/api/pump", json={"mode": "off"}).status_code == 401
    r = client.post("/api/pump", json={"mode": "off"}, headers={"X-Admin-Key": "adminkey"})
    assert r.status_code == 200 and r.json()["mode"] == "off"
    assert client.post("/api/pump", json={"mode": "banana"}, headers={"X-Admin-Key": "adminkey"}).status_code == 400
    irr.set_mode("auto")


# ---------------------------------------------------------------- the website itself
def test_site_is_served(client):
    assert client.get("/").status_code == 200
    assert "Leaf Doctor" in client.get("/").text
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/sw.js").status_code == 200
