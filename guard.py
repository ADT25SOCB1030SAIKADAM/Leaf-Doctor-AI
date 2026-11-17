"""Request guards for the Leaf Doctor API: upload checks, rate limiting, key checks, 'not sure' rule.

No web-framework code here, so everything in this file can be tested on its own.
"""
import hashlib
import hmac
import io
import os
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from PIL import Image

MAX_UPLOAD_BYTES = int(float(os.environ.get("MAX_UPLOAD_MB", "5")) * 1024 * 1024)
MAX_PIXELS = 25_000_000          # refuse photos bigger than 25 megapixels
MIN_SIDE = 64                    # refuse tiny images
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}

CONFIDENCE_MIN = 60.0            # below this (%), the answer is "not sure"
MARGIN_MIN = 15.0                # if the top two answers are closer than this (%), "not sure"


class UploadError(ValueError):
    """A photo that cannot be used. `status` is the HTTP status code to return."""
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def open_image(data: bytes):
    """Check an uploaded photo and return it as an RGB PIL image. Raises UploadError."""
    if not data:
        raise UploadError("The file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError("The photo is too large (maximum %g MB)." % (MAX_UPLOAD_BYTES / 1048576), 413)
    try:
        img = Image.open(io.BytesIO(data))
        width, height = img.size             # read from the header, nothing is decoded yet
    except Exception:
        raise UploadError("That file could not be read as an image.")
    if img.format not in ALLOWED_FORMATS:
        raise UploadError("Please upload a JPG, PNG or WebP photo.")
    if width * height > MAX_PIXELS:
        raise UploadError("The photo is too large (more than 25 megapixels).", 413)
    if min(width, height) < MIN_SIDE:
        raise UploadError("The photo is too small. Please use a larger picture.")
    try:
        return img.convert("RGB")            # decodes the pixels; fails for damaged files
    except Exception:
        raise UploadError("That image file is damaged.")


class RateLimiter:
    """Allow at most `limit` calls per `window` seconds for each key (sliding window)."""
    def __init__(self, limit: int, window: float = 60.0):
        self.limit, self.window = limit, window
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 5000:       # keep memory bounded
                for k in [k for k, v in self._hits.items() if not v]:
                    del self._hits[k]
            return True


def key_ok(provided: Optional[str], expected: Optional[str]) -> bool:
    """True if no key is required (expected is empty) or the provided key matches."""
    if not expected:
        return True
    if not provided:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


def is_uncertain(top: float, second: float) -> bool:
    """Both numbers are percentages. Not sure if the best answer is weak or too close to the next."""
    return top < CONFIDENCE_MIN or (top - second) < MARGIN_MIN


def pick_ip(peer: str, forwarded: Optional[str] = None, trust_proxy: bool = False) -> str:
    """Client address for rate limiting. Only trust X-Forwarded-For behind your own proxy."""
    if trust_proxy and forwarded:
        return forwarded.split(",")[0].strip() or peer
    return peer or "unknown"


def anonymise(ip: str) -> str:
    """Short one-way hash, so logs never contain real addresses."""
    return hashlib.sha256(ip.encode()).hexdigest()[:10]
