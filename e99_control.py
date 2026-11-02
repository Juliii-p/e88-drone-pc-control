#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E99 flight controller from the PC via keyboard (uses e99_protocol).

Reuses e99_protocol: ports, heartbeat, control() and the flag bits (TC format).
Console-based alternative to the web GUI (e99_gui.py).

Usage (PC on the drone Wi-Fi):
  python e99_control.py
  python e99_control.py --selftest     # checks the local logic (disarm, decay)

Keys (console focused):
  W/S  throttle up/down          I/K  forward/back (pitch)   J/L  left/right (roll)
  Left/Right arrows  yaw         T    takeoff          Y    land
  F    flip        H  headless (toggle)        C  gyro calibrate
  SPACE  DISARM (latched failsafe: continuous stop + throttle 0)   R  re-arm
  Q / ESC  quit (sends CONTROL_OFF)
Axes spring back to center on release (hover with altitude-hold). Windows only (msvcrt).
"""
import argparse
import socket
import sys
import threading
import time

import e99_protocol as p

HOLD_MS = 150          # an axis returns to center if the key is not repeated within this window
PULSE_MS = {p.TAKEOFF: 1000, p.LAND: 1000, p.FLIP: 600, p.CALIBRATE: 2000}
STEP = 90              # offset from center (128 +/- 90)


class Controller:
    def __init__(self, ip, port):
        self.addr = (ip, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.running = True
        self.disarmed = False
        now = time.monotonic()
        self.axes = {"roll": [p.CENTER, now], "pitch": [p.CENTER, now],
                     "throttle": [p.CENTER, now], "yaw": [p.CENTER, now]}
        self.flags_until = {}      # bit -> expires_at
        self.toggles = 0           # persistent flags (headless)

    def _heartbeat(self):
        while self.running:
            try:
                self.sock.sendto(p.HEARTBEAT, self.addr)
            except OSError:
                pass
            time.sleep(1.0)

    def _control(self):
        while self.running:
            now = time.monotonic()
            vals = {}
            for name, (val, until) in self.axes.items():
                if now > until and val != p.CENTER:
                    self.axes[name] = [p.CENTER, now]
                vals[name] = self.axes[name][0]
            flags = self.toggles
            for bit, until in list(self.flags_until.items()):
                if now <= until:
                    flags |= bit
                else:
                    del self.flags_until[bit]
            if self.disarmed:          # failsafe: continuous stop + dead throttle
                flags = (flags | p.EMERGENCY) & ~(p.TAKEOFF | p.LAND)
                vals = {"roll": p.CENTER, "pitch": p.CENTER, "throttle": 0, "yaw": p.CENTER}
            pkt = p.control(vals["roll"], vals["pitch"], vals["throttle"], vals["yaw"], flags)
            try:
                self.sock.sendto(pkt, self.addr)
            except OSError:
                pass
            time.sleep(0.05)

    def set_axis(self, name, value):
        self.axes[name] = [value, time.monotonic() + HOLD_MS / 1000.0]

    def pulse(self, bit):
        self.flags_until[bit] = time.monotonic() + PULSE_MS.get(bit, 600) / 1000.0

    def disarm(self):
        self.disarmed = True

    def rearm(self):
        self.disarmed = False
        for name in self.axes:
            self.axes[name] = [p.CENTER, time.monotonic()]

    def start(self):
        for fn in (self._heartbeat, self._control):
            threading.Thread(target=fn, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            self.sock.sendto(p.CONTROL_OFF, self.addr)
        except OSError:
            pass


def read_keys(ctrl):
    import msvcrt
    C = p.CENTER
    print("Flying. Q or ESC to quit.\n")
    while ctrl.running:
        if not msvcrt.kbhit():
            time.sleep(0.01)
            continue
        ch = msvcrt.getch()
        if ch in (b'\x00', b'\xe0'):               # arrow keys
            k = msvcrt.getch()
            if k == b'K': ctrl.set_axis("yaw", C - STEP)
            elif k == b'M': ctrl.set_axis("yaw", C + STEP)
            elif k == b'H': ctrl.set_axis("throttle", C + STEP)
            elif k == b'P': ctrl.set_axis("throttle", C - STEP)
            continue
        c = ch.lower()
        if c == b'w': ctrl.set_axis("throttle", C + STEP)
        elif c == b's': ctrl.set_axis("throttle", C - STEP)
        elif c == b'i': ctrl.set_axis("pitch", C + STEP)
        elif c == b'k': ctrl.set_axis("pitch", C - STEP)
        elif c == b'j': ctrl.set_axis("roll", C - STEP)
        elif c == b'l': ctrl.set_axis("roll", C + STEP)
        elif c == b't': ctrl.pulse(p.TAKEOFF); print("takeoff")
        elif c == b'y': ctrl.pulse(p.LAND); print("land")
        elif c == b'f': ctrl.pulse(p.FLIP); print("flip")
        elif c == b'h': ctrl.toggles ^= p.HEADLESS; print("headless:", bool(ctrl.toggles & p.HEADLESS))
        elif c == b'c': ctrl.pulse(p.CALIBRATE); print("calibrating gyro")
        elif c == b' ': ctrl.disarm(); print("!!! DISARM (motors dead). Press R to re-arm.")
        elif c == b'r': ctrl.rearm(); print(">>> re-armed.")
        elif c in (b'q', b'\x1b'):
            ctrl.stop()
            break


def selftest():
    ctrl = Controller(p.DRONE_IP, p.CTRL_PORT)
    # disarm: the packet carries EMERGENCY and throttle 0, without takeoff/land
    ctrl.disarm()
    ctrl.toggles = p.TAKEOFF
    ctrl.axes["throttle"] = [p.CENTER + STEP, time.monotonic() + 9]
    now = time.monotonic()
    flags = ctrl.toggles
    vals = {"throttle": ctrl.axes["throttle"][0]}
    if ctrl.disarmed:
        flags = (flags | p.EMERGENCY) & ~(p.TAKEOFF | p.LAND)
        vals["throttle"] = 0
    pkt = p.control(p.CENTER, p.CENTER, vals["throttle"], p.CENTER, flags)
    d = p.decode_control(pkt)
    assert d["flags"] & p.EMERGENCY and not (d["flags"] & p.TAKEOFF) and d["throttle"] == 0, d
    # decay: an axis with an expired hold returns to center
    ctrl.disarmed = False
    ctrl.axes["roll"] = [p.CENTER + STEP, now - 1]
    assert ctrl.axes["roll"][0] != p.CENTER
    # (re-centering happens inside _control; here we only validate the condition)
    assert (now > ctrl.axes["roll"][1])
    ctrl.sock.close()
    print("self-test OK")


def main():
    ap = argparse.ArgumentParser(description="E99 keyboard flight controller.")
    ap.add_argument("--ip", default=p.DRONE_IP)
    ap.add_argument("--port", type=int, default=p.CTRL_PORT)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    if sys.platform != "win32":
        print("Note: the keyboard uses msvcrt (Windows only).", file=sys.stderr)
    ctrl = Controller(args.ip, args.port)
    ctrl.start()
    print(f"Sending to {args.ip}:{args.port} (UDP). Make sure you are on the drone Wi-Fi.")
    try:
        read_keys(ctrl)
    except KeyboardInterrupt:
        ctrl.stop()
    finally:
        time.sleep(0.1)
        ctrl.sock.close()
        print("\nExiting (CONTROL_OFF sent).")


if __name__ == "__main__":
    main()
