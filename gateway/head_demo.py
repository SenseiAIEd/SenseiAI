"""Move the pan-tilt head by hand, straight over USB: no gateway, no phone.

    python head_demo.py                 # finds the ESP32, runs the tour: home, notebook, student, sweeps
    python head_demo.py --keys          # drive it: a/d pan, w/s tilt, 1 home, 2 notebook, 3 student,
                                        # n / t save as notebook / student, r relax, q quit
    python head_demo.py --port /dev/cu.usbserial-0001
    python head_demo.py --test-limits   # also shows over-rotation being refused (calibrate limits first)

Only needs pyserial (uv pip install pyserial). Stop the gateway first if it has the port open.
"""
from __future__ import annotations

import argparse
import sys
import time

from head import PanTilt


def find_port() -> str:
    from serial.tools import list_ports
    ports = [p for p in list_ports.comports()
             if any(k in (p.device + " " + (p.description or "")).lower()
                    for k in ("usbserial", "ttyusb", "ttyacm", "slab", "cp210", "ch340", "wch", "uart"))]
    if not ports:
        sys.exit("No ESP32 found. Plug it in, or pass --port (ls /dev/cu.* on a Mac, /dev/ttyUSB* on Linux).")
    return ports[0].device


def show(head: PanTilt, what: str, fn):
    t0 = time.monotonic()
    try:
        pan, tilt = fn()
        print(f"  {what:<28} -> pan {pan:3d}°  tilt {tilt:3d}°   ({time.monotonic() - t0:.1f} s)")
    except ValueError as e:
        print(f"  {what:<28} -> refused: {e}")


def tour(head: PanTilt, test_limits: bool = False):
    try:
        print(f"  limits: {head.limits()}   presets: {head.presets()}")
    except ValueError:
        print("  (original firmware: no saved presets/limits; flash firmware/sensei_head for those)")
    show(head, "home", lambda: head.preset("home"))
    show(head, "notebook (look down)", lambda: head.preset("notebook"))
    show(head, "student (look up)", lambda: head.preset("student"))
    show(head, "home", lambda: head.preset("home"))
    for pan in (60, 120, 90):
        show(head, f"pan to {pan}", lambda: head.move(pan, 90))
    for tilt in (70, 120, 90):
        show(head, f"tilt to {tilt}", lambda: head.move(90, tilt))
    if test_limits:  # drives to the soft limits: only once they suit your bracket
        show(head, "over-rotate: pan 999 tilt -50", lambda: head.move(999, -50))  # the ESP32 clamps it
    show(head, "back home", lambda: head.preset("home"))


def keys(head: PanTilt, step: int = 5):
    import termios
    import tty
    print("  a/d pan, w/s tilt, 1 home, 2 notebook, 3 student, n/t save notebook/student, r relax, q quit")
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            k = sys.stdin.read(1).lower()
            pan, tilt = head.position()
            if k == "q":
                break
            elif k in "ad":
                show(head, f"pan {'-' if k == 'a' else '+'}{step}", lambda: head.move(pan + (step if k == "d" else -step), tilt))
            elif k in "ws":
                show(head, f"tilt {'-' if k == 'w' else '+'}{step}", lambda: head.move(pan, tilt + (step if k == "s" else -step)))
            elif k in "123":
                name = {"1": "home", "2": "notebook", "3": "student"}[k]
                show(head, name, lambda: head.preset(name))
            elif k in "nt":
                name = "notebook" if k == "n" else "student"
                try:
                    head.save(name)
                    print(f"  saved pan {pan}° tilt {tilt}° as {name}")
                except ValueError as e:
                    print(f"  can't save: {e} (flash the new firmware)")
            elif k == "r":
                head.relax()
                print("  servos relaxed: move the head by hand to check balance; any key moves it again")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial port (default: find the ESP32)")
    ap.add_argument("--keys", action="store_true", help="drive the head from the keyboard")
    ap.add_argument("--test-limits", action="store_true",
                    help="also ask for pan 999 / tilt -50 and show the ESP32 clamps it (goes to the limits!)")
    ap.add_argument("--boot", type=float, default=2.0, help="seconds to wait for the ESP32 to reboot")
    args = ap.parse_args()
    port = args.port or find_port()
    print(f"Pan-tilt head on {port}")
    head = PanTilt(port, boot_s=args.boot)
    print("  PING -> PONG, position", head.position())
    try:
        keys(head) if args.keys else tour(head, args.test_limits)
    finally:
        head.close()


if __name__ == "__main__":
    main()
