# E99 / E88 Wi-Fi protocol (FlyUFO app)

Status: **derived from the official app; partially confirmed with live captures.**

This is an independent, reverse-engineered description of the Wi-Fi control protocol used by
a cheap FPV toy drone (box label "E99", PCB silkscreen "E88D", app "FlyUFO" /
`com.cooingdv.rcufo`), written for **interoperability and research on your own hardware**.
No proprietary code or firmware is included here — only the protocol description our own
analysis produced.

## 1. Network

| Item | Value |
|---|---|
| SSID | `WIFI-UFO-xxxxxx`, open (no password) |
| Channel | 2.4 GHz |
| Drone IP | `192.168.1.1` |
| Client IP | via DHCP (typically `192.168.1.100`) |

Note: this drone uses **192.168.1.0/24**, not `192.168.0.x` like some others in the family.

## 2. Video

| Item | Value |
|---|---|
| Live stream | `rtsp://192.168.1.1:7070/webcam` |
| Codec | MJPEG, ~240x320 (portrait), carried over RTSP (UDP transport) |
| Note | The RTSP server only streams while there is activity on the control port (7099). Send `CONTROL_OFF` (`08 01`) at ~50 ms to keep it alive without arming motors. |

The drone has **no onboard storage** (no SD slot): the app's "photo"/"video" are just
snapshots/recordings of the live stream saved on the phone. There is no file server to pull from.

## 3. Control channel (UDP)

| Item | Value |
|---|---|
| Destination | `192.168.1.1:7099` (UDP, from an ephemeral local port) |
| Heartbeat | `01 01` every 1 s |
| Sticks | every 50 ms while control mode is active |
| Leave control mode | `08 01` |
| Switch camera | `06 02` (second) / `06 01` (first) |

### 3.1 Stick packet ("TC" format, 9 UDP bytes)

```
03 | 66  B1  B2  B3  B4  B5  B6  99
     |   |   |   |   |   |   |   └ fixed tail 0x99
     |   |   |   |   |   |   └ XOR of B1..B5
     |   |   |   |   |   └ flags (see 3.2)
     |   |   |   |   └ yaw, 1..255, 128 center, >128 right
     |   |   |   └ throttle, 0..255; with altitude-hold 128 = hold
     |   |   └ pitch, 1..255, 128 center, >128 forward
     |   └ roll, 1..255, 128 center, >128 right
     └ fixed header 0x66
```

- Throttle never equals 1: the app converts it to 0.
- Neutral: `03 66 80 80 80 80 00 00 99` (see the self-test in `e99_protocol.py`).
- **Altitude-hold** appears to be on by default; at rest the throttle byte is 128 (hold).

### 3.2 Flag byte (B5)

| Bit | Function | Duration in the app |
|---|---|---|
| 0x01 | takeoff / fast ascend | 1 s |
| 0x02 | auto land | 1 s |
| 0x04 | emergency stop (cuts motors) | 1 s |
| 0x08 | flip | 600 ms, with roll or pitch at 1 or 255 |
| 0x10 | headless mode | held while active |
| 0x20 | fast return / unlock | — (poorly decompiled, unverified) |
| 0x80 | gyro calibration | 2 s |

### 3.3 "GL" 21-byte variant (not this drone)

If the drone identifies as GL, the app sends `03` + 20 bytes with the flags split across two
bytes. This unit replies with id 83 (not GL), so it uses the 3.1 format.

## 4. Drone replies

The drone sends ~5 bytes about once per second from `:7099`:

| Byte | Meaning |
|---|---|
| 0 | module id. **83 = `SPI_KL42_8K`** on this unit |
| 1 | camera-switch mode (1 = hot switch, 2 = restart the RTSP) |

## 5. App sequence

1. Phone joins the open network and gets a DHCP IP.
2. The player opens `rtsp://192.168.1.1:7070/webcam`.
3. Once video is ready: the UDP socket to `:7099` opens, the `01 01` heartbeat starts (1 s), and replies are read.
4. In control mode: stick packets every 50 ms. Leaving it: `08 01`.

## 6. Observations from live testing

- Control works over this channel end-to-end; takeoff, stick axes and emergency-stop were all exercised.
- **Altitude / throttle behavior is still being characterized**: on this unit, takeoff tends to keep
  climbing and `throttle = 128` does not clearly "hold". See the project README roadmap.
- Low drone battery drastically degrades the Wi-Fi link (latency from ~1-3 ms up to seconds, heavy loss).
- Bit `0x20` comes from a poorly decompiled branch and is unverified.
- `Config.TCP_SERVER_PORT = 5000` is defined in the app but unused on this screen.
