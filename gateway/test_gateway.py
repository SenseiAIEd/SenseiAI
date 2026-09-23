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
    assert httpx.get(f"{base}/config").json() == {"iceServers": [], "tutor": {"brain": None}}  # no relay, no model

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


def test_student_starts_a_session_and_gets_a_hint(gateway, monkeypatch):
    """Phone taps Start over the data channel; the tutor greets, looks at the streamed page
    and speaks its hint through the same channel."""
    from tutor import Assessment
    base, _ = gateway

    class Brain:
        model = "scripted"
        calls = 0

        def assess(self, img, instructions):
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
    assert Brain.calls == 1  # the same still page is judged once
