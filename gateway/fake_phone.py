"""
A stand-in for the Sensei Cam app: calls the gateway over WebRTC exactly like the
phone does, streaming a notebook page with a moving clock (video) and silence (audio),
and printing every instruction it is asked to say.

  python fake_phone.py                         # gateway on localhost:8787, run 60 s
  python fake_phone.py --server http://192.168.1.20:8787 --seconds 30
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
from aiortc import AudioStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame

PAGE = Path(__file__).resolve().parent.parent / "datasets/samples/math/basic/easy/bad_1.png"


class NotebookTrack(VideoStreamTrack):
    """A 1280x720 frame of a sample page with the time drawn on it, 15 fps."""

    def __init__(self):
        super().__init__()
        page = cv2.imread(str(PAGE)) if PAGE.exists() else np.full((520, 1200, 3), 250, np.uint8)
        self.base = cv2.resize(page, (1280, 720))

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        img = self.base.copy()
        cv2.putText(img, time.strftime("%H:%M:%S"), (1000, 690), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (40, 40, 160), 2)
        frame = VideoFrame.from_ndarray(img, format="bgr24")
        frame.pts, frame.time_base = pts, time_base
        return frame


class FakePhone:
    def __init__(self, server: str):
        self.server = server.rstrip("/")
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))  # like the app: no STUN
        self.heard: list[str] = []
        self.channel_open = asyncio.Event()

    async def connect(self):
        self.pc.addTrack(NotebookTrack())
        self.pc.addTrack(AudioStreamTrack())
        channel = self.pc.createDataChannel("sensei")

        @channel.on("open")
        def on_open():
            channel.send(json.dumps({"type": "hello", "app": "fake-phone"}))
            self.channel_open.set()

        @channel.on("message")
        def on_message(message):
            msg = json.loads(message)
            if msg.get("type") == "say":
                print(f"phone says: {msg['text']}")
                self.heard.append(msg["text"])
                # Pretend text-to-speech took a moment, then report it like the app does.
                asyncio.get_running_loop().call_later(
                    0.2, lambda: channel.send(json.dumps({"type": "spoken", "text": msg["text"]})))

        await self.pc.setLocalDescription(await self.pc.createOffer())  # aiortc gathers ICE here
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.post(f"{self.server}/offer", json={
                "sdp": self.pc.localDescription.sdp, "type": self.pc.localDescription.type})
            res.raise_for_status()
        await self.pc.setRemoteDescription(RTCSessionDescription(**res.json()))
        await asyncio.wait_for(self.channel_open.wait(), timeout=10)

    async def close(self):
        await self.pc.close()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8787")
    ap.add_argument("--seconds", type=float, default=60)
    args = ap.parse_args()
    phone = FakePhone(args.server)
    await phone.connect()
    print(f"connected to {args.server}; streaming for {args.seconds:.0f} s")
    try:
        await asyncio.sleep(args.seconds)
    finally:
        await phone.close()


if __name__ == "__main__":
    asyncio.run(main())
