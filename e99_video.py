#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Live E99 view in the browser (RTSP -> HLS with ffmpeg + a local http.server).

The drone preview is RTSP (e99_protocol.RTSP_URL); browsers do not play RTSP natively.
This script uses ffmpeg to turn it into HLS, serves it on localhost and opens the browser
with hls.js. Requires the ffmpeg binary on the PATH (installs nothing via pip).

Note: HLS adds ~2-4 s of latency. For low-latency FPV use the web GUI (e99_gui.py),
which streams MJPEG instead. This script is a simple standalone viewer.

Usage (PC on the drone Wi-Fi):
  python e99_video.py                 # opens http://127.0.0.1:8099 with the video
  python e99_video.py --port 9000
  python e99_video.py --selftest

No-browser fallback (lower latency): ffplay -rtsp_transport udp <RTSP_URL>
"""
import argparse
import functools
import http.server
import shutil
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser

import e99_protocol as p

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>E99 live</title>
<style>html,body{margin:0;background:#111;height:100%}
video{width:100%;height:100%;object-fit:contain;background:#000}</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/hls.js/1.5.13/hls.min.js"></script>
</head><body>
<video id="v" autoplay muted playsinline controls></video>
<script>
var v=document.getElementById('v'), src='stream.m3u8';
if(window.Hls&&Hls.isSupported()){var h=new Hls({liveSyncDuration:1,lowLatencyMode:true});
h.loadSource(src);h.attachMedia(v);h.on(Hls.Events.ERROR,function(e,d){if(d.fatal)setTimeout(function(){h.loadSource(src);h.attachMedia(v);},1000);});}
else{v.src=src;}  // Safari: native HLS
</script></body></html>"""


def hls_cmd(ffmpeg, url, out_m3u8):
    """ffmpeg command: E99 RTSP (MJPEG, UDP transport) -> low-latency H.264 HLS."""
    return [ffmpeg, "-nostdin", "-rtsp_transport", "udp", "-max_delay", "500000", "-i", url, "-an",
            "-vf", "transpose=1",        # camera is portrait: rotate 90 deg clockwise
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-g", "15",
            "-f", "hls", "-hls_time", "1", "-hls_list_size", "3",
            "-hls_flags", "delete_segments+omit_endlist", out_m3u8]


def keepalive(stop_evt):
    """Keeps the drone RTSP alive without arming motors: CONTROL_OFF (08 01) at 50 ms.

    The E99 camera only streams while there is activity on port 7099; CONTROL_OFF
    means 'leave control mode', so it keeps the channel alive without spinning motors.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (p.DRONE_IP, p.CTRL_PORT)
    while not stop_evt.is_set():
        try:
            s.sendto(p.CONTROL_OFF, addr)
        except OSError:
            pass
        stop_evt.wait(0.05)
    s.close()


def serve(directory, port):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def selftest():
    cmd = hls_cmd("ffmpeg", p.RTSP_URL, "x.m3u8")
    assert cmd[0] == "ffmpeg" and p.RTSP_URL in cmd and cmd[-1] == "x.m3u8"
    assert "-rtsp_transport" in cmd and "udp" in cmd and "libx264" in cmd
    assert "stream.m3u8" in PAGE and "hls.js" in PAGE
    print("self-test OK")


def main():
    ap = argparse.ArgumentParser(description="Live E99 view in the browser.")
    ap.add_argument("--url", default=p.RTSP_URL)
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg not found on PATH. Install it (e.g. winget install Gyan.FFmpeg) "
              "or use the fallback: ffplay -rtsp_transport udp " + args.url, file=sys.stderr)
        sys.exit(1)
    work = tempfile.mkdtemp(prefix="e99_video_")
    m3u8 = work + "/stream.m3u8"
    with open(work + "/index.html", "w", encoding="utf-8") as f:
        f.write(PAGE)
    stop_evt = threading.Event()
    threading.Thread(target=keepalive, args=(stop_evt,), daemon=True).start()
    time.sleep(1.0)        # give the RTSP time to come up before ffmpeg
    print(f"Transcoding {args.url} -> HLS in {work}")
    ff = subprocess.Popen(hls_cmd(ffmpeg, args.url, m3u8),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    httpd = serve(work, args.port)
    url = f"http://127.0.0.1:{args.port}/index.html"
    print(f"Opening {url}  (Ctrl+C to stop). Expected latency ~2-4 s.")
    webbrowser.open(url)
    try:
        while ff.poll() is None:
            time.sleep(0.5)
        print("ffmpeg exited. Is the PC on the drone Wi-Fi and the stream active?",
              file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        stop_evt.set()
        ff.terminate()
        httpd.shutdown()
        print("\nStopped.")


if __name__ == "__main__":
    main()
