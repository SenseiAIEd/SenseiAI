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
import re
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
from typing import Optional

from ears import Ears, Transcriber
from gaze import Attention, FaceFinder, Framer, Moment, read_expression
from head import GAZE_REPLY, PRESETS, Head, gaze_command
from jev import BACKENDS, Jev
from tutor import Brain, Tutor

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

# The tutor's vision model: an OpenAI-compatible endpoint on the Spark, e.g.
# SENSEI_LLM_URL=http://localhost:8000/v1  SENSEI_LLM_MODEL=qwen3-vl-30b-a3b-gguf  SENSEI_LLM_KEY=...
BRAIN = Brain.from_env()
# Optional second model for conversation (SENSEI_CHAT_MODEL): judging maths wants the slow,
# careful model; talking back wants one that answers before the student gives up waiting.
CHAT_BRAIN = Brain.chat_from_env()
# Fast typed decisions (jev.py): whether to answer, what about, whether the page is needed.
# Exists whenever it is configured; USE_JEV (or POST /jev) decides whether the tutor uses it.
JEV = Jev.from_env()
FRAME_EVERY_S = 0.5  # how often the tutor looks at the latest frame
# What this gateway can do; the app checks it so a button never silently does nothing.
FEATURES = ["look", "talk", "memory", "pause"]

# The tutor's ears: speech-to-text for voice mode (see ears.py). SENSEI_STT=off disables it.
HEAD = Head.from_env()  # the pan-tilt head, if SENSEI_HEAD_PORT names its USB serial port
# With a head: glance at the student when it helps, and centre their face (gaze.py).
AUTO_LOOK = {"on": os.environ.get("SENSEI_AUTO_LOOK", "on") != "off"}
# Demo / Aalo plant: detect but stay quiet until Hint/Check. Default off = Andy main unchanged.
TAP_ONLY = {"on": os.environ.get("SENSEI_TAP_ONLY", "0").lower() in ("1", "true", "yes", "on")}
MANUAL_HOLD_S = 30.0  # after "look at me" or a console button, Sensei doesn't move on its own for this long
TRANSCRIBER = Transcriber() if os.environ.get("SENSEI_STT", "on") != "off" else None

log = logging.getLogger("sensei")
# uvicorn configures its own loggers only, so without this every log.info here goes nowhere -
# including "ears ready" and the model warm-up, the two things you watch for on a cold start.
log.setLevel(os.environ.get("SENSEI_LOG_LEVEL", "INFO"))
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(name)s: %(message)s")
app = FastAPI()
relay = MediaRelay()


PATH_PREFIX = "/sensei"


@app.middleware("http")
async def require_key(request: Request, call_next):
    # Also served under /sensei/..., for a Funnel path on the standard HTTPS port
    # (tailscale funnel --set-path /sensei ...), which some networks allow when :8443 isn't.
    path = request.scope["path"]
    if path == PATH_PREFIX or path.startswith(PATH_PREFIX + "/"):
        path = request.scope["path"] = path[len(PATH_PREFIX):] or "/"
    if ACCESS_KEY and path != "/":  # the console page itself holds no data
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
        self.tutor: Tutor | None = None
        self.judged = 0
        self.voice = False  # the student's voice mode: the phone mutes its mic unless this is on
        self.last_look: dict | None = None  # the latest frame the model judged, and what it made of it
        self.last_heard: str | None = None  # the latest thing the student said (voice mode)
        self.last_heard_at = -1e9
        # The head (gaze.py): where it points, and the last frame of the page while it pointed there.
        self.latest_frame_at = 0.0
        self.looking_at = "notebook"  # notebook | student | elsewhere | moving
        self.looked_at_since = time.monotonic()
        self.last_glance_at = -1e9
        self.manual_until = 0.0
        self.page_frame = None
        self.glance = None       # gaze.Glance: the last look at the student
        self.face_jpeg: bytes | None = None
        self.attention = Attention(JEV)
        self.framer: Framer | None = None  # made on the first glance

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
        if event == "tutor_assessment":
            self.last_look = {"t": self.elapsed(), **fields}
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
                self.latest_frame, self.latest_frame_at = img, time.monotonic()
                if self.looking_at == "notebook":
                    self.page_frame = img
                ok, jpg = await asyncio.to_thread(cv2.imencode, ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if ok:
                    self.latest_jpeg = jpg.tobytes()

    async def start_tutor(self, minutes: float):
        """Begin an interactive session (replacing any running one)."""
        if self.tutor is not None and self.tutor.phase == "watching":
            await self.tutor.end("restarted")

        async def speak(text: str, why: str):
            self.send({"type": "say", "text": text, "why": why})

        async def notify(state: dict):
            self.send(state)

        self.tutor = Tutor(BRAIN, speak, notify, minutes=minutes, log_event=self.log,
                           save_frame=self.save_judged_frame, chat_brain=CHAT_BRAIN, decider=JEV,
                           tap_only=TAP_ONLY["on"])
        await self.tutor.start()
        if HEAD is not None:  # the tutor watches the notebook
            asyncio.ensure_future(self.point("notebook", "session start"))
        if not any(getattr(t, "sensei_role", "") == "tutor" for t in self.tasks):
            task = asyncio.ensure_future(self.run_tutor())
            task.sensei_role = "tutor"
            self.tasks.append(task)
        if HEAD is not None and not any(getattr(t, "sensei_role", "") == "gaze" for t in self.tasks):
            task = asyncio.ensure_future(self.run_gaze())
            task.sensei_role = "gaze"
            self.tasks.append(task)

    def save_judged_frame(self, img) -> str:
        """Keep the exact frame the model is about to judge (judged_001.jpg, ...)."""
        self.judged += 1
        name = f"judged_{self.judged:03d}.jpg"
        cv2.imwrite(str(self.folder / name), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        return name

    async def run_tutor(self):
        """Feed the tutor frames and timer ticks. Model calls run as their own tasks."""
        last_tick = 0.0
        while True:
            await asyncio.sleep(FRAME_EVERY_S)
            t = self.tutor
            if t is None or t.phase != "watching":
                continue
            # Only the page is the page: frames of the student's face are not homework.
            if self.latest_frame is not None and not t.thinking and self.looking_at == "notebook":
                asyncio.ensure_future(t.on_frame(self.latest_frame))
            if time.monotonic() - last_tick >= 1.0:
                last_tick = time.monotonic()
                await t.tick()

    async def heard(self, text: str, info: dict):
        """The student said something in voice mode."""
        self.send({"type": "heard", "text": text})
        self.last_heard, self.last_heard_at = text, time.monotonic()
        preset = gaze_command(text) if HEAD is not None else None
        if preset is not None:
            await self.gaze(preset, text, info)
        elif self.tutor is not None:
            self.log("heard", text=text, **info)
            await self.tutor.hear(text, self.page())
        else:
            self.log("student_said", text=text, **info)

    async def gaze(self, preset: str, text: str, info: dict):
        """'Look at my notebook': point the head, and say so (a command, not a question for the model)."""
        self.log("gaze", preset=preset, text=text, **info)
        self.manual_until = time.monotonic() + MANUAL_HOLD_S
        ok = await self.point(preset, "asked out loud")
        reply = GAZE_REPLY[preset] if ok else "Sorry, I can't move my head right now."
        if self.tutor is not None and self.tutor.phase == "watching":
            self.tutor.remember("student", text)
            await self.tutor.speak(reply, "gaze")
        else:
            self.send({"type": "say", "text": reply, "why": "gaze"})

    # -- the head: where to look ------------------------------------------------------------
    def page(self):
        """The newest frame of the page, even while the head looks at the student."""
        if self.looking_at == "notebook" or self.page_frame is None:
            return self.latest_frame
        return self.page_frame

    async def frame_after(self, t: float, timeout: float = 3.0):
        """The first frame that left the phone after monotonic time t (+ the video's delay)."""
        deadline = time.monotonic() + timeout
        while self.latest_frame_at < t + 0.35 and time.monotonic() < deadline and not self.closed:
            await asyncio.sleep(0.05)
        return self.latest_frame

    async def point(self, target: str, why: str, read_face: bool = True) -> bool:
        """Turn the head to a preset. At the student: centre their face, then read it."""
        self.looking_at = "moving"
        ok = await HEAD.look(target)
        self.looking_at = target if ok else "elsewhere"
        self.looked_at_since = time.monotonic()
        self.log("look", at=target, why=why, ok=ok, **({"error": HEAD.error} if not ok else {}))
        if ok and target == "student":
            self.last_glance_at = time.monotonic()
            await self.glance_at_student(read_face)
        return ok

    async def glance_at_student(self, read_face: bool = True):
        if self.framer is None or self.framer.head is not HEAD:
            self.framer = Framer(HEAD, FaceFinder(), self.frame_after)
        self.looking_at = "moving"
        try:
            g = await self.framer.frame_face()
        finally:
            self.looking_at = "student"
        self.looked_at_since = time.monotonic()
        brain = CHAT_BRAIN or BRAIN
        if g.image is not None and g.face is not None and read_face and brain is not None:
            try:
                g.expression = await asyncio.to_thread(read_expression, brain, g.image, g.face)
            except Exception as e:
                g.note = (g.note + f"; expression unavailable: {e}").strip("; ")
        if g.image is not None:
            ok, jpg = await asyncio.to_thread(cv2.imencode, ".jpg", g.image, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                self.face_jpeg = jpg.tobytes()
        self.glance = g
        self.log("glance", **g.summary())
        if g.expression and self.tutor is not None:
            self.tutor.note_face(g.expression)

    def moment(self) -> Moment:
        t, now = self.tutor, time.monotonic()
        return Moment(
            looking_at=self.looking_at, seconds_here=now - self.looked_at_since,
            seconds_since_glance=now - self.last_glance_at,
            sensei_asked_a_question=t.awaiting_answer(),
            seconds_since_sensei_spoke=t.clock() - t.last_spoke_at,
            seconds_since_student_spoke=now - self.last_heard_at,
            student_last_said=self.last_heard or "",
            seconds_since_page_activity=t.clock() - t.last_activity,
            hints_given=t.hints_given,
            last_expression=(self.glance.expression if self.glance and self.glance.expression else "unknown"),
            sensei_thinking=t.thinking)

    async def run_gaze(self):
        """Once a second: should the head look somewhere else?"""
        while True:
            await asyncio.sleep(1.0)
            t = self.tutor
            if (not AUTO_LOOK["on"] or t is None or t.phase != "watching" or self.looking_at == "moving"
                    or time.monotonic() < self.manual_until):
                continue
            try:
                target = await asyncio.to_thread(self.attention.decide, self.moment())
                if target is not None:
                    await self.point(target, self.attention.last_reason)
            except Exception:
                log.exception("gaze decision failed")

    async def tutor_request(self, what: str, minutes: float = 10):
        if what == "start":
            await self.start_tutor(minutes)
        elif self.tutor is not None:
            await self.tutor.request(what, self.page())

    async def close(self):
        """Stop recording and hang up. Every caller waits until session.mp4 is finalized."""
        self.closed = True
        if self._closing is None:
            self._closing = asyncio.ensure_future(self._close())
        await asyncio.shield(self._closing)

    async def _close(self):
        if self.tutor is not None:
            self.tutor.stop()
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


class Gaze(BaseModel):
    preset: str | None = None  # notebook | student | home
    pan: int | None = None     # absolute servo degrees (0-180, 90 = centred) ...
    tilt: int | None = None
    nudge_pan: int = 0         # ... or relative, for calibrating presets from the console
    nudge_tilt: int = 0
    save: str | None = None    # store where the head points now as this preset (on the ESP32)
    limit: str | None = None   # "pan" | "tilt": set that axis's soft limits to [lo, hi]
    lo: int | None = None
    hi: int | None = None
    find_face: bool = False    # centre the student's face from where the head points now
    auto: bool | None = None   # switch automatic glances on/off


class TutorAction(BaseModel):
    action: str  # start | hint | check | look | repeat | pause | resume | end
    minutes: float = 10


class JevSwitch(BaseModel):
    on: Optional[bool] = None
    backend: Optional[str] = None  # jevk5 | semif | hosted


class TapOnlySwitch(BaseModel):
    on: bool


def jev_state() -> dict:
    if JEV is None:
        return {"on": False, "available": False}
    return {"on": JEV.enabled, "available": True, "backend": JEV.backend, "where": JEV.where,
            "url": JEV.url, "backends": sorted(BACKENDS),
            "floors": {"reply": JEV.skip_below, "reply_after_question": JEV.skip_if_answer_below,
                       "no_page": JEV.no_page_below}}


class BrainChoice(BaseModel):
    model: str  # one of the ids from GET /brain


@app.get("/config")
async def config():
    """What the phone needs before calling: the TURN relay (if configured) and whether the
    tutor is here and has a model. (Gateways from before the tutor don't send "tutor".)"""
    ice = [{"urls": TURN_URLS, **turn_credentials()}] if TURN_URLS and TURN_SECRET else []
    return {"iceServers": ice, "tutor": {"brain": BRAIN.model if BRAIN else None,
                                         "chat_brain": CHAT_BRAIN.model if CHAT_BRAIN else None,
                                         "jev": jev_state(),
                                         "tap_only": TAP_ONLY["on"],
                                         "ears": TRANSCRIBER.name if TRANSCRIBER else None,
                                         "features": FEATURES}}


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
            kind = msg.get("type")
            if kind == "spoken":
                s.last_spoken = msg.get("text")
            elif kind == "voice":
                s.voice = bool(msg.get("on"))
            s.log(f"phone:{kind}", **{k: v for k, v in msg.items() if k != "type"})
            # The student's buttons: start / hint / check / repeat / end.
            if kind == "start":
                asyncio.ensure_future(s.tutor_request("start", float(msg.get("minutes") or 10)))
            elif kind == "request" and msg.get("what") in ("hint", "check", "look", "repeat", "pause", "resume", "end"):
                asyncio.ensure_future(s.tutor_request(msg["what"]))

    @pc.on("track")
    def on_track(track):
        s.tracks.append(track.kind)
        s.media[track.kind] = relay.subscribe(track)
        if track.kind == "video":
            s.tasks.append(asyncio.ensure_future(s.watch_video(relay.subscribe(track))))
        elif track.kind == "audio" and TRANSCRIBER is not None:
            ears = Ears(TRANSCRIBER, s.heard, listening=lambda: s.voice and not s.closed)
            s.tasks.append(asyncio.ensure_future(ears.run(relay.subscribe(track))))

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


@app.post("/tutor")
async def tutor_action(body: TutorAction):
    """Drive the tutor from the console (same as the phone's buttons)."""
    if body.action not in ("start", "hint", "check", "look", "repeat", "pause", "resume", "end"):
        raise HTTPException(400, "unknown action")
    if session is None or session.closed:
        raise HTTPException(409, "phone not connected")
    await session.tutor_request(body.action, body.minutes)
    return {"ok": True, "tutor": session.tutor.state() if session.tutor else None}


@app.post("/head")
async def head(body: Gaze):
    """Point the pan-tilt head: a preset, absolute angles, or a nudge."""
    if HEAD is None:
        raise HTTPException(409, "no pan-tilt head (set SENSEI_HEAD_PORT, e.g. /dev/ttyUSB0)")
    if body.auto is not None:
        AUTO_LOOK["on"] = body.auto
        return {"ok": True, "head": head_state()}
    live = session is not None and not session.closed
    if live:
        session.manual_until = time.monotonic() + MANUAL_HOLD_S
    if body.preset is not None:
        if body.preset not in PRESETS:
            raise HTTPException(400, f"preset must be one of {', '.join(PRESETS)}")
        ok = await (session.point(body.preset, "console") if live else HEAD.look(body.preset))
    elif body.pan is not None and body.tilt is not None:
        ok = await HEAD.move(body.pan, body.tilt)
    elif body.nudge_pan or body.nudge_tilt:
        ok = await HEAD.nudge(body.nudge_pan, body.nudge_tilt)
    elif body.save is not None:
        if body.save not in PRESETS:
            raise HTTPException(400, f"preset must be one of {', '.join(PRESETS)}")
        ok = await HEAD.save(body.save)
    elif body.limit is not None:
        if body.limit not in ("pan", "tilt") or body.lo is None or body.hi is None or body.lo >= body.hi:
            raise HTTPException(400, "limit needs axis pan|tilt and lo < hi")
        ok = await HEAD.set_limit(body.limit, body.lo, body.hi)
    elif body.find_face:
        if not live:
            raise HTTPException(409, "phone not connected")
        await session.glance_at_student()
        ok = True
    else:
        ok = await HEAD.connect()
    if session is not None and not session.closed:
        session.log("head", request=body.model_dump(exclude_defaults=True), ok=ok, **HEAD.state())
    if not ok:
        raise HTTPException(502, HEAD.error or "pan-tilt head failed")
    return {"ok": True, "head": head_state()}


def head_state() -> dict | None:
    if HEAD is None:
        return None
    st = {**HEAD.state(), "auto": AUTO_LOOK["on"]}
    if session is not None:
        st.update(looking_at=session.looking_at, why=session.attention.last_reason,
                  jev=session.attention.last_jev,
                  glance=session.glance.summary() if session.glance else None,
                  manual_hold_s=max(0, round(session.manual_until - time.monotonic())))
    return st


@app.get("/face.jpg")
async def face_jpg():
    """The frame from Sensei's last glance at the student."""
    if session is None or session.face_jpeg is None:
        raise HTTPException(404, "no glance yet")
    return Response(session.face_jpeg, media_type="image/jpeg")


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
        return {"connected": False, "head": head_state()}
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
        "voice": session.voice,
        "heard": session.last_heard,
        "tutor": session.tutor.state() if session.tutor else None,
        "conversation": session.tutor.conversation[-10:] if session.tutor else [],
        "brain": BRAIN.model if BRAIN else None,
        "jev": jev_state(),
        "ears": TRANSCRIBER.name if TRANSCRIBER else None,
        "head": head_state(),
    }


@app.get("/brain")
async def which_brain():
    """The model in use and the ones this endpoint can serve, so you can try another."""
    if BRAIN is None:
        raise HTTPException(409, "no model configured (set SENSEI_LLM_URL)")
    try:
        available = await asyncio.to_thread(BRAIN.list_models)
    except Exception as e:  # the list is a convenience; the current model still works
        log.warning("could not list models: %s", e)
        available = []
    return {"model": BRAIN.model, "available": available}


@app.post("/brain")
async def choose_brain(body: BrainChoice):
    """Switch the tutor to another model without a restart.

    The first look afterwards pays the router's load (minutes for a 30B), because it serves one
    model at a time. Warming happens here rather than leaving the student to wait for it."""
    if BRAIN is None:
        raise HTTPException(409, "no model configured (set SENSEI_LLM_URL)")
    was, BRAIN.model = BRAIN.model, body.model
    log.info("brain: %s -> %s", was, BRAIN.model)
    t0 = time.time()
    try:
        await asyncio.to_thread(BRAIN.ping)
    except Exception as e:
        BRAIN.model = was  # it can't answer: don't leave the tutor pointed at a dead model
        raise HTTPException(502, f"{body.model} did not answer ({e}); staying on {was}")
    if session is not None:
        session.log("brain_changed", was=was, now=BRAIN.model)
    return {"model": BRAIN.model, "was": was, "warm_up_s": round(time.time() - t0, 1)}


@app.get("/jev")
async def which_jev():
    """Whether the tutor is using Jev for its fast decisions (USE_JEV, switchable at runtime)."""
    return jev_state()


@app.post("/jev")
async def switch_jev(body: JevSwitch):
    """Turn Jev on or off, or change backend, without a restart. Off means Sensei decides
    exactly as it did before."""
    if JEV is None:
        raise HTTPException(409, "Jev is not configured (set SENSEI_JEV_BACKEND, or a key for hosted)")
    if body.backend is not None:
        try:
            JEV.use(body.backend)
        except ValueError as e:
            raise HTTPException(400, str(e))
    if body.on is not None:
        JEV.enabled = body.on
    log.info("jev %s, backend %s", "on" if JEV.enabled else "off", JEV.backend)
    if session is not None:
        session.log("jev_switched", on=JEV.enabled, backend=JEV.backend)
    return jev_state()


@app.get("/tap_only")
async def which_tap_only():
    """Whether background looks stay quiet until Hint/Check (SENSEI_TAP_ONLY / plant demo)."""
    live = session.tutor.tap_only if session is not None and session.tutor is not None else TAP_ONLY["on"]
    return {"on": live, "default": TAP_ONLY["on"]}


@app.post("/tap_only")
async def switch_tap_only(body: TapOnlySwitch):
    """Turn tap-only / quiet-until-Hint on or off without a restart. Default remains off."""
    TAP_ONLY["on"] = bool(body.on)
    if session is not None and session.tutor is not None:
        session.tutor.tap_only = TAP_ONLY["on"]
    log.info("tap_only %s", "on" if TAP_ONLY["on"] else "off")
    if session is not None:
        session.log("tap_only_switched", on=TAP_ONLY["on"])
    return {"on": TAP_ONLY["on"]}


@app.get("/last_look")
async def last_look():
    """What Sensei last looked at: the frame file, what the model read and said, and how long it took."""
    return {"look": session.last_look if session is not None else None}


@app.get("/judged/{name}")
async def judged_frame(name: str):
    """A frame the model judged in the current session (judged_NNN.jpg)."""
    if session is None or not re.fullmatch(r"judged_\d{3}\.jpg", name):
        raise HTTPException(404, "no such frame")
    path = session.folder / name
    if not path.exists():
        raise HTTPException(404, "no such frame")
    return FileResponse(path, media_type="image/jpeg")


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


@app.on_event("startup")
async def warm_up_ears():
    """Load the speech model in the background so the first answer isn't slow."""
    if TRANSCRIBER is not None:
        async def load():
            try:
                await asyncio.to_thread(TRANSCRIBER.load)
                log.info("ears ready: %s", TRANSCRIBER.name)
            except Exception as e:
                log.warning("speech-to-text unavailable (%s); voice mode will not understand speech", e)
        asyncio.ensure_future(load())


@app.on_event("startup")
async def wake_head():
    """Connect to the pan-tilt head (the ESP32 reboots when its port opens, so do it early)."""
    if HEAD is not None:
        async def wake():
            if await HEAD.connect():
                log.info("pan-tilt head ready on %s at pan %s tilt %s", HEAD.port, HEAD.pan, HEAD.tilt)
        asyncio.ensure_future(wake())


@app.on_event("startup")
async def warm_up_brains():
    """Ask each model a trivial question so it is resident before a student arrives.

    A router that has to load a 30B model answers the first request minutes later - longer
    than SENSEI_LLM_TIMEOUT, so the student's first look would simply fail."""
    async def warm_each():
        # One at a time: a router loading a 30B model has nothing to spare for a second
        # request, and two pings at once means both wait and one times out. Quick model first,
        # so the conversation is ready even while the careful one is still loading.
        warmed = set()
        for brain, what in ((CHAT_BRAIN, "chat brain"), (BRAIN, "brain")):
            if brain is None or brain.model in warmed:
                continue
            warmed.add(brain.model)
            t0 = time.time()
            try:
                await asyncio.to_thread(brain.ping)
                log.info("%s ready: %s (%.0fs)", what, brain.model, time.time() - t0)
            except Exception as e:
                log.warning("%s (%s) did not warm up: %s", what, brain.model, e)

    asyncio.ensure_future(warm_each())


@app.on_event("shutdown")
async def shutdown():
    if session is not None:
        await session.close()
    if HEAD is not None:
        HEAD.close()
