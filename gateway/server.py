"""
Sensei gateway: runs on the DGX Spark and receives the live call from the Sensei Cam app.

  Phone  --WebRTC video + mic audio-->  gateway  --> sessions/<time>/session.mp4 (recording)
         <--data channel "say" text---           --> http://localhost:8787 (live preview + controls)

The phone reaches the Spark either directly (same Wi-Fi, or the Tailscale app) or from
anywhere through Tailscale Funnel, with the media relayed by coturn (see README.md).
The vision model will run on the same box.

Setup:
  uv venv && source .venv/bin/activate
  uv pip install -r requirements.txt
  uvicorn server:app --host 0.0.0.0 --port 8787

Open http://<spark-address>:8787 in any browser to watch the stream and send spoken instructions.
In the phone app, set the server to the same http://<spark-address>:8787.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import av
import cv2
from aiortc import MediaStreamTrack, RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaRelay
from aiortc.mediastreams import MediaStreamError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

RECORD_DIR = Path(os.environ.get("SENSEI_RECORD_DIR", "sessions"))
PREVIEW_FPS = 8  # JPEGs for the browser preview; the recording keeps the full frame rate
# No STUN: phone and Spark reach each other directly (same LAN or tailnet). aiortc's default
# public STUN server would stall every call ~5 s when there is no internet.
RTC_CONFIG = RTCConfiguration(iceServers=[])
RECORD_LONG_SIDE = 1280

# Access key. Required whenever the gateway is reachable from the internet (Funnel):
# otherwise anyone with the URL could watch the camera. Sent as the X-Sensei-Key header,
# "Authorization: Bearer <key>", or ?key= for the console page and its preview image.
# Unset = open (LAN only).
ACCESS_KEY = os.environ.get("SENSEI_KEY", "")

# TURN relay for phones that can't reach the Spark directly (e.g. through Funnel).
# URLS: comma-separated, e.g. "turns:spark-e257.tail803c7f.ts.net:10000?transport=tcp".
# SECRET: coturn's static-auth-secret; the gateway hands out short-lived passwords from it.
TURN_URLS = [u.strip() for u in os.environ.get("SENSEI_TURN_URLS", "").split(",") if u.strip()]
TURN_SECRET = os.environ.get("SENSEI_TURN_SECRET", "")
TURN_TTL_S = int(os.environ.get("SENSEI_TURN_TTL", 6 * 3600))

log = logging.getLogger("sensei")
app = FastAPI()
relay = MediaRelay()


@app.middleware("http")
async def require_key(request: Request, call_next):
    if ACCESS_KEY and request.url.path != "/":  # the console page itself holds no data
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        given = request.headers.get("x-sensei-key") or bearer or request.query_params.get("key") or ""
        if not hmac.compare_digest(given.encode(), ACCESS_KEY.encode()):
            return JSONResponse({"detail": "missing or wrong access key"}, status_code=401)
    return await call_next(request)


def turn_credentials(now: float | None = None) -> dict:
    """coturn "use-auth-secret" credentials: valid until the expiry in the username."""
    username = f"{int((now or time.time()) + TURN_TTL_S)}:sensei"
    digest = hmac.new(TURN_SECRET.encode(), username.encode(), hashlib.sha1).digest()
    return {"username": username, "credential": base64.b64encode(digest).decode()}


class SessionRecorder:
    """Writes the call to an mp4: H.264 video at a fixed size, plus AAC audio.

    The file is opened on the first video frame, which fixes its size and orientation
    (long side RECORD_LONG_SIDE); later frames are scaled to match, because WebRTC starts
    small and ramps up. Audio that arrives before that first frame is dropped. (aiortc's
    MediaRecorder writes the header on the first packet of any track, so when audio wins
    that race the whole video is stuck at 640x480.) Encoding runs on its own thread so
    it never stalls the WebRTC event loop.
    """

    def __init__(self, path: Path, long_side: int = RECORD_LONG_SIDE):
        self.path = path
        self.long_side = long_side
        self.video_frames = 0
        self.size: tuple[int, int] | None = None
        self._queue: queue.Queue = queue.Queue(maxsize=300)
        self._tasks: list[asyncio.Task] = []
        self._thread: threading.Thread | None = None
        self._has_audio = False

    def start(self, video: MediaStreamTrack, audio: MediaStreamTrack | None):
        self._has_audio = audio is not None
        self._thread = threading.Thread(target=self._write, name="recorder", daemon=True)
        self._thread.start()
        self._tasks = [asyncio.ensure_future(self._pump(t)) for t in (video, audio) if t is not None]

    async def _pump(self, track: MediaStreamTrack):
        while True:
            try:
                frame = await track.recv()
            except MediaStreamError:
                return
            try:
                self._queue.put_nowait((track.kind, frame))
            except queue.Full:
                pass  # writer is behind: drop a frame rather than grow memory

    def _write(self):
        container = video = audio = None
        while (item := self._queue.get()) is not None:
            kind, frame = item
            if container is None:
                if kind != "video":
                    continue
                scale = self.long_side / max(frame.width, frame.height)
                self.size = (round(frame.width * scale / 2) * 2, round(frame.height * scale / 2) * 2)
                container = av.open(str(self.path), "w")
                video = container.add_stream("libx264", rate=30)
                video.width, video.height = self.size
                video.pix_fmt = "yuv420p"
                audio = container.add_stream("aac") if self._has_audio else None
            if kind == "video":
                if (frame.width, frame.height) != self.size:
                    scaled = frame.reformat(width=self.size[0], height=self.size[1])
                    scaled.pts, scaled.time_base = frame.pts, frame.time_base
                    frame = scaled
                self.video_frames += 1
                packets = video.encode(frame)
            elif audio is not None:
                packets = audio.encode(frame)
            else:
                continue
            for packet in packets:
                container.mux(packet)
        if container is not None:
            for stream in (video, audio):
                if stream is not None:
                    for packet in stream.encode(None):
                        container.mux(packet)
            container.close()

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        if self._thread is not None:
            await asyncio.to_thread(self._queue.put, None)
            await asyncio.to_thread(self._thread.join)


class Session:
    """One call from the phone: its recording, latest frame and instruction channel."""

    def __init__(self, pc: RTCPeerConnection):
        self.pc = pc
        self.started = time.time()
        self.folder = RECORD_DIR / datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        self.folder.mkdir(parents=True, exist_ok=True)
        self.recorder = SessionRecorder(self.folder / "session.mp4")
        self.channel = None
        self.tracks: list[str] = []
        self.media: dict[str, MediaStreamTrack] = {}
        self.latest_frame = None  # newest camera frame (BGR ndarray), for the tutor later
        self.latest_jpeg: bytes | None = None
        self.frames = 0
        self.fps = 0.0
        self.last_spoken: str | None = None
        self.tasks: list[asyncio.Task] = []
        self.closed = False
        self._closing: asyncio.Future | None = None

    def route(self) -> str | None:
        """How media reaches us: the phone's side of the chosen ICE pair, e.g. "relay 192.0.2.2:49160"
        (through TURN) or "host 192.168.1.23:40000" (direct). Uses aioice internals; diagnostics only."""
        try:
            pair = self.pc.sctp.transport.transport._connection._nominated.get(1)
            c = pair.remote_candidate if pair else None
            return f"{c.type} {c.host}:{c.port}" if c else None
        except AttributeError:
            return None

    def elapsed(self) -> float:
        return round(time.time() - self.started, 2)

    def log(self, event: str, **fields):
        """Append to log.jsonl; `t` is seconds since the recording started."""
        with open(self.folder / "log.jsonl", "a") as f:
            f.write(json.dumps({"t": self.elapsed(), "event": event, **fields}) + "\n")

    def send(self, msg: dict) -> bool:
        if self.channel is None or self.channel.readyState != "open":
            return False
        self.channel.send(json.dumps(msg))
        return True

    async def watch_video(self, track):
        """Keep the latest frame and a preview JPEG, and measure the frame rate."""
        last_jpeg = 0.0
        window_start, window_frames = time.time(), 0
        while True:
            frame = await track.recv()
            self.frames += 1
            window_frames += 1
            now = time.time()
            if now - window_start >= 1.0:
                self.fps = round(window_frames / (now - window_start), 1)
                window_start, window_frames = now, 0
            if now - last_jpeg >= 1.0 / PREVIEW_FPS:
                last_jpeg = now
                img = frame.to_ndarray(format="bgr24")
                self.latest_frame = img
                ok, jpg = await asyncio.to_thread(cv2.imencode, ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok:
                    self.latest_jpeg = jpg.tobytes()

    async def close(self):
        """Stop recording and hang up. Every caller waits until session.mp4 is finalized."""
        self.closed = True
        if self._closing is None:
            self._closing = asyncio.ensure_future(self._close())
        await asyncio.shield(self._closing)

    async def _close(self):
        for t in self.tasks:
            t.cancel()
        await self.recorder.stop()
        await self.pc.close()
        self.log("ended", frames=self.frames, recorded=self.recorder.video_frames, size=self.recorder.size)
        log.info("session %s ended after %.0f s", self.folder.name, self.elapsed())


session: Session | None = None


class Offer(BaseModel):
    sdp: str
    type: str


class Say(BaseModel):
    text: str


@app.get("/config")
async def config():
    """What the phone needs before calling: the TURN relay, if one is configured."""
    if not (TURN_URLS and TURN_SECRET):
        return {"iceServers": []}
    return {"iceServers": [{"urls": TURN_URLS, **turn_credentials()}]}


@app.post("/offer")
async def offer(body: Offer):
    """The phone calls in: answer its WebRTC offer and start recording."""
    global session
    if session is not None:
        await session.close()  # one phone at a time; a new call replaces the old one

    pc = RTCPeerConnection(RTC_CONFIG)
    s = Session(pc)
    session = s

    @pc.on("datachannel")
    def on_datachannel(channel):
        s.channel = channel

        @channel.on("message")
        def on_message(message):
            try:
                msg = json.loads(message)
            except (TypeError, ValueError):
                return
            if msg.get("type") == "spoken":
                s.last_spoken = msg.get("text")
            s.log(f"phone:{msg.get('type')}", **{k: v for k, v in msg.items() if k != "type"})

    @pc.on("track")
    def on_track(track):
        s.tracks.append(track.kind)
        s.media[track.kind] = relay.subscribe(track)
        if track.kind == "video":
            s.tasks.append(asyncio.ensure_future(s.watch_video(relay.subscribe(track))))

    @pc.on("connectionstatechange")
    async def on_state():
        s.log("connection", state=pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await s.close()

    await pc.setRemoteDescription(RTCSessionDescription(sdp=body.sdp, type=body.type))
    if "video" in s.media:
        s.recorder.start(s.media["video"], s.media.get("audio"))
    await pc.setLocalDescription(await pc.createAnswer())
    if s.closed:  # hung up while we were answering
        raise HTTPException(409, "session ended before it started")
    s.log("started", tracks=s.tracks)
    asyncio.ensure_future(log_route(s))
    log.info("session %s started with %s", s.folder.name, s.tracks)
    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


async def log_route(s: Session):
    """Record how the phone connected (direct or relayed) once ICE has settled."""
    for _ in range(100):
        if s.closed:
            return
        if s.pc.connectionState == "connected" and (route := s.route()):
            s.log("route", route=route)
            return
        await asyncio.sleep(0.1)


@app.post("/say")
async def say(body: Say):
    """Speak an instruction on the phone."""
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "empty text")
    if session is None or not session.send({"type": "say", "text": text}):
        raise HTTPException(409, "phone not connected")
    session.log("say", text=text)
    return {"ok": True, "t": session.elapsed()}


@app.post("/hush")
async def hush():
    if session is None or not session.send({"type": "hush"}):
        raise HTTPException(409, "phone not connected")
    session.log("hush")
    return {"ok": True}


@app.post("/hangup")
async def hangup():
    if session is not None:
        await session.close()
    return {"ok": True}


@app.get("/status")
async def status():
    if session is None:
        return {"connected": False}
    return {
        "connected": session.pc.connectionState == "connected" and not session.closed,
        "state": session.pc.connectionState,
        "channel": session.channel.readyState if session.channel else None,
        "tracks": session.tracks,
        "seconds": session.elapsed(),
        "frames": session.frames,
        "fps": session.fps,
        "recording": str(session.folder / "session.mp4"),
        "last_spoken": session.last_spoken,
        "route": session.route(),
    }


@app.get("/snapshot.jpg")
async def snapshot():
    if session is None or session.latest_jpeg is None:
        raise HTTPException(404, "no video yet")
    return Response(session.latest_jpeg, media_type="image/jpeg")


@app.get("/preview.mjpg")
async def preview():
    async def frames():
        sent = None
        while True:
            jpg = session.latest_jpeg if session is not None else None
            if jpg is not None and jpg is not sent:
                sent = jpg
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
            await asyncio.sleep(1.0 / PREVIEW_FPS)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.on_event("shutdown")
async def shutdown():
    if session is not None:
        await session.close()
