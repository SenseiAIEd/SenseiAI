"""
A stand-in for the Sensei Cam app: calls the gateway over WebRTC exactly like the
phone does, streaming a notebook page with a moving clock (video) and silence (audio),
and printing every instruction it is asked to say.

  python fake_phone.py                         # gateway on localhost:8787, run 60 s
  python fake_phone.py --server http://192.168.1.20:8787 --seconds 30
  python fake_phone.py --server https://spark-e257.tail803c7f.ts.net:8443 --key KEY --relay-only
      # through Funnel, media forced through the TURN relay the gateway advertises
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
from aioice import TransportPolicy
from aiortc import (AudioStreamTrack, RTCConfiguration, RTCIceServer, RTCPeerConnection,
                    RTCSessionDescription, VideoStreamTrack)
from av import VideoFrame

PAGE = Path(__file__).resolve().parent.parent / "datasets/samples/math/basic/easy/bad_1.png"


class NotebookTrack(VideoStreamTrack):
    """A 1280x720 frame of a sample page with the time drawn on it, 30 fps."""

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
    def __init__(self, server: str, key: str = "", relay_only: bool = False):
        self.server = server.rstrip("/")
        self.headers = {"X-Sensei-Key": key} if key else {}
        self.relay_only = relay_only
        self.pc: RTCPeerConnection | None = None
        self.heard: list[str] = []
        self.messages: list[dict] = []  # everything the gateway sent on the data channel
        self.channel = None
        self.channel_open = asyncio.Event()

    def use_relay_only(self):
        """Offer only TURN relay candidates, like a phone that can't reach the Spark directly.
        aiortc has no iceTransportPolicy, so set it on its (private) ICE connections."""
        gatherers = {t.sender.transport.transport.iceGatherer for t in self.pc.getTransceivers()}
        gatherers.add(self.pc.sctp.transport.transport.iceGatherer)
        for g in gatherers:
            g._connection._transport_policy = TransportPolicy.RELAY

    async def connect(self):
        async with httpx.AsyncClient(timeout=10, headers=self.headers) as client:
            res = await client.get(f"{self.server}/config")
            res.raise_for_status()
            servers = [RTCIceServer(**s) for s in res.json()["iceServers"]]
        if self.relay_only and not servers:
            raise RuntimeError("--relay-only needs a TURN relay, but the gateway advertises none")
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))  # like the app: TURN only if offered

        self.pc.addTrack(NotebookTrack())
        self.pc.addTrack(AudioStreamTrack())
        channel = self.pc.createDataChannel("sensei")
        self.channel = channel

        @channel.on("open")
        def on_open():
            channel.send(json.dumps({"type": "hello", "app": "fake-phone"}))
            self.channel_open.set()

        @channel.on("message")
        def on_message(message):
            msg = json.loads(message)
            self.messages.append(msg)
            if msg.get("type") == "session_ended":
                print(f"session ended: {msg.get('summary')}")
            if msg.get("type") == "say":
                print(f"phone says: {msg['text']}")
                self.heard.append(msg["text"])
                # Pretend text-to-speech took a moment, then report it like the app does.
                asyncio.get_running_loop().call_later(
                    0.2, lambda: channel.send(json.dumps({"type": "spoken", "text": msg["text"]})))

        if self.relay_only:
            self.use_relay_only()
        await self.pc.setLocalDescription(await self.pc.createOffer())  # aiortc gathers ICE here
        async with httpx.AsyncClient(timeout=10, headers=self.headers) as client:
            res = await client.post(f"{self.server}/offer", json={
                "sdp": self.pc.localDescription.sdp, "type": self.pc.localDescription.type})
            res.raise_for_status()
        await self.pc.setRemoteDescription(RTCSessionDescription(**res.json()))
        await asyncio.wait_for(self.channel_open.wait(), timeout=10)

    def send(self, msg: dict):
        """Like the app's buttons: {"type": "start", "minutes": 5}, {"type": "request", "what": "hint"}."""
        self.channel.send(json.dumps(msg))

    def candidate_types(self) -> set[str]:
        """Types of the ICE candidates we offered (host, relay, ...)."""
        return {line.split(" typ ")[1].split()[0]
                for line in self.pc.localDescription.sdp.splitlines() if line.startswith("a=candidate")}

    async def close(self):
        if self.pc is not None:
            await self.pc.close()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://localhost:8787")
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--key", default="", help="the gateway's SENSEI_KEY, if it has one")
    ap.add_argument("--relay-only", action="store_true", help="send media only through the TURN relay")
    ap.add_argument("--tutor", type=float, metavar="MINUTES", help="start a tutoring session of this length")
    args = ap.parse_args()
    phone = FakePhone(args.server, key=args.key, relay_only=args.relay_only)
    await phone.connect()
    print(f"connected to {args.server} via {', '.join(sorted(phone.candidate_types()))} candidates; "
          f"streaming for {args.seconds:.0f} s")
    if args.tutor:
        phone.send({"type": "start", "minutes": args.tutor})
    try:
        await asyncio.sleep(args.seconds)
    finally:
        await phone.close()


if __name__ == "__main__":
    asyncio.run(main())
