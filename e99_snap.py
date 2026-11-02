#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Take a photo from the E99 = grab a frame from the live stream (the drone has no storage).

The E99 does not save files (no SD slot); the app's "photo" is a snapshot of the video.
This does the same from the PC: it keeps the RTSP alive with CONTROL_OFF (no motors),
and grabs one frame with ffmpeg. Source: MJPEG 240x320, RTSP over UDP (verified on the bench).

Usage (PC on the drone Wi-Fi):
  python e99_snap.py                       # saves snap_YYYYmmdd_HHMMSS.jpg here
  python e99_snap.py --out D:\photos
  python e99_snap.py --selftest
Requires ffmpeg on the PATH.
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

import e99_protocol as p


def grab_cmd(ffmpeg, url, out_jpg):
    return [ffmpeg, "-nostdin", "-rtsp_transport", "udp", "-max_delay", "500000",
            "-i", url, "-vf", "transpose=1", "-frames:v", "1", "-y", out_jpg]


def keepalive(stop_evt):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (p.DRONE_IP, p.CTRL_PORT)
    while not stop_evt.is_set():
        try:
            s.sendto(p.CONTROL_OFF, addr)
        except OSError:
            pass
        stop_evt.wait(0.05)
    s.close()


def snap(url, out_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg not found on PATH.", file=sys.stderr)
        return False
    stop_evt = threading.Event()
    threading.Thread(target=keepalive, args=(stop_evt,), daemon=True).start()
    time.sleep(1.5)        # let the RTSP come up
    try:
        subprocess.run(grab_cmd(ffmpeg, url, out_path),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
    except subprocess.TimeoutExpired:
        pass
    finally:
        stop_evt.set()
    ok = os.path.exists(out_path) and os.path.getsize(out_path) > 0
    print(f"OK -> {out_path} ({os.path.getsize(out_path)} bytes)" if ok
          else "No frame captured. Is the PC on the drone Wi-Fi and the drone powered on?")
    return ok


def selftest():
    cmd = grab_cmd("ffmpeg", p.RTSP_URL, "x.jpg")
    assert cmd[0] == "ffmpeg" and "udp" in cmd and "-frames:v" in cmd and cmd[-1] == "x.jpg"
    print("self-test OK")


def main():
    ap = argparse.ArgumentParser(description="Grab a frame from the E99 stream.")
    ap.add_argument("--url", default=p.RTSP_URL)
    ap.add_argument("--out", default=".", help="destination folder or .jpg file")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    out = args.out
    if os.path.isdir(out) or out.endswith(("\\", "/")):
        os.makedirs(out, exist_ok=True)
        out = os.path.join(out, "snap_" + time.strftime("%Y%m%d_%H%M%S") + ".jpg")
    sys.exit(0 if snap(args.url, out) else 1)


if __name__ == "__main__":
    main()
