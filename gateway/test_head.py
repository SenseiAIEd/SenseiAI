"""The pan-tilt head, against a simulated ESP32 on a pseudo-terminal. Run: pytest gateway/"""
import asyncio
import json

import httpx
import pytest

import server
from fake_head import FakeHead
from head import Head, PanTilt, gaze_command
from test_gateway import gateway  # noqa: F401  (fixture)


def connect(port):
    return PanTilt(port, settle_s=0, boot_s=0)


@pytest.fixture
def esp32():
    fake = FakeHead()
    yield fake
    fake.close()


def test_client_speaks_the_firmware_protocol(esp32):
    head = connect(esp32.port)
    assert head.position() == (90, 90)
    assert head.preset("notebook") == (90, 135)
    assert head.move(200, 0) == (170, 40)  # the firmware clamps to its safe limits
    with pytest.raises(ValueError):
        head.preset("ceiling")
    head.close()


def test_head_reconnects_and_never_raises(esp32):
    async def run():
        head = Head(esp32.port, connect=connect)
        assert await head.look("student")
        assert head.state()["preset"] == "student" and (head.pan, head.tilt) == (90, 70)
        assert await head.nudge(-5, 10) and (head.pan, head.tilt) == (85, 80)
        assert not await head.look("ceiling")          # bad preset: reported, link kept
        assert head.dev is not None and "unknown preset" in head.error
        head.dev.ser.close()                             # cable pulled
        assert not await head.look("home")
        assert head.dev is None
        assert await head.look("home") and head.error is None  # plugged back in
        head.close()

        missing = Head("/dev/no-such-port", connect=connect)
        assert not await missing.look("notebook") and missing.error
    asyncio.run(run())


@pytest.mark.parametrize("said, preset", [
    ("Sensei, look at my notebook", "notebook"),
    ("can you look down at the page please", "notebook"),
    ("look at me", "student"),
    ("Look up!", "student"),
    ("look straight", "home"),
    ("what does this look like to you?", None),
    ("I don't know where to look", None),
    ("does my answer look right", None),
])
def test_spoken_gaze_commands(said, preset):
    assert gaze_command(said) == preset


def test_console_and_voice_move_the_head(gateway, esp32, monkeypatch, tmp_path):  # noqa: F811
    base, _ = gateway
    monkeypatch.setattr(server, "HEAD", Head(esp32.port, connect=connect))
    http = httpx.Client(base_url=base, timeout=10)

    r = http.post("/head", json={"preset": "notebook"})
    assert r.status_code == 200 and r.json()["head"]["tilt"] == 135
    assert http.post("/head", json={"nudge_pan": -10}).json()["head"]["pan"] == 80
    assert http.post("/head", json={"pan": 100, "tilt": 100}).json()["head"]["preset"] is None
    assert http.post("/head", json={"preset": "ceiling"}).status_code == 400
    assert http.get("/status").json()["head"]["connected"]

    sent = asyncio.run(hear_in_session(tmp_path, "Sensei, look at me"))
    assert server.HEAD.preset == "student"
    assert {"type": "say", "text": "Okay, looking at you.", "why": "gaze"} in sent


class Channel:
    readyState = "open"

    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(json.loads(msg))


def bare_session(tmp_path, monkeypatch=None):
    """A Session without a WebRTC call: enough to hear speech and move the head."""
    server.RECORD_DIR = tmp_path
    s = server.Session(pc=None)
    s.channel = Channel()
    return s


async def hear_in_session(tmp_path, text):
    s = bare_session(tmp_path)
    await s.heard(text, {})
    return s.channel.sent


def test_no_head_configured(gateway, monkeypatch):  # noqa: F811
    base, _ = gateway
    monkeypatch.setattr(server, "HEAD", None)
    http = httpx.Client(base_url=base, timeout=10)
    assert http.post("/head", json={"preset": "home"}).status_code == 409
    assert gaze_command("look at me") == "student"  # parsed, but the server only acts with a head


def test_presets_and_limits_are_set_from_the_gateway(esp32):
    async def run():
        head = Head(esp32.port, connect=connect)
        assert await head.connect()
        assert head.state()["limits"] == {"pan": (10, 170), "tilt": (40, 150)}
        await head.move(100, 120)
        assert await head.save("notebook")
        assert head.state()["presets"]["notebook"] == (100, 120)
        await head.look("home")
        assert await head.look("notebook") and (head.pan, head.tilt) == (100, 120)
        assert await head.set_limit("tilt", 60, 200)                  # 200 is past the hard limit
        assert head.state()["limits"]["tilt"] == (60, 160)
        assert await head.move(90, 20) and head.tilt == 60            # can't over-rotate
        assert not await head.set_limit("tilt", 90, 80)               # nonsense is refused
        head.close()
    asyncio.run(run())


def test_old_firmware_without_settings_still_works(esp32):
    real = esp32._handle

    def v1(line):  # the build guide's original sketch: no PRESETS?/LIMITS?
        if line.split()[0].upper() in ("PRESETS?", "LIMITS?", "SAVE", "LIMIT", "NUDGE", "STOP", "RELAX"):
            return esp32._reply("ERR unknown command")
        real(line)
    esp32._handle = v1

    async def run():
        head = Head(esp32.port, connect=connect)
        assert await head.connect() and head.error is None and "presets" not in head.state()
        assert await head.look("student") and await head.nudge(5, 0)
        head.close()
    asyncio.run(run())
