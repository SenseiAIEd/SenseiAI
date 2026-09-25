"""A simulated pan-tilt head: speaks the ESP32 firmware's serial protocol on a pseudo-terminal.

Lets you run the gateway with SENSEI_HEAD_PORT=<pty> and no hardware, and backs the tests.

    python fake_head.py            # prints e.g. /dev/pts/7; then SENSEI_HEAD_PORT=/dev/pts/7
"""
from __future__ import annotations

import os
import threading
import time

HARD_PAN = (5, 175)
HARD_TILT = (30, 160)


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


class FakeHead:
    def __init__(self, step_s: float = 0.0005):
        self.master, slave = os.openpty()
        self.port = os.ttyname(slave)
        self._slave = slave
        self.pan = self.tilt = 90
        self.limits = {"pan": [10, 170], "tilt": [40, 150]}
        self.presets = {"home": [90, 90], "notebook": [90, 135], "student": [90, 70]}
        self.relaxed = False
        self.step_s = step_s   # seconds per 1-degree step (the firmware defaults to 15 ms)
        self.commands: list[str] = []
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _reply(self, text: str):
        os.write(self.master, (text + "\r\n").encode())

    def _go(self, pan: int, tilt: int):
        pan, tilt = clamp(pan, *self.limits["pan"]), clamp(tilt, *self.limits["tilt"])
        self.relaxed = False
        self._reply("OK")
        steps = max(abs(pan - self.pan), abs(tilt - self.tilt))
        time.sleep(steps * self.step_s)
        self.pan, self.tilt = pan, tilt
        self._reply(f"ARRIVED {pan} {tilt}")

    def _handle(self, line: str):
        self.commands.append(line)
        cmd, _, arg = line.partition(" ")
        cmd = cmd.upper()
        try:
            if cmd == "PING":
                self._reply("PONG")
            elif cmd == "POS?":
                self._reply(f"POS {self.pan} {self.tilt}")
            elif cmd == "MOVE":
                p, t = arg.split()
                self._go(int(p), int(t))
            elif cmd == "PAN":
                self._go(int(arg), self.tilt)
            elif cmd == "TILT":
                self._go(self.pan, int(arg))
            elif cmd == "NUDGE":
                p, t = arg.split()
                self._go(self.pan + int(p), self.tilt + int(t))
            elif cmd == "PRESET":
                if arg.lower() not in self.presets:
                    self._reply("ERR unknown preset")
                else:
                    self._go(*self.presets[arg.lower()])
            elif cmd == "SAVE":
                if arg.lower() not in self.presets:
                    self._reply("ERR unknown preset")
                else:
                    self.presets[arg.lower()] = [self.pan, self.tilt]
                    self._reply("OK")
            elif cmd == "PRESETS?":
                self._reply("PRESETS " + " ".join(f"{n} {p} {t}" for n, (p, t) in self.presets.items()))
            elif cmd == "LIMIT":
                axis, lo, hi = arg.split()
                hard = HARD_PAN if axis.upper() == "PAN" else HARD_TILT
                if axis.upper() not in ("PAN", "TILT") or int(lo) >= int(hi):
                    raise ValueError
                self.limits[axis.lower()] = [clamp(int(lo), *hard), clamp(int(hi), *hard)]
                self._reply("OK")
            elif cmd == "LIMITS?":
                self._reply("LIMITS {} {} {} {}".format(*self.limits["pan"], *self.limits["tilt"]))
            elif cmd == "STOP":
                self._reply("OK")
                self._reply(f"ARRIVED {self.pan} {self.tilt}")
            elif cmd == "RELAX":
                self.relaxed = True
                self._reply("OK")
            elif cmd == "SPEED":
                self._reply("OK")
            else:
                self._reply("ERR unknown command")
        except ValueError:
            self._reply("ERR usage")

    def _serve(self):
        buf = b""
        while not self._stop:
            try:
                data = os.read(self.master, 256)
            except OSError:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.decode(errors="ignore").strip()
                if line:
                    self._handle(line)

    def close(self):
        self._stop = True
        for fd in (self.master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass


if __name__ == "__main__":
    head = FakeHead(step_s=0.015)
    print(f"fake pan-tilt head on {head.port}  (export SENSEI_HEAD_PORT={head.port})", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        head.close()
