#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quick E99 health check WITHOUT motors: network, latency, control, telemetry and video.

Arms nothing: only ping, heartbeat/CONTROL_OFF (keepalive) and a single frame grab.
Lets you know in seconds whether everything is healthy before flying, and infers the
battery level from latency (healthy link ~1-3 ms; hundreds/thousands as it drains).

Usage (PC on the drone Wi-Fi):
  python e99_check.py
  python e99_check.py --selftest     # does not touch the drone
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

import e99_protocol as p
import e99_snap          # reuse keepalive + grab_cmd (does not arm motors)

OK, NO = "[OK]  ", "[FAIL]"


def check_network():
    lat = p.ping_ms(p.DRONE_IP, 1500)
    if lat is None:
        print(NO, "Network: the drone does not answer ping. Is the adapter on the drone AP and the drone on?")
        return False, None
    print(OK, f"Network: drone reachable, latency {lat} ms")
    return True, lat


def check_telemetry(secs=4):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(0.3)
    addr = (p.DRONE_IP, p.CTRL_PORT); sent = rep = 0; last = None
    t0 = time.time()
    while time.time() - t0 < secs:
        s.sendto(p.HEARTBEAT, addr); sent += 1
        try:
            last = s.recvfrom(64)[0]; rep += 1
        except socket.timeout:
            pass
        time.sleep(0.1)
    s.close()
    if not last:
        print(NO, f"Control/telemetry: 0/{sent} replies. Port 7099 not responding.")
        return False
    st = p.parse_status(last)
    print(OK, f"Control/telemetry: {rep}/{sent} replies, device_id={st['device_id']}")
    return True


def check_video():
    ff = shutil.which("ffmpeg")
    if not ff:
        print(NO, "Video: ffmpeg not found on PATH.")
        return False
    out = os.path.join(tempfile.gettempdir(), "e99_check_frame.jpg")
    if os.path.exists(out):
        os.remove(out)
    stop = threading.Event()
    threading.Thread(target=e99_snap.keepalive, args=(stop,), daemon=True).start()
    time.sleep(1.5)
    try:
        subprocess.run(e99_snap.grab_cmd(ff, p.RTSP_URL, out),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    except subprocess.TimeoutExpired:
        pass
    finally:
        stop.set()
    if os.path.exists(out) and os.path.getsize(out) > 0:
        res = ""
        fp = shutil.which("ffprobe")
        if fp:
            r = subprocess.run([fp, "-v", "error", "-select_streams", "v:0",
                                "-show_entries", "stream=width,height", "-of", "csv=p=0", out],
                               capture_output=True, text=True)
            res = " " + r.stdout.strip()
        print(OK, f"Video: frame captured{res} ({os.path.getsize(out)} bytes)")
        return True
    print(NO, "Video: no frame (weak link or RTSP did not come up).")
    return False


def battery_hint(lat):
    if lat is None:
        return "No link: check the connection and the drone battery."
    if lat < 50:
        return "Excellent link -> healthy battery."
    if lat < 500:
        return "Degraded link -> half-charged battery or some 2.4GHz interference."
    return "High latency -> likely low battery; charge/swap before flying."


def run():
    print("== E99 quick check (no motors) ==")
    net_ok, lat = check_network()
    tel_ok = check_telemetry() if net_ok else False
    vid_ok = check_video() if net_ok else False
    print("-" * 40)
    print("Battery:", battery_hint(lat))
    all_ok = net_ok and tel_ok and vid_ok
    print("RESULT:", "ALL OK, ready to fly." if all_ok else "failures, see above.")
    return all_ok


def selftest():
    assert callable(p.ping_ms)
    cmd = e99_snap.grab_cmd("ffmpeg", p.RTSP_URL, "x.jpg")
    assert "udp" in cmd and cmd[-1] == "x.jpg"
    assert battery_hint(1).startswith("Excellent link")
    assert battery_hint(800).startswith("High latency")
    assert battery_hint(None).startswith("No link")
    print("self-test OK")


def main():
    ap = argparse.ArgumentParser(description="Quick E99 health check without motors.")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    sys.exit(0 if run() else 1)


if __name__ == "__main__":
    main()
