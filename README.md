# E88 / E99 Drone — PC Control & FPV

Control a cheap Wi-Fi FPV toy drone **from your PC** — keyboard flight, low-latency live
video in the browser, telemetry and a hardened emergency-stop — with no phone app, using
only Python (standard library) and `ffmpeg`.

This is an independent, reverse-engineered project built for **interoperability, research
and experimentation on your own hardware**. See [DISCLAIMER](DISCLAIMER.md) and
[RESPONSIBLE_USE](RESPONSIBLE_USE.md).

## The drone

A ~$20–30 dual-camera Wi-Fi FPV toy drone from the common "UFO" family:

- **Box label:** E99
- **PCB silkscreen:** E88D (the real model — likely an E88-series board with a firmware variant)
- **App:** FlyUFO (`com.cooingdv.rcufo`)
- **Access point:** `WIFI-UFO-xxxxxx` (open), drone at `192.168.1.1`
- **Video:** MJPEG ~240×320 over RTSP; dual camera (switchable)
- **Module id reported:** 83 (`SPI_KL42_8K`)

The protocol is documented in [PROTOCOL.md](PROTOCOL.md). It very likely works for other
drones in the same family (E88 / E99 / FlyUFO / WIFI-UFO), possibly with small tweaks.

## Features

- **Web GUI** (`e99_gui.py`): live video centered, on-screen controls, and a debug panel
  (sent packet decoded, drone telemetry, latency, packet rates). Fly with the keyboard.
- **Low-latency video** (~100–300 ms) via MJPEG, streamed to the browser.
- **Hardened failsafe:** SPACE latches a continuous emergency-stop that only clears when you
  re-arm — robust even over a lossy link.
- **Console controller** (`e99_control.py`) for a no-browser option.
- **Health check** (`e99_check.py`): network, latency, control/telemetry and video in seconds,
  with a battery hint inferred from latency. Arms nothing.
- **Snapshot** (`e99_snap.py`): grab a still from the live stream (the drone has no storage).
- Everything is **standard-library Python + ffmpeg**; no pip packages.

## Requirements

- **Python 3.8+**
- **ffmpeg** on the `PATH` (for video / snapshots). E.g. `winget install Gyan.FFmpeg`.
- A **Wi-Fi adapter** to join the drone's access point. Tip: use a **second (USB) adapter** so
  your main Wi-Fi keeps internet. `e99_net.ps1` (Windows) automates that split.
- The keyboard controllers (`e99_gui.py` key capture is cross-platform in the browser; the
  console `e99_control.py` uses `msvcrt` and is **Windows-only**). The protocol/video/check
  scripts are cross-platform.

## Quick start

```bash
# 1. Join the drone Wi-Fi (WIFI-UFO-xxxxxx). On Windows with a dedicated USB adapter:
pwsh -File ./e99_net.ps1 -Connect -Ifc 'Wi-Fi 2' -Ssid 'WIFI-UFO-xxxxxx'

# 2. Sanity check (no motors):
python e99_check.py

# 3. Fly from the browser:
python e99_gui.py        # opens http://127.0.0.1:8099
```

**Controls (GUI):** `T` arm · `G` takeoff · `W/S` throttle · `I/K` forward/back ·
`J/L` left/right · arrows yaw · `X` hover · `Y` land · `F` flip · `V` switch camera ·
`SPACE` **STOP**.

## Scripts

| File | What it does |
|---|---|
| `e99_protocol.py` | Protocol core: packet builder/decoder, constants. Run it for a self-test. |
| `e99_gui.py` | Web GUI: video + keyboard flight + debug panel. |
| `e99_control.py` | Console keyboard controller (Windows). |
| `e99_check.py` | Quick no-motor health check (network/latency/control/video). |
| `e99_snap.py` | Grab a still frame from the stream. |
| `e99_video.py` | Standalone HLS browser viewer (higher latency; GUI is preferred). |
| `e99_armtest.py` | Bench routine: arm briefly, then continuous emergency-stop. **Props off.** |
| `e99_net.ps1` | Windows helper to put a USB adapter on the drone AP, keeping internet on the built-in card. |

Every Python script supports `--selftest` (offline, touches nothing).

## Safety

- **Always test with propellers OFF** until you trust the behavior. The control channel can
  spin the motors.
- The **only guaranteed cut is removing the battery** — keep it within reach. A software stop
  depends on the (sometimes lossy) Wi-Fi link.
- **Low battery wrecks the link** (latency jumps to seconds, heavy packet loss). If video/control
  feel laggy, the battery is probably low — the GUI/`e99_check` latency meter will show it.
- Fly only where it's legal and safe, away from people. Follow your local drone regulations.

## How it works

The drone exposes an open Wi-Fi AP. Control is plain UDP to `192.168.1.1:7099` (a 9-byte
"TC" stick packet at 50 ms + a 1 s heartbeat); video is MJPEG over RTSP on `:7070`, which only
streams while the control port is active. Full details and byte layout in
[PROTOCOL.md](PROTOCOL.md).

## Roadmap / ideas

Research and experimentation directions (own hardware only):

- **FPV acro:** tune it into a usable cheap FPV drone; later swap to a lighter frame, stronger
  motors, dual battery or an extra pod.
- **Autonomy:** autonomous takeoff/landing, position/altitude characterization, basic tracking/follow.
- **Audio:** add a microphone + speaker for live two-way audio (deploy nearby, talk at a distance).
- **PC integration & swarm:** drive several units from one PC as a small swarm.

Contributions welcome — especially captures/confirmations from other E88/E99/FlyUFO units.

## License

[MIT](LICENSE). Please also read [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md) — this project is
shared for peaceful research and education, not for weaponization, military/combat use,
surveillance of others, or anything illegal.

---

*Keywords: E88, E88D, E99, FlyUFO, com.cooingdv.rcufo, WIFI-UFO, cheap FPV drone, Wi-Fi drone
PC control, RTSP MJPEG drone, reverse engineering, UDP drone protocol.*
