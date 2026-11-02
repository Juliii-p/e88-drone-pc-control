#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web interface to fly the E99 from the PC with the keyboard.

Python backend (stdlib + ffmpeg, no extra deps) that:
  - sends UDP control at 50 ms and keeps the video alive (CONTROL_OFF keepalive),
  - turns the RTSP (MJPEG/UDP) into a low-latency MJPEG stream for the browser,
  - serves a UI: video in the center, controls on the left, debug on the right.
The browser captures the keyboard and tells the backend which keys are held.

Usage (PC on the drone Wi-Fi):
  python e99_gui.py                  # opens http://127.0.0.1:8099
  python e99_gui.py --port 9000
  python e99_gui.py --selftest       # checks the logic (does not touch the drone)

Keys: W/S throttle (128=hover, up/down; release=hover) | I/K fwd-back | J/L left-right | <-/-> yaw
      T arm (no takeoff) | G TAKEOFF (to hover) | X HOVER (level) | Y land | SPACE STOP
      F flip | H headless | C gyro calibrate | V switch camera (1<->2)
Starts DISARMED. Requires ffmpeg on the PATH. Bench only, props OFF.
"""
import argparse
import http.server
import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse

import e99_protocol as p

STEP = 90                # roll/pitch/yaw offset
THR_STEP = 60            # throttle offset (altitude-hold drone: 128=hover, +=up, -=down). Tune here.
TICK = 0.05
WATCHDOG_S = 1.5          # if the client goes silent while armed -> disarm
FLAGNAMES = [(p.TAKEOFF, "TAKEOFF"), (p.LAND, "LAND"), (p.EMERGENCY, "EMERGENCY"),
             (p.FLIP, "FLIP"), (p.HEADLESS, "HEADLESS"), (p.CALIBRATE, "CALIBRATE")]


class Backend:
    def __init__(self, ip, port):
        self.addr = (ip, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.0)        # non-blocking (send and recv)
        self.pressed = set()
        self.armed = False
        self.headless = False
        self.camera_second = False
        self.flags_until = {}
        self.stop_latched = False        # failsafe: continuous EMERGENCY until re-armed
        self.last_client = time.monotonic()
        self.last_sent = b""
        self.last_telem = b""
        self.sent_count = 0
        self.rate = 0.0
        self.reply_count = 0
        self.reply_rate = 0.0
        self.latency_ms = None
        self.last_frame = b""        # latest JPEG from the stream (filled by video_loop)
        self.running = True

    # --- state -> packet ---
    def axes(self):
        C, pr = p.CENTER, self.pressed
        thr = C + (THR_STEP if "w" in pr else 0) - (THR_STEP if "s" in pr else 0)   # 128=hover
        pit = C + (STEP if "i" in pr else 0) - (STEP if "k" in pr else 0)
        rol = C + (STEP if "l" in pr else 0) - (STEP if "j" in pr else 0)
        yaw = C + (STEP if "right" in pr else 0) - (STEP if "left" in pr else 0)
        return rol, pit, thr, yaw

    def flags(self, now):
        f = p.HEADLESS if self.headless else 0
        for bit, until in list(self.flags_until.items()):
            if now <= until:
                f |= bit
            else:
                del self.flags_until[bit]
        return f

    def build(self, now):
        if self.stop_latched:
            return p.control(throttle=0, flags=p.EMERGENCY)     # LATCHED full stop
        if not self.armed:
            return p.CONTROL_OFF                                 # keepalive, no motors
        rol, pit, thr, yaw = self.axes()
        return p.control(rol, pit, thr, yaw, self.flags(now))

    # --- client commands ---
    def touch(self):
        self.last_client = time.monotonic()

    def key(self, k, down):
        self.touch()
        k = k.lower()
        now = time.monotonic()
        if down:
            if k in ("w", "s", "i", "k", "j", "l", "left", "right"):
                self.pressed.add(k)
            elif k == "t":
                self.armed = True; self.stop_latched = False   # ARM (no takeoff; motors stay idle)
            elif k == "g":
                if self.armed:                                  # TAKEOFF: the drone needs this command
                    self.flags_until[p.TAKEOFF] = now + 1.0     # to engage motors and climb to hover
            elif k == "y":
                self.flags_until[p.LAND] = now + 1.0
            elif k in (" ", "space"):
                if self.stop_latched:
                    self.stop_latched = False    # 2nd STOP: cut the EMERGENCY beacon -> keepalive
                else:
                    self.disarm()                # 1st STOP: latched continuous EMERGENCY
            elif k == "f":
                self.flags_until[p.FLIP] = now + 0.6
            elif k == "c":
                self.flags_until[p.CALIBRATE] = now + 2.0
            elif k == "h":
                self.headless = not self.headless
            elif k == "v":
                self.switch_camera()
            elif k == "x":
                self.pressed.clear()        # hover: sticks to neutral, stays armed and at altitude
        else:
            self.pressed.discard(k)

    def disarm(self):
        self.armed = False
        self.stop_latched = True         # keeps sending EMERGENCY until re-armed with T
        self.pressed.clear()

    def switch_camera(self):
        self.camera_second = not self.camera_second
        try:
            self.sock.sendto(p.switch_camera(self.camera_second), self.addr)
        except OSError:
            pass

    def snapshot(self):
        now = time.monotonic()
        s = self.last_sent
        hexs = s.hex()
        mode = "disarm" if self.stop_latched else ("control" if self.armed else "keepalive")
        dec = p.decode_control(s) if len(s) == 9 else None
        flags_on = []
        if dec:
            flags_on = [n for b, n in FLAGNAMES if dec["flags"] & b]
        tel = self.last_telem
        return {
            "armed": self.armed, "mode": mode, "camera": 2 if self.camera_second else 1,
            "headless": self.headless, "rate": round(self.rate, 1),
            "latency_ms": self.latency_ms, "reply_rate": round(self.reply_rate, 1),
            "throttle": dec["throttle"] if dec else p.CENTER,
            "sent_hex": hexs, "sent": dec, "flags": flags_on,
            "pressed": sorted(self.pressed),
            "telem_hex": tel.hex() if tel else "",
            "telem": p.parse_status(tel) if tel else None,
            "stale": now - self.last_client > WATCHDOG_S,
        }

    def ping_loop(self):
        while self.running:
            self.latency_ms = p.ping_ms(self.addr[0], 1000)
            time.sleep(1.5)

    # --- control loop at 50 ms ---
    def loop(self):
        t_rate = time.monotonic(); c0 = 0; r0 = 0
        while self.running:
            now = time.monotonic()
            if self.armed and now - self.last_client > WATCHDOG_S:
                self.disarm()                     # failsafe: client lost
            pkt = self.build(now)
            try:
                self.sock.sendto(pkt, self.addr)
            except OSError:
                pass
            self.last_sent = pkt; self.sent_count += 1
            try:
                while True:
                    d, _ = self.sock.recvfrom(64); self.last_telem = d; self.reply_count += 1
            except (BlockingIOError, OSError):
                pass
            if now - t_rate >= 1.0:
                self.rate = (self.sent_count - c0) / (now - t_rate)
                self.reply_rate = (self.reply_count - r0) / (now - t_rate)
                c0 = self.sent_count; r0 = self.reply_count; t_rate = now
            time.sleep(TICK)


# ---- ffmpeg RTSP(MJPEG/UDP) -> JPEGs on a pipe: low latency (live MJPEG to the browser) ----
def mjpeg_cmd(ffmpeg, url):
    return [ffmpeg, "-nostdin", "-fflags", "nobuffer", "-flags", "low_delay",
            "-rtsp_transport", "udp", "-i", url, "-an",
            "-vf", "transpose=1",                    # camera is portrait: rotate 90 deg clockwise
            "-q:v", "6", "-f", "image2pipe", "-vcodec", "mjpeg", "-"]


def video_loop(backend, ffmpeg, url):
    """Runs ffmpeg, splits the stream into JPEGs and stores the latest frame. Reconnects if it dies."""
    while backend.running:
        proc = subprocess.Popen(mjpeg_cmd(ffmpeg, url),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        buf = b""
        while backend.running:
            chunk = proc.stdout.read(8192)
            if not chunk:
                break
            buf += chunk
            while True:
                a = buf.find(b"\xff\xd8")            # JPEG start (SOI)
                b2 = buf.find(b"\xff\xd9", a + 2)     # end (EOI)
                if a != -1 and b2 != -1:
                    backend.last_frame = buf[a:b2 + 2]
                    buf = buf[b2 + 2:]
                else:
                    if a > 0:
                        buf = buf[a:]                 # drop junk before the SOI
                    break
        try:
            proc.terminate()
        except OSError:
            pass
        if backend.running:
            time.sleep(1.0)                           # died (signal/camera switch): retry


def make_handler(backend):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass                   # quiet

        def do_GET(self):
            backend.touch()
            if self.path.startswith("/state"):
                self._send(json.dumps(backend.snapshot()).encode(), "application/json")
            elif self.path.startswith("/stream.mjpg"):
                self._stream()
            else:
                self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")

        def _send(self, body, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            last = None
            try:
                while backend.running:
                    f = backend.last_frame
                    if f and f is not last:
                        last = f
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(f)).encode() + b"\r\n\r\n" + f + b"\r\n")
                    else:
                        time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            data = urllib.parse.parse_qs(self.rfile.read(n).decode())
            if self.path.startswith("/key"):
                backend.key(data.get("key", [""])[0], data.get("down", ["0"])[0] == "1")
            self.send_response(204)
            self.end_headers()
    return H


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>E99 Control</title>
<style>
:root{--bg:#0f1115;--pan:#171a21;--ln:#262b36;--tx:#dfe3ea;--mut:#8b93a3;--ok:#39d98a;--warn:#f5a623;--bad:#ff5566;--ac:#4aa8ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.4 system-ui,Segoe UI,sans-serif;height:100vh;display:flex;flex-direction:column}
header{display:flex;gap:16px;align-items:center;padding:8px 14px;background:var(--pan);border-bottom:1px solid var(--ln)}
header h1{font-size:15px;margin:0;font-weight:600}
.badge{padding:3px 10px;border-radius:6px;font-weight:700;font-size:12px;letter-spacing:.3px}
.main{flex:1;display:flex;min-height:0}
.col{padding:12px;overflow:auto}
.left{width:230px;border-right:1px solid var(--ln)}
.center{flex:1;display:flex;align-items:center;justify-content:center;background:#000;min-width:0}
.center img{max-width:100%;max-height:100%;background:#000}
.right{width:310px;border-left:1px solid var(--ln);background:var(--pan)}
h2{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:var(--mut);margin:14px 0 8px}
.pad{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:8px}
.key{background:#1d2129;border:1px solid var(--ln);border-radius:7px;padding:10px 4px;text-align:center;font-weight:600;font-size:13px;user-select:none}
.key small{display:block;color:var(--mut);font-weight:400;font-size:10px;margin-top:2px}
.key.active{background:var(--ac);color:#06101c;border-color:var(--ac)}
.key.sp{grid-column:span 3}
.btn{width:100%;margin:4px 0;padding:10px;border:1px solid var(--ln);background:#1d2129;color:var(--tx);border-radius:7px;font-weight:600;cursor:pointer}
.btn.stop{background:var(--bad);color:#210;border-color:var(--bad);font-size:15px;padding:13px}
.btn.arm{background:var(--ok);color:#042;border-color:var(--ok)}
.row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--ln)}
.row b{color:var(--mut);font-weight:500}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px;word-break:break-all;color:var(--ac)}
.pill{font-size:11px;padding:2px 7px;border-radius:5px;background:#222835;margin:2px 4px 0 0;display:inline-block}
.hint{color:var(--mut);font-size:11px;margin-top:6px}
</style></head><body>
<header>
  <h1>E99 Control</h1>
  <span id="st" class="badge" style="background:#333">DISARMED</span>
  <span id="cam" class="badge" style="background:#222835">CAM 1</span>
  <span id="lat" class="badge" style="background:#222835">— ms</span>
  <span id="rate" class="badge" style="background:#222835">0 pkt/s</span>
  <span id="warn" class="badge" style="background:var(--warn);color:#210;display:none">NO FOCUS</span>
</header>
<div class="main">
  <div class="col left">
    <h2>Controls</h2>
    <div class="pad">
      <div></div><div class="key" data-k="w">W<small>throttle+</small></div><div></div>
      <div class="key" data-k="j">J<small>left</small></div><div class="key" data-k="s">S<small>throttle-</small></div><div class="key" data-k="l">L<small>right</small></div>
      <div class="key" data-k="i">I<small>forward</small></div><div class="key" data-k="k">K<small>back</small></div><div class="key" data-k="left">&larr;<small>yaw L</small></div>
      <div class="key" data-k="right">&rarr;<small>yaw R</small></div>
    </div>
    <button class="btn arm" data-tap="t">T &middot; Arm (no takeoff)</button>
    <button class="btn" data-tap="g">G &middot; Takeoff (to hover)</button>
    <h2>Throttle</h2>
    <div style="background:#1d2129;border:1px solid var(--ln);border-radius:6px;height:16px;overflow:hidden">
      <div id="thr_bar" style="height:100%;width:0;background:var(--ac);transition:width .08s"></div>
    </div>
    <div class="hint"><span id="thr_val">128</span>/255 &middot; 128=hover &middot; W up / S down (release = hover)</div>
    <button class="btn" data-tap="x">X &middot; HOVER (level, keep throttle)</button>
    <button class="btn" data-tap="y">Y &middot; Land</button>
    <button class="btn" data-tap="f">F &middot; Flip</button>
    <button class="btn" data-tap="h">H &middot; Headless</button>
    <button class="btn" data-tap="c">C &middot; Gyro calibrate</button>
    <button class="btn" data-tap="v">V &middot; Switch camera</button>
    <button class="btn stop" data-tap=" ">SPACE &middot; STOP</button>
    <div class="hint">Props OFF. Click the page to focus the keyboard.</div>
  </div>
  <div class="center"><img id="v" src="stream.mjpg" alt="drone video"></div>
  <div class="col right">
    <h2>Debug</h2>
    <div class="row"><b>State</b><span id="d_mode">—</span></div>
    <div class="row"><b>Camera</b><span id="d_cam">1</span></div>
    <div class="row"><b>Latency</b><span id="d_lat">—</span></div>
    <div class="row"><b>Telemetry/s</b><span id="d_rrate">0</span></div>
    <div class="row"><b>Packets/s</b><span id="d_rate">0</span></div>
    <div class="hint" id="d_bat"></div>
    <h2>Sent packet</h2>
    <div class="mono" id="d_hex">—</div>
    <div id="d_dec" style="margin-top:6px"></div>
    <div id="d_flags" style="margin-top:4px"></div>
    <h2>Drone info</h2>
    <div class="mono" id="d_thex">—</div>
    <div id="d_tel" style="margin-top:6px"></div>
    <div class="hint" id="d_note"></div>
  </div>
</div>
<script>
// ---- MJPEG video: reload the <img> if it drops ----
var v=document.getElementById('v');
v.onerror=function(){setTimeout(function(){v.src='stream.mjpg?'+Date.now();},1500);};

// ---- keyboard -> backend ----
var MAP={'w':'w','s':'s','i':'i','k':'k','j':'j','l':'l','arrowleft':'left','arrowright':'right',
         't':'t','g':'g','x':'x','y':'y','f':'f','h':'h','c':'c','v':'v',' ':' '};
var localPressed={};
function send(k,down){fetch('/key',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
  body:'key='+encodeURIComponent(k)+'&down='+(down?1:0)});}
function norm(e){var k=e.key.toLowerCase();return MAP[k];}
document.addEventListener('keydown',function(e){var k=norm(e);if(!k)return;e.preventDefault();
  if(localPressed[k])return;localPressed[k]=1;highlight(k,true);send(k,true);});
document.addEventListener('keyup',function(e){var k=norm(e);if(!k)return;e.preventDefault();
  delete localPressed[k];highlight(k,false);send(k,false);});
window.addEventListener('blur',function(){for(var k in localPressed){highlight(k,false);send(k,false);}localPressed={};});
function highlight(k,on){var el=document.querySelector('.key[data-k="'+k+'"]');if(el)el.classList.toggle('active',on);}
// buttons (tap): down and up
document.querySelectorAll('[data-tap]').forEach(function(b){
  b.addEventListener('click',function(){var k=b.getAttribute('data-tap');send(k,true);setTimeout(function(){send(k,false);},60);});
});

// ---- state poll ----
function flagPills(a){return (a&&a.length)?a.map(function(f){return '<span class="pill">'+f+'</span>';}).join(''):'<span class="hint">none</span>';}
async function poll(){
  try{
    var r=await fetch('/state');var s=await r.json();
    var st=document.getElementById('st');
    if(s.mode==='disarm'){st.textContent='EMERGENCY ●';st.style.background='var(--bad)';st.style.color='#210';}
    else if(s.armed){st.textContent='ARMED';st.style.background='var(--ok)';st.style.color='#042';}
    else{st.textContent='DISARMED';st.style.background='#333';st.style.color='#fff';}
    document.getElementById('d_note').textContent = (s.mode==='disarm')
      ? 'Continuous EMERGENCY. SPACE again stops the beacon · T re-arms.' : '';
    document.getElementById('cam').textContent='CAM '+s.camera;
    document.getElementById('rate').textContent=s.rate+' pkt/s';
    // latency (useful to infer the drone battery)
    var lat=s.latency_ms, latTxt=(lat==null)?'no reply':(lat+' ms');
    var le=document.getElementById('lat'); le.textContent=latTxt;
    var col=(lat==null)?'var(--bad)':(lat<50?'var(--ok)':(lat<500?'var(--warn)':'var(--bad)'));
    le.style.background=col; le.style.color=(lat!=null&&lat<50)?'#042':'#210';
    document.getElementById('d_lat').textContent=latTxt;
    document.getElementById('d_rrate').textContent=s.reply_rate;
    var thr=s.throttle||0;
    document.getElementById('thr_bar').style.width=Math.round(thr/255*100)+'%';
    document.getElementById('thr_val').textContent=thr;
    document.getElementById('d_bat').textContent=(lat!=null&&lat>=500)?'High latency -> likely low drone battery.':((lat==null)?'No reply -> check link/battery.':'');
    document.getElementById('warn').style.display=s.stale?'inline-block':'none';
    document.getElementById('d_mode').textContent=s.mode+(s.headless?' · headless':'');
    document.getElementById('d_cam').textContent=s.camera;
    document.getElementById('d_rate').textContent=s.rate;
    document.getElementById('d_hex').textContent=s.sent_hex||'—';
    var dd=document.getElementById('d_dec');
    if(s.sent){dd.innerHTML='roll <b>'+s.sent.roll+'</b> · pitch <b>'+s.sent.pitch+'</b> · thr <b>'+s.sent.throttle+'</b> · yaw <b>'+s.sent.yaw+'</b>';}
    else{dd.innerHTML='<span class="hint">CONTROL_OFF (keepalive, motors off)</span>';}
    document.getElementById('d_flags').innerHTML=flagPills(s.flags);
    document.getElementById('d_thex').textContent=s.telem_hex||'—';
    document.getElementById('d_tel').innerHTML=s.telem?('device_id <b>'+s.telem.device_id+'</b> · cam_reset <b>'+s.telem.camera_reset+'</b>'):'<span class="hint">no reply from drone</span>';
  }catch(e){}
  setTimeout(poll,150);
}
poll();
</script></body></html>"""


def selftest():
    b = Backend(p.DRONE_IP, p.CTRL_PORT)
    now = time.monotonic()
    assert b.build(now) == p.CONTROL_OFF                      # starts disarmed
    b.key("t", True)                                          # arm WITHOUT takeoff; rest = hover (128)
    d = p.decode_control(b.build(time.monotonic()))
    assert b.armed and not (d["flags"] & p.TAKEOFF) and d["throttle"] == p.CENTER
    b.key("w", True)                                          # up
    d = p.decode_control(b.build(time.monotonic()))
    assert d["throttle"] > p.CENTER and d["roll"] == p.CENTER
    b.key("w", False)                                         # release -> back to hover
    assert p.decode_control(b.build(time.monotonic()))["throttle"] == p.CENTER
    b.key("s", True)                                          # down
    assert p.decode_control(b.build(time.monotonic()))["throttle"] < p.CENTER
    b.key("s", False)
    b.key("g", True)                                          # TAKEOFF sends TAKEOFF (armed only)
    assert p.decode_control(b.build(time.monotonic()))["flags"] & p.TAKEOFF
    b.flags_until.clear()
    b.key(" ", True)                                          # STOP
    d = p.decode_control(b.build(time.monotonic()))
    assert not b.armed and b.stop_latched and d["flags"] & p.EMERGENCY and d["throttle"] == 0
    # LATCHED: keeps sending EMERGENCY afterwards, does not revert to keepalive
    assert p.decode_control(b.build(time.monotonic()))["flags"] & p.EMERGENCY
    # re-arming with T clears the latch
    b.key("t", True)
    assert b.armed and not b.stop_latched
    # watchdog: losing the client leaves the stop latched (same decision as the loop)
    b.last_client = time.monotonic() - 3
    if b.armed and time.monotonic() - b.last_client > WATCHDOG_S:
        b.disarm()
    assert b.stop_latched
    b.stop_latched = False; b.armed = True; b.last_client = time.monotonic() - 3
    b2 = b.snapshot(); assert b2["stale"]
    # camera
    b.switch_camera(); assert b.camera_second
    b.sock.close()
    assert "stream.mjpg" in PAGE and "<img" in PAGE and "image2pipe" in mjpeg_cmd("ffmpeg", "x")
    print("self-test OK")


def main():
    ap = argparse.ArgumentParser(description="Web interface to fly the E99.")
    ap.add_argument("--ip", default=p.DRONE_IP)
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--url", default=p.RTSP_URL)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg not found on PATH (e.g. winget install Gyan.FFmpeg).", file=sys.stderr)
        sys.exit(1)

    backend = Backend(args.ip, p.CTRL_PORT)   # UDP control (7099); args.port is the HTTP port
    threading.Thread(target=backend.loop, daemon=True).start()
    threading.Thread(target=backend.ping_loop, daemon=True).start()
    threading.Thread(target=video_loop, args=(backend, ffmpeg, args.url), daemon=True).start()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(backend))
    httpd.daemon_threads = True
    url = f"http://127.0.0.1:{args.port}/"
    print(f"UI at {url}  (Ctrl+C to stop). Starts DISARMED. Props OFF.")
    import webbrowser
    webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        backend.running = False
        time.sleep(0.2)
        backend.sock.close()
        print("\nStopped.")


if __name__ == "__main__":
    main()
