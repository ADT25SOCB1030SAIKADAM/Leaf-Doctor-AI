"""Leaf Doctor backend: FastAPI + MobileNetV2 model. Serves the web/phone app and the JSON API.

Settings come from environment variables (all optional):
  ADMIN_KEY            secret needed for pump control and feedback summary (empty = open, for development)
  SENSOR_KEY           secret the soil-sensor device must send (see sensor.py)
  ALLOWED_ORIGINS      comma-separated list of other websites allowed to call the API (default: none)
  MAX_UPLOAD_MB        largest photo accepted (default 5)
  PREDICT_LIMIT_PER_MIN  photos per minute per visitor (default 20)
  EXCLUDE_CLASSES      class names the model must never answer with (default: dataset_small)
  TRUST_PROXY          set to 1 only when running behind your own proxy (uses X-Forwarded-For)
  ENABLE_DOCS          set to 0 to hide the /docs page in production
  DATA_DIR             where the small databases are stored (default: this folder)
"""
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

import numpy as np
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import feedback
import guard
from sensor import router as sensor_router

BASE = os.path.dirname(os.path.abspath(__file__))
FRONT = os.path.join(BASE, "frontend")

ADMIN_KEY = os.environ.get("ADMIN_KEY")
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
TRUST_PROXY = os.environ.get("TRUST_PROXY") == "1"
EXCLUDE = {c.strip() for c in os.environ.get("EXCLUDE_CLASSES", "dataset_small").split(",") if c.strip()}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("leafdoctor")

ADVICE = {
    "healthy": "No disease detected. Keep up regular watering and monitoring.",
    "blight": "Remove affected leaves, avoid overhead watering, improve airflow, and use a suitable fungicide if it spreads.",
    "rust": "Remove infected leaves, avoid wetting foliage, and apply a rust-labelled fungicide.",
    "mildew": "Increase air circulation, reduce humidity, and apply sulfur or a mildew-specific fungicide.",
    "spot": "Prune infected leaves, water at the base of the plant, and rotate crops next season.",
    "scab": "Clear fallen leaves, prune for airflow, and apply fungicide early in the season.",
    "rot": "Remove infected parts, improve drainage, and avoid over-watering.",
    "virus": "No cure. Remove and destroy infected plants and control insects such as aphids and whiteflies.",
    "mite": "Spray with water or neem oil/insecticidal soap and keep plants well watered.",
    "mold": "Reduce humidity, space plants out, and remove affected leaves.",
}
DEFAULT_ADVICE = "Isolate the plant, remove badly affected leaves, and consult a local agriculture expert."

STARTED = time.time()
MODEL_VERSION = "unknown"
_model, _classes = None, None
_load_lock = threading.Lock()
_infer_lock = threading.Lock()
predict_limiter = guard.RateLimiter(int(os.environ.get("PREDICT_LIMIT_PER_MIN", "20")), 60)
feedback_limiter = guard.RateLimiter(30, 60)


def load():
    """Load the model once (thread-safe). Raises HTTP 503 if the model files are missing."""
    global _model, _classes, MODEL_VERSION
    if _model is None:
        with _load_lock:
            if _model is None:
                mp, cp = os.path.join(BASE, "model.keras"), os.path.join(BASE, "class_names.json")
                if not (os.path.exists(mp) and os.path.exists(cp)):
                    raise HTTPException(503, "The model is not available yet. Please try again later.")
                import tensorflow as tf
                model = tf.keras.models.load_model(mp)
                with open(cp) as f:
                    _classes = json.load(f)
                _model = model
                info = os.path.join(BASE, "model_info.json")      # optional: {"version": "v1"}
                if os.path.exists(info):
                    with open(info) as f:
                        MODEL_VERSION = str(json.load(f).get("version", "unknown"))[:40]
                else:
                    MODEL_VERSION = time.strftime("%Y-%m-%d", time.gmtime(os.path.getmtime(mp)))
    return _model, _classes


@asynccontextmanager
async def lifespan(app):
    try:
        await run_in_threadpool(load)
        log.info("model loaded (version %s)", MODEL_VERSION)
    except HTTPException as e:
        log.warning("model not loaded: %s", e.detail)
    except Exception:
        log.exception("model failed to load")
    if not ADMIN_KEY:
        log.warning("ADMIN_KEY is not set: pump control is open to everyone (fine for development only)")
    yield


ENABLE_DOCS = os.environ.get("ENABLE_DOCS", "1") != "0"
app = FastAPI(title="Leaf Doctor API", version="1.0", lifespan=lifespan,
              docs_url="/docs" if ENABLE_DOCS else None, redoc_url=None,
              openapi_url="/openapi.json" if ENABLE_DOCS else None)

if ALLOWED_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type", "X-API-Key", "X-Admin-Key"])

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=()",
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                                "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
                                "base-uri 'self'; form-action 'self'"),
}


DOC_PATHS = ("/docs", "/openapi.json")


@app.middleware("http")
async def protect(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/predict":
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > guard.MAX_UPLOAD_BYTES + 65536:      # reject before reading the body
            return JSONResponse({"detail": "The photo is too large (maximum %g MB)." % (guard.MAX_UPLOAD_BYTES / 1048576)},
                                status_code=413, headers=SECURITY_HEADERS)
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        if name == "Content-Security-Policy" and request.url.path.startswith(DOC_PATHS):
            continue                      # the API docs page loads its own scripts
        response.headers.setdefault(name, value)
    return response


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    return guard.pick_ip(peer, request.headers.get("x-forwarded-for"), TRUST_PROXY)


def pretty(label):
    plant, _, disease = label.partition("___")
    return plant.replace("_", " ").strip(), (disease or "healthy").replace("_", " ").strip()


def advice_for(disease):
    d = disease.lower()
    for key, text in ADVICE.items():
        if key in d:
            return text
    return DEFAULT_ADVICE


def run_model(img, model, classes):
    """Runs in a worker thread so one slow photo never freezes the whole server."""
    arr = np.expand_dims(np.asarray(img.resize((224, 224)), dtype="float32"), 0)
    with _infer_lock:
        probs = np.array(model.predict(arr, verbose=0)[0], dtype="float64")
    for i, name in enumerate(classes):
        if name in EXCLUDE:
            probs[i] = 0.0
    total = float(probs.sum())
    if total <= 0:
        raise HTTPException(500, "The model could not produce a result for this photo.")
    probs = probs / total
    results = []
    for i in probs.argsort()[::-1][:3]:
        plant, disease = pretty(classes[int(i)])
        results.append({"plant": plant, "disease": disease, "confidence": round(float(probs[i]) * 100, 2)})
    best = results[0]
    best["advice"] = advice_for(best["disease"])
    second = results[1]["confidence"] if len(results) > 1 else 0.0
    return {"prediction": best, "top3": results, "low_confidence": guard.is_uncertain(best["confidence"], second),
            "model_version": MODEL_VERSION}


@app.get("/api/health")
def health():
    return {"status": "ok", "model_loaded": _model is not None, "model_version": MODEL_VERSION,
            "uptime_seconds": int(time.time() - STARTED)}


@app.get("/api/ready")
def ready():
    if _model is None:
        raise HTTPException(503, "Model not loaded")
    return {"status": "ready"}


@app.post("/predict")
async def predict(request: Request, file: UploadFile = File(...)):
    ip = client_ip(request)
    if not predict_limiter.allow(ip):
        raise HTTPException(429, "Too many photos in a short time. Please wait a minute and try again.",
                            headers={"Retry-After": "60"})
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "Please upload an image file (JPG, PNG or WebP).")
    data = await file.read(guard.MAX_UPLOAD_BYTES + 1)
    try:
        img = guard.open_image(data)
    except guard.UploadError as e:
        raise HTTPException(e.status, str(e))
    started = time.time()
    model, classes = await run_in_threadpool(load)
    result = await run_in_threadpool(run_model, img, model, classes)
    best = result["prediction"]
    log.info("predict visitor=%s result=%s/%s confidence=%.1f uncertain=%s ms=%d", guard.anonymise(ip), best["plant"],
             best["disease"], best["confidence"], result["low_confidence"], (time.time() - started) * 1000)
    return result


class FeedbackIn(BaseModel):
    plant: str = Field(..., max_length=80)
    disease: str = Field(..., max_length=80)
    confidence: float = Field(..., ge=0, le=100)
    correct: bool
    note: Optional[str] = Field(None, max_length=200)


@app.post("/api/feedback")
def post_feedback(body: FeedbackIn, request: Request):
    if not feedback_limiter.allow(client_ip(request)):
        raise HTTPException(429, "Too many requests. Please wait a minute.", headers={"Retry-After": "60"})
    feedback.record(body.plant, body.disease, body.confidence, body.correct, body.note, MODEL_VERSION)
    return {"ok": True}


@app.get("/api/feedback/summary")
def feedback_summary(x_admin_key: Optional[str] = Header(None)):
    if not guard.key_ok(x_admin_key, ADMIN_KEY):
        raise HTTPException(401, "Admin key required.")
    return feedback.summary()


app.include_router(sensor_router)   # soil sensor + pump API (/api/...)


@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(FRONT, "sw.js"), media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(FRONT, "manifest.webmanifest"), media_type="application/manifest+json")


# Serves index.html at "/" and the icons. Must come after the API routes.
app.mount("/", StaticFiles(directory=FRONT, html=True), name="site")
