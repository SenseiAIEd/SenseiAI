"""End-to-end gateway test with the simulated phone. Run: pytest gateway/"""
import asyncio
import json
import socket
import threading
import time

import av
import httpx
import pytest
import uvicorn

import server
from fake_phone import FakePhone


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "RECORD_DIR", tmp_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", tmp_path
    srv.should_exit = True
    thread.join(timeout=10)
    server.session = None


def wait_for(check, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.1)
    return False


def test_call_preview_say_and_record(gateway):
    base, record_dir = gateway
    http = httpx.Client(base_url=base, timeout=10)
    assert http.post("/say", json={"text": "hello"}).status_code == 409  # nobody connected yet

    async def run_phone():
        phone = FakePhone(base)
        await phone.connect()
        await asyncio.to_thread(check_while_live, http)
        await asyncio.sleep(0.5)  # let the "spoken" reply arrive
        heard = list(phone.heard)
        await phone.close()
        return heard

    def check_while_live(http):
        assert wait_for(lambda: http.get("/status").json().get("connected"))
        assert wait_for(lambda: http.get("/status").json()["frames"] > 20)  # ~1.5 s of video
        status = http.get("/status").json()
        assert sorted(status["tracks"]) == ["audio", "video"]
        assert status["channel"] == "open"

        snap = http.get("/snapshot.jpg")
        assert snap.status_code == 200 and snap.content[:2] == b"\xff\xd8"  # a JPEG

        assert http.post("/say", json={"text": "Please place your notebook under the camera."}).json()["ok"]
        assert wait_for(lambda: http.get("/status").json()["last_spoken"] is not None)

    heard = asyncio.run(run_phone())
    assert heard == ["Please place your notebook under the camera."]

    http.post("/hangup")
    [folder] = record_dir.iterdir()
    events = [json.loads(line) for line in (folder / "log.jsonl").read_text().splitlines()]
    kinds = [e["event"] for e in events]
    for expected in ("started", "phone:hello", "say", "phone:spoken", "ended"):
        assert expected in kinds

    with av.open(str(folder / "session.mp4")) as mp4:
        kinds = sorted(s.type for s in mp4.streams)
        assert kinds == ["audio", "video"]
        assert mp4.streams.video[0].width == 1280
        assert sum(1 for _ in mp4.decode(video=0)) > 15


def test_say_rejects_empty_text(gateway):
    base, _ = gateway
    assert httpx.post(f"{base}/say", json={"text": "  "}).status_code == 400


class ListTrack:
    """A track that plays a list of frames, then ends like a hung-up call."""

    def __init__(self, kind, frames, delay=0.0):
        self.kind, self.frames, self.delay = kind, list(frames), delay

    async def recv(self):
        await asyncio.sleep(self.delay)
        if not self.frames:
            raise server.MediaStreamError
        return self.frames.pop(0)


def video_frames(n, width, height, start=0):
    from fractions import Fraction
    import numpy as np
    for i in range(n):
        f = av.VideoFrame.from_ndarray(np.full((height, width, 3), 200, np.uint8), format="bgr24")
        f.pts, f.time_base = (start + i) * 6000, Fraction(1, 90000)  # 15 fps
        yield f


def audio_frames(n):
    from fractions import Fraction
    import numpy as np
    for i in range(n):
        f = av.AudioFrame.from_ndarray(np.zeros((1, 960 * 2), np.int16), format="s16", layout="stereo")
        f.sample_rate, f.pts, f.time_base = 48000, i * 960, Fraction(1, 48000)
        yield f


def test_recording_keeps_full_size_when_audio_arrives_first(tmp_path):
    # Audio (every 20 ms) lands before the first video frame, which is small (ramp-up).
    path = tmp_path / "session.mp4"

    async def record():
        rec = server.SessionRecorder(path)
        frames = list(video_frames(5, 640, 360)) + list(video_frames(25, 1280, 720, start=5))
        rec.start(ListTrack("video", frames, delay=0.03), ListTrack("audio", audio_frames(60), delay=0.02))
        await asyncio.sleep(1.2)
        await rec.stop()
        return rec

    rec = asyncio.run(record())
    assert rec.size == (1280, 720)
    with av.open(str(path)) as mp4:
        assert (mp4.streams.video[0].width, mp4.streams.video[0].height) == (1280, 720)
        assert sorted(s.type for s in mp4.streams) == ["audio", "video"]
        assert sum(1 for _ in mp4.decode(video=0)) == 30


def test_recording_keeps_portrait_orientation(tmp_path):
    path = tmp_path / "session.mp4"

    async def record():
        rec = server.SessionRecorder(path)
        rec.start(ListTrack("video", video_frames(10, 360, 640)), None)
        await asyncio.sleep(0.3)
        await rec.stop()
        return rec

    assert asyncio.run(record()).size == (720, 1280)


def test_access_key_protects_everything_but_the_page(gateway, monkeypatch):
    base, _ = gateway
    monkeypatch.setattr(server, "ACCESS_KEY", "s3cret")
    assert httpx.get(f"{base}/").status_code == 200  # the console page holds no data
    for path in ("/status", "/config", "/snapshot.jpg"):
        assert httpx.get(f"{base}{path}").status_code == 401
    assert httpx.post(f"{base}/offer", json={"sdp": "", "type": "offer"}).status_code == 401
    assert httpx.get(f"{base}/status", headers={"X-Sensei-Key": "wrong"}).status_code == 401
    assert httpx.get(f"{base}/status", headers={"X-Sensei-Key": "s3cret"}).status_code == 200
    assert httpx.get(f"{base}/status?key=s3cret").status_code == 200  # console's preview image
    assert httpx.get(f"{base}/status", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_config_hands_out_coturn_rest_credentials(gateway, monkeypatch):
    import base64, hashlib, hmac
    base, _ = gateway
    config = httpx.get(f"{base}/config").json()
    assert config["iceServers"] == [] and config["tutor"]["brain"] is None  # no relay, no model
    assert set(config["tutor"]["features"]) >= {"look", "talk", "memory", "pause"}  # the app checks these

    monkeypatch.setattr(server, "TURN_URLS", ["turns:spark.example.ts.net:10000?transport=tcp"])
    monkeypatch.setattr(server, "TURN_SECRET", "turn-secret")
    [ice] = httpx.get(f"{base}/config").json()["iceServers"]
    assert ice["urls"] == ["turns:spark.example.ts.net:10000?transport=tcp"]
    # coturn (use-auth-secret) accepts: username "<expiry unix time>:<anything>",
    # password base64(HMAC-SHA1(secret, username)).
    expiry = int(ice["username"].split(":")[0])
    assert time.time() + server.TURN_TTL_S - 60 < expiry <= time.time() + server.TURN_TTL_S + 1
    expected = hmac.new(b"turn-secret", ice["username"].encode(), hashlib.sha1).digest()
    assert base64.b64decode(ice["credential"]) == expected


def test_the_model_can_be_swapped_without_a_restart(gateway, monkeypatch):
    """Trying the fast model against the careful one shouldn't mean editing a file and
    restarting mid-session. A model that can't answer is rejected, not adopted."""
    base, _ = gateway

    class Brain:
        base_url = "http://model"
        model = "thinking"

        def list_models(self):
            return ["fast", "thinking"]

        def ping(self):
            if self.model == "broken":
                raise RuntimeError("no such model")
            return "ready"

    monkeypatch.setattr(server, "BRAIN", Brain())

    listed = httpx.get(f"{base}/brain").json()
    assert listed["model"] == "thinking" and listed["available"] == ["fast", "thinking"]

    swapped = httpx.post(f"{base}/brain", json={"model": "fast"}).json()
    assert swapped == {"model": "fast", "was": "thinking", "warm_up_s": swapped["warm_up_s"]}
    assert httpx.get(f"{base}/brain").json()["model"] == "fast"

    refused = httpx.post(f"{base}/brain", json={"model": "broken"})
    assert refused.status_code == 502
    assert httpx.get(f"{base}/brain").json()["model"] == "fast"  # still on the working one


def test_student_starts_a_session_and_gets_a_hint(gateway, monkeypatch):
    """Phone taps Start over the data channel; the tutor greets, looks at the streamed page
    and speaks its hint through the same channel."""
    from tutor import Assessment
    base, _ = gateway

    class Brain:
        model = "scripted"
        calls = 0

        def assess(self, img, instructions, system=""):
            Brain.calls += 1
            assert img.shape[0] > 100  # a real decoded camera frame
            return Assessment(page="work", problem="5 - (2x - 4) = 11", steps=["5 - 2x - 4 = 11"],
                              first_error=1, error_kind="sign",
                              say="Look at your first line: what happens to the minus four?")

    monkeypatch.setattr(server, "BRAIN", Brain())

    async def run():
        phone = FakePhone(base)
        await phone.connect()
        phone.send({"type": "start", "minutes": 5})
        for _ in range(100):  # the page is still -> judged after ~1.5 s
            if any(m.get("why") == "hint_1" for m in phone.messages):
                break
            await asyncio.sleep(0.1)
        phone.send({"type": "request", "what": "end"})
        await asyncio.sleep(0.5)
        await phone.close()
        return phone.messages

    messages = asyncio.run(run())
    whys = [m.get("why") for m in messages if m["type"] == "say"]
    assert whys[:2] == ["greeting", "hint_1"] and "wrap_up" in whys
    states = [m for m in messages if m["type"] == "tutor"]
    assert states and states[0]["phase"] == "watching" and 0 < states[0]["remaining_s"] <= 300
    assert messages[-1]["type"] == "session_ended"
    # A mistake is never spoken about on one reading: the same page is looked at twice, and only
    # then does the hint go out. (An unchanging correct page is still judged just once.)
    assert Brain.calls == 2
    # the frame the model judged is kept, with its size, next to the recording
    [folder] = [f for f in server.RECORD_DIR.iterdir() if (f / "judged_001.jpg").exists()]
    judged = [json.loads(l) for l in (folder / "log.jsonl").read_text().splitlines()
              if '"tutor_assessment"' in l]
    assert judged[0]["frame"] == "judged_001.jpg" and judged[0]["frame_size"][0] > 0
    # ...and the console can show it: the frame the hint was actually based on (the second
    # look, the one that confirmed the mistake) and what the model made of it
    look = server.session.last_look
    assert look["frame"] == "judged_002.jpg" and look["say"].startswith("Look at your first line")


@pytest.mark.skipif(__import__("shutil").which("espeak-ng") is None, reason="needs espeak-ng for a test voice")
def test_student_asks_out_loud_and_sensei_answers(gateway, monkeypatch, tmp_path):
    """Voice mode end to end: the phone's mic carries a spoken question over WebRTC, the
    Spark transcribes it, and the tutor answers it looking at the streamed page."""
    import subprocess
    from tutor import Assessment
    base, _ = gateway
    wav = tmp_path / "question.wav"
    subprocess.run(["espeak-ng", "-v", "en-us", "-s", "150", "-w", str(wav), "is my first line right"], check=True)
    subprocess.run(["sox", str(wav), str(tmp_path / "q.wav"), "pad", "1", "2"], check=False)
    padded = tmp_path / "q.wav"
    wav = padded if padded.exists() else wav

    asked = []

    class Brain:
        model = "scripted"

        def assess(self, img, instructions, system=""):
            asked.append((img is not None, instructions))
            return Assessment(page="work", steps=["5 - 2x - 4 = 11"], say="Almost. Look at the minus four.")

    monkeypatch.setattr(server, "BRAIN", Brain())

    async def run():
        phone = FakePhone(base, audio_file=str(wav))
        await phone.connect()
        phone.send({"type": "start", "minutes": 5})
        phone.send({"type": "voice", "on": True})
        for _ in range(600):  # generous: speech-to-text runs on the CPU
            if any(m.get("why") == "reply" for m in phone.messages):
                break
            await asyncio.sleep(0.1)
        await phone.close()
        return phone.messages

    messages = asyncio.run(run())
    heard = [m["text"] for m in messages if m["type"] == "heard"]
    assert heard and "first line" in heard[0].lower()
    replies = [m["text"] for m in messages if m.get("why") == "reply"]
    assert replies == ["Almost. Look at the minus four."]
    talk = [i for has_img, i in asked if "said out loud" in i]
    assert talk and "first line" in talk[0].lower()


def test_jev_can_be_switched_on_and_off_at_runtime(gateway, monkeypatch):
    from jev import Jev
    base, _ = gateway
    monkeypatch.setattr(server, "JEV", None)
    assert httpx.get(f"{base}/jev").json() == {"on": False, "available": False}
    assert httpx.post(f"{base}/jev", json={"on": True}).status_code == 409  # nothing to switch on

    monkeypatch.setattr(server, "JEV", Jev("http://localhost:1/v1/systemone", enabled=False))
    assert httpx.get(f"{base}/jev").json()["on"] is False
    assert httpx.post(f"{base}/jev", json={"on": True}).json()["on"] is True
    assert httpx.get(f"{base}/config").json()["tutor"]["jev"]["on"] is True
    assert httpx.post(f"{base}/jev", json={"on": False}).json()["on"] is False


def test_jev_backend_can_be_changed_at_runtime(gateway, monkeypatch):
    from jev import Jev
    base, _ = gateway
    monkeypatch.setattr(server, "JEV", Jev("http://127.0.0.1:8095/v1/systemone", backend="jevk5"))
    state = httpx.post(f"{base}/jev", json={"backend": "semif"}).json()
    assert state["backend"] == "semif" and state["floors"]["reply"] == 0.15 and state["on"] is False
    assert httpx.post(f"{base}/jev", json={"backend": "hosted"}).status_code == 400  # no key
    jevk8 = httpx.post(f"{base}/jev", json={"backend": "jevk8"}).json()
    assert jevk8["backend"] == "jevk8" and jevk8["floors"]["reply"] == 0.25
    assert jevk8["floors"]["reply_after_question"] == 0.15
    assert httpx.post(f"{base}/jev", json={"backend": "jevk5", "on": True}).json()["on"] is True


def test_served_under_a_path_prefix_too(gateway, monkeypatch):
    base, _ = gateway
    monkeypatch.setattr(server, "ACCESS_KEY", "k")
    http = httpx.Client(base_url=base, timeout=10)
    assert http.get("/sensei/status").status_code == 401
    assert http.get("/sensei/status", headers={"X-Sensei-Key": "k"}).json()["connected"] is False
    assert http.get("/status", headers={"X-Sensei-Key": "k"}).status_code == 200
    assert http.get("/sensei/").status_code == 200 and http.get("/senseix").status_code in (401, 404)


# --- the SenseiDesk tab: a human tutor or parent watching from the web app ------------------------
def test_a_desk_sees_every_event_and_the_live_video(gateway):
    from aiortc import RTCPeerConnection, RTCSessionDescription

    base, _ = gateway
    http = httpx.Client(base_url=base, timeout=10)
    assert http.post("/watch", json={"sdp": "", "type": "offer"}).status_code == 409  # no phone yet
    seen: list[dict] = []
    stop = threading.Event()

    def listen():
        with httpx.stream("GET", f"{base}/events", timeout=30) as r:
            for line in r.iter_lines():
                if line.startswith("data: "):
                    seen.append(json.loads(line[6:]))
                if stop.is_set():
                    return

    async def desk_watches() -> int:
        pc = RTCPeerConnection()
        pc.addTransceiver("video", direction="recvonly")
        pc.addTransceiver("audio", direction="recvonly")
        got = asyncio.get_running_loop().create_future()

        @pc.on("track")
        def on_track(track):
            if track.kind == "video":
                async def first_frame():
                    frame = await track.recv()
                    if not got.done():
                        got.set_result(frame.width)
                asyncio.ensure_future(first_frame())

        await pc.setLocalDescription(await pc.createOffer())
        answer = await asyncio.to_thread(http.post, "/watch", json={"sdp": pc.localDescription.sdp,
                                                                     "type": pc.localDescription.type})
        assert answer.status_code == 200, answer.text
        await pc.setRemoteDescription(RTCSessionDescription(**answer.json()))
        width = await asyncio.wait_for(got, 15)
        await pc.close()
        return width

    async def run():
        phone = FakePhone(base)
        await phone.connect()
        assert await asyncio.to_thread(wait_for, lambda: http.get("/status").json().get("connected"))
        listener = threading.Thread(target=listen, daemon=True)
        listener.start()
        assert await asyncio.to_thread(wait_for, lambda: any(m["type"] == "hello" for m in seen))
        width = await desk_watches()
        assert (await asyncio.to_thread(http.post, "/say", json={"text": "Hello from the desk"})).json()["ok"]
        await asyncio.to_thread(wait_for, lambda: any(m.get("event") == "say" for m in seen))
        stop.set()
        await phone.close()
        return width

    width = asyncio.run(run())
    assert width > 0                                                  # the desk got live video
    hello = next(m for m in seen if m["type"] == "hello")
    assert hello["status"]["connected"] and any(e["event"] == "started" for e in hello["history"])
    assert any(m.get("event") == "desk_watch" for m in seen)
    assert any(m.get("event") == "say" and m["text"] == "Hello from the desk" for m in seen)
    assert any(m["type"] == "phone" and m["msg"] == {"type": "say", "text": "Hello from the desk"} for m in seen)
    http.post("/hangup")


def test_the_desk_web_app_can_call_from_another_origin(gateway, monkeypatch):
    base, _ = gateway
    monkeypatch.setattr(server, "ACCESS_KEY", "s3cret")
    origin = {"Origin": "https://spark-e257.tail803c7f.ts.net"}
    pre = httpx.options(f"{base}/say", headers={**origin, "Access-Control-Request-Method": "POST",
                                                "Access-Control-Request-Headers": "x-sensei-key,content-type"})
    assert pre.status_code == 200                                     # preflights carry no key
    r = httpx.get(f"{base}/status", headers={**origin, "X-Sensei-Key": "s3cret"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "*"
    assert httpx.get(f"{base}/events", headers=origin).status_code == 401  # still needs the key
