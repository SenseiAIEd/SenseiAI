"""Sensei's body: the pan-tilt head (ESP32 + two servos) that points the phone's camera.

The ESP32 runs firmware/sensei_head/sensei_head.ino and speaks one-line text commands over USB
serial at 115200 baud (PING, MOVE <pan> <tilt>, PRESET <name>, POS?, SPEED <ms>). `PanTilt` is the
blocking client from the build guide; `Head` wraps it for the async gateway and never raises, so a
loose USB cable can't take the tutor down with it.

Off unless SENSEI_HEAD_PORT is set (e.g. /dev/ttyUSB0 or /dev/ttyACM0 on the Spark).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from typing import Optional

log = logging.getLogger("sensei.head")

PRESETS = ("notebook", "student", "home")


class PanTilt:
    """Talks to the Sensei Desk ESP32 firmware over USB serial (blocking)."""

    def __init__(self, port: str, baud: int = 115200, settle_s: float = 0.3, boot_s: float = 2.0):
        import serial  # pyserial

        self.ser = serial.Serial(port, baud, timeout=0.2)
        self.settle_s = settle_s          # let the phone stop wobbling
        self._lock = threading.Lock()
        time.sleep(boot_s)                # the ESP32 reboots when the port opens
        self.ser.reset_input_buffer()
        if self._send("PING") != "PONG":
            raise RuntimeError("pan-tilt head not responding")

    def _readline(self, deadline: float) -> str:
        while time.monotonic() < deadline:
            line = self.ser.readline().decode(errors="ignore").strip()
            if line:
                return line
        raise TimeoutError("no reply from pan-tilt head")

    def _send(self, text: str, wait_arrival: bool = False, timeout: float = 6.0):
        with self._lock:
            self.ser.reset_input_buffer()     # drop stale ARRIVED lines
            self.ser.write((text + "\n").encode())
            deadline = time.monotonic() + timeout
            reply = self._readline(deadline)
            if reply.startswith("ERR"):
                raise ValueError(f"{text!r} -> {reply}")
            if not wait_arrival:
                return reply
            while True:
                line = self._readline(deadline)
                if line.startswith("ARRIVED"):
                    time.sleep(self.settle_s)
                    _, pan, tilt = line.split()
                    return int(pan), int(tilt)

    def move(self, pan: int, tilt: int) -> tuple[int, int]:
        return self._send(f"MOVE {pan} {tilt}", wait_arrival=True)

    def preset(self, name: str) -> tuple[int, int]:
        return self._send(f"PRESET {name}", wait_arrival=True)

    def speed(self, ms_per_step: int):
        self._send(f"SPEED {ms_per_step}")

    def position(self) -> tuple[int, int]:
        _, pan, tilt = self._send("POS?").split()
        return int(pan), int(tilt)

    def close(self):
        self.ser.close()


class Head:
    """Async, fault-tolerant wrapper: connects lazily, reconnects after errors, reports state."""

    def __init__(self, port: str, connect=PanTilt):
        self.port = port
        self._connect = connect
        self.dev: Optional[PanTilt] = None
        self.pan: Optional[int] = None
        self.tilt: Optional[int] = None
        self.preset: Optional[str] = None
        self.error: Optional[str] = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls) -> Optional["Head"]:
        port = os.environ.get("SENSEI_HEAD_PORT", "").strip()
        return cls(port) if port else None

    def state(self) -> dict:
        return {"port": self.port, "connected": self.dev is not None, "pan": self.pan,
                "tilt": self.tilt, "preset": self.preset, "error": self.error}

    async def _run(self, what: str, fn) -> bool:
        """Run one blocking command, (re)connecting first if needed. Returns False on failure."""
        async with self._lock:
            try:
                if self.dev is None:
                    self.dev = await asyncio.to_thread(self._connect, self.port)
                    log.info("pan-tilt head connected on %s", self.port)
                self.pan, self.tilt = await asyncio.to_thread(fn, self.dev)
                self.error = None
                return True
            except Exception as e:  # serial errors, timeouts, ERR replies, unplugged cable
                self.error = f"{what}: {e}"
                log.warning("pan-tilt head: %s", self.error)
                if not isinstance(e, ValueError) and self.dev is not None:  # link trouble: reconnect next time
                    try:
                        self.dev.close()
                    except Exception:
                        pass
                    self.dev = None
                return False

    async def connect(self) -> bool:
        return await self._run("connect", lambda d: d.position())

    async def look(self, preset: str) -> bool:
        ok = await self._run(f"preset {preset}", lambda d: d.preset(preset))
        if ok:
            self.preset = preset
        return ok

    async def move(self, pan: int, tilt: int) -> bool:
        ok = await self._run(f"move {pan} {tilt}", lambda d: d.move(pan, tilt))
        if ok:
            self.preset = None
        return ok

    async def nudge(self, dpan: int, dtilt: int) -> bool:
        """Move relative to where the head is now (the firmware clamps to its safe limits)."""
        if self.pan is None and not await self.connect():
            return False
        return await self.move(self.pan + dpan, self.tilt + dtilt)

    def close(self):
        if self.dev is not None:
            self.dev.close()
            self.dev = None


# Spoken gaze commands. Conservative on purpose: a question that merely contains "look" goes to
# the tutor, not the servos.
_GAZE = [
    (re.compile(r"\blook (?:at|down at|over at) (?:my |the |this )?(?:notebook|page|paper|homework|work|desk|problem)\b"
                r"|\blook down\b"), "notebook"),
    (re.compile(r"\blook (?:at|up at) (?:me|my face)\b|\blook up\b"), "student"),
    (re.compile(r"\b(?:look straight|look ahead|go home|center yourself)\b"), "home"),
]
GAZE_REPLY = {"notebook": "Okay, looking at your notebook.",
              "student": "Okay, looking at you.",
              "home": "Okay, looking straight ahead."}


def gaze_command(text: str) -> Optional[str]:
    """'Sensei, look at my notebook' -> "notebook"; anything else -> None."""
    t = text.lower()
    for pattern, preset in _GAZE:
        if pattern.search(t):
            return preset
    return None
