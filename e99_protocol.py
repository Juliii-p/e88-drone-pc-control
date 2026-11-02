"""E99/E88 Wi-Fi protocol (FlyUFO app, com.cooingdv.rcufo 1.9.7), 9-byte "TC" packet.

Source: reverse-engineered from the app (see PROTOCOL.md). Run this file for the self-test.
"""
import platform
import re
import subprocess

DRONE_IP = "192.168.1.1"
CTRL_PORT = 7099                              # SocketClient.startUdpTask
RTSP_URL = "rtsp://192.168.1.1:7070/webcam"   # Config.PREVIEW_ADDRESS
CENTER = 128

HEARTBEAT = bytes([0x01, 0x01])      # HeartBeatTask, every 1 s
CONTROL_OFF = bytes([0x08, 0x01])    # FlyController.setController(false)

# Flag byte bits (FlyController.FlyControlTask, deviceType == 10 branch)
TAKEOFF = 0x01     # isFastFly, app holds it 1 s
LAND = 0x02        # isFastDrop, 1 s
EMERGENCY = 0x04   # isEmergencyStop, 1 s: cuts motors
FLIP = 0x08        # isCircleTurnEnd, 600 ms, with roll/pitch at extreme in the flip direction
HEADLESS = 0x10    # isNoHeadMode, held while active
UNLOCK = 0x20      # isFastReturn / isUnLock (poorly decompiled branch, unverified)
CALIBRATE = 0x80   # isGyroCorrection, 2 s


def switch_camera(second: bool) -> bytes:
    """SocketClient.switchCamera: 06 02 = second camera, 06 01 = first."""
    return bytes([0x06, 0x02 if second else 0x01])


def _clamp(v, lo=1, hi=255):
    return max(lo, min(hi, int(v)))


def control(roll=CENTER, pitch=CENTER, throttle=CENTER, yaw=CENTER, flags=0) -> bytes:
    """Stick packet: 03 | 66 roll pitch throttle yaw flags xor 99.

    roll/pitch/yaw: 1..255 (128 center, >128 = right/forward/right).
    throttle: 0..255; with altitude-hold (app default) 128 = hold altitude.
    """
    b1, b2, b4 = _clamp(roll), _clamp(pitch), _clamp(yaw)
    b3 = max(0, min(255, int(throttle)))
    if b3 == 1:  # the app turns 1 into 0
        b3 = 0
    f = flags & 0xFF
    return bytes([0x03, 0x66, b1, b2, b3, b4, f, b1 ^ b2 ^ b3 ^ b4 ^ f, 0x99])


def decode_control(payload: bytes):
    """Inverse of control(); returns a dict or None if not a valid stick packet."""
    if len(payload) != 9 or payload[0] != 0x03 or payload[1] != 0x66 or payload[8] != 0x99:
        return None
    b1, b2, b3, b4, f, chk = payload[2:8]
    return {"roll": b1, "pitch": b2, "throttle": b3, "yaw": b4, "flags": f,
            "checksum_ok": chk == (b1 ^ b2 ^ b3 ^ b4 ^ f)}


def parse_status(data: bytes) -> dict:
    """Drone reply: byte 0 = module id (83 = SPI_KL42_8K), byte 1 = camera-switch mode."""
    return {"device_id": data[0] if data else None,
            "camera_reset": data[1] if len(data) > 1 else None,
            "len": len(data)}


def ping_ms(ip=DRONE_IP, timeout_ms=1000):
    """Ping RTT in ms, or None if no reply. Useful to infer the drone's battery level
    (good link ~1-3 ms on a full battery; climbs to hundreds/thousands as it drains)."""
    win = platform.system() == "Windows"
    cmd = ["ping", "-n" if win else "-c", "1",
           "-w" if win else "-W", str(timeout_ms if win else max(1, timeout_ms // 1000)), ip]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout_ms / 1000 + 2).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    m = re.search(r"(?:tiempo|time)[=<]\s*(\d+)\s*ms", out, re.I)   # matches EN and ES ping output
    return int(m.group(1)) if m else None


if __name__ == "__main__":
    assert control() == bytes.fromhex("036680808080000099"), control().hex()
    assert control(flags=TAKEOFF) == bytes.fromhex("036680808080010199")
    assert control(roll=255, pitch=0, throttle=1, yaw=300)[2:6] == bytes([255, 1, 0, 255])
    p = control(roll=200, pitch=60, throttle=170, yaw=90, flags=HEADLESS | LAND)
    d = decode_control(p)
    assert d and d["checksum_ok"] and (d["roll"], d["pitch"], d["throttle"], d["yaw"]) == (200, 60, 170, 90)
    assert decode_control(p[:-1] + b"\x00") is None
    assert parse_status(bytes([0x53, 0x01]))["device_id"] == 83
    print("self-test OK")
