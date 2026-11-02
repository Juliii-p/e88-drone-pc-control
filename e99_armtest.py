#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E99 "arm test" routine: arms the motors, holds ~1 s, then full-stops continuously.

Meant for the bench with PROPELLERS OFF. Replicates what was validated:
  - arm/takeoff with TAKEOFF (flag 0x01) at 50 ms for ARM_SECONDS,
  - then full stop = continuous disarm beacon (throttle 0 + EMERGENCY) until Ctrl+C.
The disarm is the same failsafe used by the controller.

Usage (PC on the drone Wi-Fi):
  python e99_armtest.py                 # arm 1 s, then continuous stop until Ctrl+C
  python e99_armtest.py --seconds 1.5
  python e99_armtest.py --selftest      # does not touch the drone
"""
import argparse
import socket
import time

import e99_protocol as p

TICK = 0.05


def arm_hold_stop(ip, port, seconds):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (ip, port)
    t0 = time.monotonic()
    print(f"[armtest] T+0.00s  ARMING (TAKEOFF), holding {seconds:.1f}s...")
    n = 0
    while time.monotonic() - t0 < seconds:
        s.sendto(p.control(flags=p.TAKEOFF), addr)   # throttle=center (hover), takeoff flag
        n += 1
        time.sleep(TICK)
    t_stop = time.monotonic()
    print(f"[armtest] T+{t_stop - t0:.2f}s  CONTINUOUS EMERGENCY (throttle 0). Ctrl+C to stop the beacon.")
    m = 0
    try:
        while True:                       # does not stop on its own: the stop beacon stays until you cut it
            s.sendto(p.control(throttle=0, flags=p.EMERGENCY), addr); m += 1
            time.sleep(0.03)
    except KeyboardInterrupt:
        pass
    s.sendto(p.CONTROL_OFF, addr)
    s.close()
    print(f"\n[armtest] beacon stopped (arm {n} pkt, {m} EMERGENCY pkt / {time.monotonic() - t_stop:.1f}s).")


def selftest():
    arm = p.control(flags=p.TAKEOFF)
    assert p.decode_control(arm)["flags"] & p.TAKEOFF
    stop = p.control(throttle=0, flags=p.EMERGENCY)
    d = p.decode_control(stop)
    assert d["flags"] & p.EMERGENCY and d["throttle"] == 0 and not (d["flags"] & p.TAKEOFF)
    assert p.CONTROL_OFF == bytes([0x08, 0x01])
    print("self-test OK")


def main():
    ap = argparse.ArgumentParser(description='"arm test": arm 1 s then continuous stop (bench, props off).')
    ap.add_argument("--ip", default=p.DRONE_IP)
    ap.add_argument("--port", type=int, default=p.CTRL_PORT)
    ap.add_argument("--seconds", type=float, default=1.0)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    arm_hold_stop(args.ip, args.port, args.seconds)


if __name__ == "__main__":
    main()
