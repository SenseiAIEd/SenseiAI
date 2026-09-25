"""Where Sensei looks and how it frames a face. Run: pytest gateway/"""
import asyncio
import math

import numpy as np
import pytest

import server
from fake_head import FakeHead
from gaze import (Aim, Attention, Face, FaceFinder, Framer, Moment, field_of_view, parse_expression,
                  turn_towards)
from head import Head, PanTilt
from test_head import bare_session


def connect(port):
    return PanTilt(port, settle_s=0, boot_s=0)


# --- how far to turn ---------------------------------------------------------------------
def test_centred_face_needs_no_turn():
    assert turn_towards(Face(0.5, 0.42, 0.2, 0.2, 0.9), 720, 1280, Aim()) == (0, 0)
    assert turn_towards(Face(0.54, 0.46, 0.2, 0.2, 0.9), 720, 1280, Aim()) == (0, 0)  # inside the deadband


def test_turn_matches_the_camera_geometry():
    hfov, vfov = field_of_view(720, 1280)  # portrait: narrow across, wide down
    assert (hfov, vfov) == (50, 64)
    aim = Aim(gain=1.0, max_step=90)
    dpan, dtilt = turn_towards(Face(1.0, 0.42, 0.1, 0.1, 0.9), 720, 1280, aim)
    assert dpan == round(hfov / 2) and dtilt == 0      # face at the right edge: half the view
    dpan, dtilt = turn_towards(Face(0.5, 0.0, 0.1, 0.1, 0.9), 720, 1280, aim)
    assert dpan == 0 and dtilt < 0                     # face at the top: tilt up (smaller angle)


def test_one_nudge_is_limited():
    assert turn_towards(Face(1.0, 1.0, 0.1, 0.1, 0.9), 720, 1280, Aim()) == (15, 15)
    assert turn_towards(Face(0.0, 0.0, 0.1, 0.1, 0.9), 720, 1280, Aim(pan_sign=-1)) == (15, -15)


# --- the closed loop, against a simulated head and a simulated room ----------------------
class Room:
    """A face at fixed head angles; where it shows in the frame follows from where the head points.
    `pan_dir` = -1 models a pan servo mounted the other way round."""

    def __init__(self, head: Head, face_pan=112, face_tilt=58, pan_dir=1, tilt_dir=1):
        self.head, self.face_pan, self.face_tilt = head, face_pan, face_tilt
        self.pan_dir, self.tilt_dir = pan_dir, tilt_dir
        self.frame = np.zeros((1280, 720, 3), np.uint8)
        self.looks = 0

    def find(self, img):
        self.looks += 1
        hfov, vfov = field_of_view(720, 1280)

        def pos(delta, fov):
            return 0.5 + math.tan(math.radians(delta)) / (2 * math.tan(math.radians(fov / 2)))
        cx = pos(self.pan_dir * (self.face_pan - self.head.pan), hfov)
        cy = pos(self.tilt_dir * (self.face_tilt - self.head.tilt), vfov)
        return Face(cx, cy, 0.15, 0.2, 0.95) if 0 <= cx <= 1 and 0 <= cy <= 1 else None

    async def frame_after(self, t):
        return self.frame


@pytest.fixture
def esp32():
    fake = FakeHead()
    yield fake
    fake.close()


@pytest.mark.parametrize("pan_dir", [1, -1])
def test_framer_centres_the_face_and_learns_directions(esp32, pan_dir):
    async def run():
        head = Head(esp32.port, connect=connect)
        await head.look("student")                       # 90, 70
        room = Room(head, pan_dir=pan_dir)
        framer = Framer(head, room, room.frame_after)
        framer.finder = room
        g = await framer.frame_face()
        assert g.framed, g
        assert abs(g.face.cx - 0.5) <= 0.07 and abs(g.face.cy - 0.42) <= 0.07
        assert framer.aim.pan_sign == pan_dir            # a backwards servo is found and flipped
        assert g.steps <= 5
    asyncio.run(run())


def test_framer_searches_then_gives_up(esp32):
    async def run():
        head = Head(esp32.port, connect=connect)
        await head.look("student")
        room = Room(head, face_pan=10, face_tilt=40)     # nowhere near the student preset
        framer = Framer(head, room, room.frame_after)
        framer.finder = room
        g = await framer.frame_face()
        assert not g.framed and g.note == "no face in view"
    asyncio.run(run())


def test_face_finder_loads_and_ignores_an_empty_desk():
    assert FaceFinder().find(np.full((720, 1280, 3), 180, np.uint8)) is None


def test_expression_parsing():
    assert parse_expression("Confused.") == "confused"
    assert parse_expression("<think>maybe happy</think>frustrated") == "frustrated"
    assert parse_expression("I can't tell") is None


# --- when to look ------------------------------------------------------------------------
def moment(**kw):
    base = dict(looking_at="notebook", seconds_here=30, seconds_since_glance=60,
                sensei_asked_a_question=False, seconds_since_sensei_spoke=30,
                seconds_since_student_spoke=30, student_last_said="", seconds_since_page_activity=2,
                hints_given=0, last_expression="unknown", sensei_thinking=False)
    return Moment(**{**base, **kw})


def test_attention_rules():
    a = Attention()
    assert a.decide(moment()) is None                                            # writing: keep watching
    assert a.decide(moment(sensei_asked_a_question=True, seconds_since_sensei_spoke=2)) == "student"
    assert a.decide(moment(student_last_said="this is so hard", seconds_since_student_spoke=3)) == "student"
    assert a.decide(moment(seconds_since_page_activity=60)) == "student"         # stuck?
    assert a.decide(moment(seconds_since_page_activity=60, seconds_since_glance=5)) is None  # not again yet
    assert a.decide(moment(seconds_here=1, sensei_asked_a_question=True, seconds_since_sensei_spoke=2)) is None
    assert a.decide(moment(looking_at="student", seconds_here=4)) is None
    assert a.decide(moment(looking_at="student", seconds_here=9)) == "notebook"  # glance over
    assert a.decide(moment(looking_at="student", seconds_here=9, seconds_since_student_spoke=1)) is None
    assert a.decide(moment(looking_at="student", seconds_here=4, student_last_said="is this right?",
                           seconds_since_student_spoke=3)) == "notebook"


class FakeJev:
    enabled = True

    def __init__(self, choice, confidence):
        self.answer = {"look": {"choice": choice, "confidence": confidence}}
        self.states = []

    def ask(self, state, questions):
        self.states.append(state)
        return self.answer


def test_jev_overrules_when_confident_but_not_the_guard_rails():
    assert Attention(FakeJev("student", 0.8)).decide(moment()) == "student"
    assert Attention(FakeJev("student", 0.3)).decide(moment()) is None           # not sure: rules stand
    assert Attention(FakeJev("stay", 0.9)).decide(
        moment(sensei_asked_a_question=True, seconds_since_sensei_spoke=2)) is None
    assert Attention(FakeJev("student", 0.9)).decide(moment(seconds_since_glance=5)) is None
    assert Attention(FakeJev("stay", 0.9)).decide(moment(looking_at="student", seconds_here=20)) == "notebook"

    class Down(FakeJev):
        def ask(self, state, questions):
            raise ConnectionError("down")
    assert Attention(Down("x", 1)).decide(moment(seconds_since_page_activity=60)) == "student"


# --- in a session ------------------------------------------------------------------------
def test_session_glances_frames_the_face_and_keeps_the_page(esp32, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "HEAD", Head(esp32.port, connect=connect))
    monkeypatch.setattr(server, "CHAT_BRAIN", None)
    monkeypatch.setattr(server, "BRAIN", None)

    async def run():
        s = bare_session(tmp_path)
        page = np.full((1280, 720, 3), 200, np.uint8)
        s.latest_frame, s.page_frame = page, page
        room = Room(server.HEAD)
        s.framer = Framer(server.HEAD, room, room.frame_after)
        await s.point("student", "test")
        assert s.looking_at == "student" and s.glance.framed
        assert s.page() is page                          # questions still get the page, not the face
        assert s.face_jpeg
        await s.point("notebook", "test")
        assert s.looking_at == "notebook" and server.HEAD.preset == "notebook"
    asyncio.run(run())
