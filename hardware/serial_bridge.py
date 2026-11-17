"""Bridge between an Arduino Uno (USB) and the Leaf Doctor website.

The Arduino prints lines like   M,41.3,512   (moisture %, raw value).
This program sends each reading to the server and sends the server's pump decision
back to the Arduino as   P,1  (ON)  or  P,0  (OFF).

Run (server must already be running):
    pip install pyserial
    python hardware/serial_bridge.py
    python hardware/serial_bridge.py --port /dev/cu.usbmodem1101    # if it cannot find the Arduino
    python hardware/serial_bridge.py --key YOUR_SENSOR_KEY          # if the server has SENSOR_KEY set

Close the Arduino IDE Serial Monitor first, because only one program can use the USB port.
Stop with Ctrl + C.
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request


def find_port():
    for pattern in ("/dev/cu.usbmodem*", "/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/ttyACM*", "/dev/ttyUSB*"):
        ports = glob.glob(pattern)
        if ports:
            return ports[0]
    return None


def post(url, payload, key=None):
    """Send one reading to the server. Returns the answer (a dict) or None."""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-API-Key"] = key
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        hint = " (check the key: --key or SENSOR_KEY)" if e.code == 401 else ""
        print("Server refused the reading: HTTP %s%s" % (e.code, hint))
        return None
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("Server not reachable: %s" % e)
        return None


def handle_line(line, ser, url, device, key=None):
    """Process one line from the Arduino."""
    if line.startswith("M,"):
        try:
            _, m, raw = line.split(",")
            moisture, raw = float(m), int(raw)
        except ValueError:
            print("Skipped a garbled line: %r" % line)
            return
        answer = post(url, {"device": device, "moisture": moisture, "raw": raw}, key)
        if answer is not None:
            ser.write(b"P,1\n" if answer.get("pump") else b"P,0\n")
            print("%5.1f %%   raw %4d   pump %-3s   mode %s" % (
                moisture, raw, "ON" if answer.get("pump") else "off", answer.get("mode")))
    elif line:
        print("Arduino:", line)


def run(ser, url, device, key=None):
    while True:
        line = ser.readline().decode(errors="ignore").strip()
        if line:
            handle_line(line, ser, url, device, key)


def main():
    ap = argparse.ArgumentParser(description="Arduino USB to Leaf Doctor bridge")
    ap.add_argument("--port", help="serial port, e.g. /dev/cu.usbmodem1101 (found automatically if left out)")
    ap.add_argument("--server", default="http://127.0.0.1:8000", help="Leaf Doctor server address")
    ap.add_argument("--device", default="arduino-uno", help="name shown for this sensor")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--key", default=os.environ.get("SENSOR_KEY"), help="sensor key if the server has SENSOR_KEY set")
    args = ap.parse_args()

    try:
        import serial
    except ImportError:
        sys.exit("pyserial is missing. Run:  pip install pyserial")

    url = args.server.rstrip("/") + "/api/sensor"
    while True:
        port = args.port or find_port()
        if not port:
            print("No Arduino found. Plug it in with the USB cable and close the Arduino Serial Monitor. Retrying...")
            time.sleep(3)
            continue
        try:
            print("Opening %s ..." % port)
            ser = serial.Serial(port, args.baud, timeout=2)
            time.sleep(2)                       # the Uno restarts when the port opens
            print("Connected. Sending readings to %s  (Ctrl + C to stop)" % url)
            run(ser, url, args.device, args.key)
        except serial.SerialException as e:
            print("Serial problem: %s. Retrying in 3 s..." % e)
            time.sleep(3)
        except KeyboardInterrupt:
            print("\nStopped.")
            return


if __name__ == "__main__":
    main()
