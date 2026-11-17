"""Tests for the parts that do not need a web server: guard.py, feedback.py, irrigation.py.
Run:  python -m pytest -q      (or)      python -m unittest discover -s tests -p "test_logic.py"
"""
import io
import os
import sys
import tempfile
import unittest

os.environ["DATA_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import feedback  # noqa: E402
import guard  # noqa: E402
import irrigation as irr  # noqa: E402


def image_bytes(size=(200, 160), fmt="JPEG", color=(60, 140, 70)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


class UploadChecks(unittest.TestCase):
    def test_valid_jpeg_png_webp(self):
        for fmt in ("JPEG", "PNG", "WEBP"):
            self.assertEqual(guard.open_image(image_bytes(fmt=fmt)).mode, "RGB")

    def test_empty_and_not_an_image(self):
        for bad in (b"", b"hello world, not a picture"):
            with self.assertRaises(guard.UploadError) as c:
                guard.open_image(bad)
            self.assertEqual(c.exception.status, 400)

    def test_wrong_format_gif_rejected(self):
        with self.assertRaises(guard.UploadError):
            guard.open_image(image_bytes(fmt="GIF"))

    def test_too_small_rejected(self):
        with self.assertRaises(guard.UploadError):
            guard.open_image(image_bytes(size=(30, 30)))

    def test_too_many_bytes_gives_413(self):
        old = guard.MAX_UPLOAD_BYTES
        guard.MAX_UPLOAD_BYTES = 1000
        try:
            with self.assertRaises(guard.UploadError) as c:
                guard.open_image(image_bytes(size=(600, 600), color=(1, 2, 3)) + b"x" * 2000)
            self.assertEqual(c.exception.status, 413)
        finally:
            guard.MAX_UPLOAD_BYTES = old

    def test_too_many_pixels_gives_413_without_decoding(self):
        old = guard.MAX_PIXELS
        guard.MAX_PIXELS = 10_000
        try:
            with self.assertRaises(guard.UploadError) as c:
                guard.open_image(image_bytes(size=(300, 300)))
            self.assertEqual(c.exception.status, 413)
        finally:
            guard.MAX_PIXELS = old

    def test_truncated_file_rejected(self):
        with self.assertRaises(guard.UploadError):
            guard.open_image(image_bytes(size=(400, 400))[:300])


class RateLimiting(unittest.TestCase):
    def test_blocks_after_limit_and_recovers(self):
        rl = guard.RateLimiter(3, 60)
        self.assertTrue(all(rl.allow("a", now=t) for t in (0, 1, 2)))
        self.assertFalse(rl.allow("a", now=3))
        self.assertTrue(rl.allow("b", now=3))          # other users are not affected
        self.assertTrue(rl.allow("a", now=61))         # window has passed


class Keys(unittest.TestCase):
    def test_key_rules(self):
        self.assertTrue(guard.key_ok(None, None))      # no key configured = open (development)
        self.assertTrue(guard.key_ok("anything", ""))
        self.assertFalse(guard.key_ok(None, "secret"))
        self.assertFalse(guard.key_ok("wrong", "secret"))
        self.assertTrue(guard.key_ok("secret", "secret"))


class NotSure(unittest.TestCase):
    def test_rules(self):
        self.assertTrue(guard.is_uncertain(55, 10))    # weak
        self.assertTrue(guard.is_uncertain(70, 62))    # too close to second
        self.assertFalse(guard.is_uncertain(91, 6))    # clear winner

    def test_ip_helpers(self):
        self.assertEqual(guard.pick_ip("1.1.1.1", "9.9.9.9, 2.2.2.2", trust_proxy=False), "1.1.1.1")
        self.assertEqual(guard.pick_ip("1.1.1.1", "9.9.9.9, 2.2.2.2", trust_proxy=True), "9.9.9.9")
        self.assertNotIn("1.1.1.1", guard.anonymise("1.1.1.1"))


class Feedback(unittest.TestCase):
    def setUp(self):
        feedback.DB_PATH = os.path.join(tempfile.mkdtemp(), "feedback.db")   # fresh database for every test

    def test_record_and_summary(self):
        feedback.record("Tomato", "Early blight", 91.4, True)
        feedback.record("Tomato", "Early blight", 52.0, False, "looked like target spot\x00\x07")
        feedback.record("Apple", "healthy", 99.0, True, "x" * 500)
        s = feedback.summary()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["marked_correct"], 2)
        self.assertEqual(s["percent_correct"], 66.7)
        self.assertEqual(s["most_reported_wrong"][0]["disease"], "Early blight")

    def test_note_is_cleaned_and_limited(self):
        import sqlite3
        feedback.record("A", "B", 1.0, False, "bad\x00note" + "y" * 400)
        row = sqlite3.connect(feedback.DB_PATH).execute("SELECT note FROM feedback ORDER BY ts DESC LIMIT 1").fetchone()
        self.assertEqual(len(row[0]), 200)
        self.assertNotIn("\x00", row[0])


class Irrigation(unittest.TestCase):
    def setUp(self):
        irr.DB_PATH = os.path.join(tempfile.mkdtemp(), "irrigation.db")
        irr.set_mode("auto")
        irr._state.update(pump=False, pump_since=None, locked_until=0.0)

    def test_hysteresis(self):
        self.assertTrue(irr.decide(20, 1000.0))
        self.assertTrue(irr.decide(45, 1005.0))        # in between: keeps running
        self.assertFalse(irr.decide(60, 1010.0))
        self.assertFalse(irr.decide(45, 1015.0))       # in between: stays off

    def test_safety_cutoff_and_rest(self):
        irr.decide(10, 2000.0)
        self.assertTrue(irr.decide(10, 2050.0))
        self.assertFalse(irr.decide(10, 2070.0))       # more than 60 s
        self.assertFalse(irr.decide(5, 2100.0))        # resting
        self.assertTrue(irr.decide(5, 2700.0))         # rest is over

    def test_manual_modes_and_status(self):
        irr.set_mode("off")
        self.assertFalse(irr.decide(5, 3000.0))
        irr.set_mode("on")
        self.assertTrue(irr.decide(90, 3010.0))
        self.assertEqual([irr.status_for(10), irr.status_for(50), irr.status_for(85)], ["dry", "good", "wet"])

    def test_snapshot_freshness(self):
        irr.record("t", 40.0, 500, False, 5000.0)
        self.assertTrue(irr.snapshot(5050.0)["fresh"])
        self.assertFalse(irr.snapshot(5300.0)["fresh"])


if __name__ == "__main__":
    unittest.main()
