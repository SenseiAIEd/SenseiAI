"""Where Sensei looks, and how far to turn to see the student's face.

Two different questions, answered by two different tools:

  WHEN to look up   Attention: a few rules about the moment (Sensei just asked something, the page
                    has been still for a long time, the student sounds stuck), optionally refined by
                    one Jev call. Jev only reads text, so it judges the situation, not the picture.
  HOW FAR to turn   Framer: a face detector (YuNet, runs on the Spark's CPU in a few ms) finds the
                    face in the frame; the angle to it follows from the camera's field of view; the
                    head nudges by that angle and looks again until the face sits where we want it.

Then read_expression asks the vision model for one word about the face (engaged, confused, ...),
which the tutor gets as context.

Domain:
  Face        a detected face in normalised frame coordinates (0..1)
  Moment      what Attention decides from: where the head points, how long, what just happened
  Glance      one look at the student: framed?, expression, how many nudges it took
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

import cv2
import numpy as np

log = logging.getLogger("sensei.gaze")

MODEL_PATH = Path(__file__).with_name("models") / "face_detection_yunet_2023mar.onnx"


# --- seeing a face --------------------------------------------------------------------------
@dataclass
class Face:
    cx: float      # centre, 0..1 across the frame
    cy: float      # centre, 0..1 down the frame
    w: float       # size, as a fraction of the frame
    h: float
    score: float


class FaceFinder:
    """The largest face in a frame (YuNet). Frames are shrunk first: faces at a desk are big."""

    def __init__(self, model: Path = MODEL_PATH, min_score: float = 0.7, work_side: int = 480):
        self.model, self.min_score, self.work_side = str(model), min_score, work_side
        self._det = None
        self._size = None

    def find(self, img: np.ndarray) -> Optional[Face]:
        h, w = img.shape[:2]
        scale = min(1.0, self.work_side / max(h, w))
        small = cv2.resize(img, (round(w * scale), round(h * scale))) if scale < 1 else img
        sh, sw = small.shape[:2]
        if self._det is None:
            self._det = cv2.FaceDetectorYN.create(self.model, "", (sw, sh), self.min_score)
        if self._size != (sw, sh):
            self._det.setInputSize((sw, sh))
            self._size = (sw, sh)
        _, faces = self._det.detect(small)
        if faces is None or len(faces) == 0:
            return None
        x, y, fw, fh, *_, score = max(faces, key=lambda f: f[2] * f[3])
        return Face(cx=(x + fw / 2) / sw, cy=(y + fh / 2) / sh, w=fw / sw, h=fh / sh, score=float(score))


# --- turning towards it ---------------------------------------------------------------------
def field_of_view(frame_w: int, frame_h: int) -> tuple[float, float]:
    """(horizontal, vertical) degrees. Pixel 2 XL main camera: about 64 x 50 in landscape; the
    phone may stream portrait, so the longer side gets the wider angle. Override with
    SENSEI_CAM_FOV="64,50" (long side, short side)."""
    long_deg, short_deg = (float(v) for v in os.environ.get("SENSEI_CAM_FOV", "64,50").split(","))
    return (long_deg, short_deg) if frame_w >= frame_h else (short_deg, long_deg)


@dataclass
class Aim:
    x: float = 0.5          # where the face should sit: centred across ...
    y: float = 0.42         # ... and a little above the middle, so the shoulders show too
    deadband: float = 0.07  # close enough: within 7% of the frame
    gain: float = 0.8       # turn a little less than the full angle: no overshoot
    max_step: int = 15      # degrees per nudge, so one bad detection can't swing the phone
    pan_sign: int = 1       # +1: a bigger pan angle turns the view to the right (flipped if not)
    tilt_sign: int = 1      # +1: a bigger tilt angle turns the view down (the guide's presets)


def turn_towards(face: Face, frame_w: int, frame_h: int, aim: Aim) -> tuple[int, int]:
    """Degrees to nudge (pan, tilt) to move the face to the aim point; (0, 0) when it's there."""
    hfov, vfov = field_of_view(frame_w, frame_h)

    def angle(off: float, fov: float) -> float:  # pinhole camera: offset in frame -> degrees
        return math.degrees(math.atan(2 * off * math.tan(math.radians(fov / 2))))

    dx, dy = face.cx - aim.x, face.cy - aim.y
    dpan = 0 if abs(dx) <= aim.deadband else aim.pan_sign * angle(dx, hfov) * aim.gain
    dtilt = 0 if abs(dy) <= aim.deadband else aim.tilt_sign * angle(dy, vfov) * aim.gain
    clip = lambda v: int(round(max(-aim.max_step, min(aim.max_step, v))))
    dpan, dtilt = clip(dpan), clip(dtilt)
    # A needed turn that rounds to zero still has to move at least a degree.
    if dpan == 0 and abs(dx) > aim.deadband:
        dpan = aim.pan_sign * (1 if dx > 0 else -1)
    if dtilt == 0 and abs(dy) > aim.deadband:
        dtilt = aim.tilt_sign * (1 if dy > 0 else -1)
    return dpan, dtilt


@dataclass
class Glance:
    """One look at the student."""
    framed: bool = False
    steps: int = 0
    face: Optional[Face] = None
    expression: Optional[str] = None
    note: str = ""
    at: float = field(default_factory=time.time)
    image: Optional[np.ndarray] = field(default=None, repr=False)

    def summary(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k != "image"}
        if self.face:
            d["face"] = {k: round(v, 3) for k, v in asdict(self.face).items()}
        return d


# Searching when no face is in view: small looks around the "student" preset.
SEARCH = [(0, -8), (-12, 0), (12, 0), (0, 8)]


class Framer:
    """Closed loop: find the face, nudge towards it, look again. Learns the servo directions: if a
    nudge moves the face the wrong way, that axis is flipped for good."""

    def __init__(self, head, finder: FaceFinder, frame_after: Callable[[float], Awaitable[Optional[np.ndarray]]],
                 aim: Optional[Aim] = None, max_steps: int = 6):
        self.head, self.finder, self.frame_after = head, finder, frame_after
        self.aim = aim or Aim(pan_sign=int(os.environ.get("SENSEI_HEAD_PAN_SIGN", 1)),
                              tilt_sign=int(os.environ.get("SENSEI_HEAD_TILT_SIGN", 1)))
        self.max_steps = max_steps
        self.confirmed = {"pan": False, "tilt": False}  # direction seen to work at least once

    CAUTIOUS_STEP = 8  # degrees, until an axis's direction is confirmed

    async def _look(self) -> tuple[Optional[np.ndarray], Optional[Face]]:
        img = await self.frame_after(time.monotonic())
        if img is None:
            return None, None
        return img, await asyncio.to_thread(self.finder.find, img)

    async def frame_face(self) -> Glance:
        g = Glance()
        img, face = await self._look()
        searched = 0
        while g.steps < self.max_steps:
            if img is None:
                g.note = "no camera frame"
                return g
            if face is None:
                if searched >= len(SEARCH):
                    g.note = "no face in view"
                    return g
                dpan, dtilt = SEARCH[searched]
                searched += 1
                g.steps += 1
                if not await self.head.nudge(dpan, dtilt):
                    g.note = "head did not move"
                    return g
                img, face = await self._look()
                continue
            h, w = img.shape[:2]
            dpan, dtilt = turn_towards(face, w, h, self.aim)
            c = self.CAUTIOUS_STEP
            if not self.confirmed["pan"]:
                dpan = max(-c, min(c, dpan))
            if not self.confirmed["tilt"]:
                dtilt = max(-c, min(c, dtilt))
            if (dpan, dtilt) == (0, 0):
                g.framed, g.face, g.image = True, face, img
                return g
            g.steps += 1
            before = face
            if not await self.head.nudge(dpan, dtilt):
                g.note = "head did not move"  # e.g. at its limit
                g.face, g.image = face, img
                return g
            img, face = await self._look()
            if face is not None:
                self._learn_direction(before, face, dpan, dtilt)
            else:
                # Lost it right after turning towards it: that turn went the wrong way.
                # Flip the unconfirmed axes that moved, and turn back.
                for axis, d in (("pan", dpan), ("tilt", dtilt)):
                    if d and not self.confirmed[axis]:
                        setattr(self.aim, f"{axis}_sign", -getattr(self.aim, f"{axis}_sign"))
                        log.info("%s direction was backwards; flipped", axis)
                if not await self.head.nudge(-dpan, -dtilt):
                    g.note = "head did not move"
                    return g
                img, face = await self._look()
        g.note = "not centred after max steps"
        g.face, g.image = face, img
        return g

    def _learn_direction(self, before: Face, after: Face, dpan: int, dtilt: int):
        a = self.aim
        for axis, d, was, now, target in (("pan", dpan, before.cx, after.cx, a.x),
                                          ("tilt", dtilt, before.cy, after.cy, a.y)):
            if not d:
                continue
            if abs(now - target) > abs(was - target) + 0.03:
                setattr(a, f"{axis}_sign", -getattr(a, f"{axis}_sign"))
                log.info("%s direction was backwards; flipped (set SENSEI_HEAD_%s_SIGN=%d)",
                         axis, axis.upper(), getattr(a, f"{axis}_sign"))
            elif abs(now - target) < abs(was - target):
                self.confirmed[axis] = True


# --- reading the face -----------------------------------------------------------------------
EXPRESSIONS = ("engaged", "confused", "frustrated", "bored", "happy", "tired", "away")
EXPRESSION_PROMPT = (
    "This is a student at their desk, seen by their tutor's camera. How do they look right now? "
    f"Answer with exactly one word from: {', '.join(EXPRESSIONS)}. "
    "'away' means no student is visible or they are turned away.")


def face_crop(img: np.ndarray, face: Optional[Face], margin: float = 0.9) -> np.ndarray:
    """The face with some room around it (expressions include the brow and mouth)."""
    if face is None:
        return img
    h, w = img.shape[:2]
    half_w, half_h = face.w * w * (0.5 + margin), face.h * h * (0.5 + margin)
    x0, x1 = int(max(0, face.cx * w - half_w)), int(min(w, face.cx * w + half_w))
    y0, y1 = int(max(0, face.cy * h - half_h)), int(min(h, face.cy * h + half_h))
    return img[y0:y1, x0:x1] if x1 > x0 and y1 > y0 else img


def parse_expression(text: str) -> Optional[str]:
    text = re.sub(r"(?s)<think>.*?</think>", "", text or "").lower()
    found = [(text.find(e), e) for e in EXPRESSIONS if e in text]
    return min(found)[1] if found else None


def read_expression(brain, img: np.ndarray, face: Optional[Face]) -> Optional[str]:
    """One short vision-model call. Blocking: run it in a thread."""
    from tutor import to_jpeg_b64
    content = [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{to_jpeg_b64(face_crop(img, face), 640)}"}},
               {"type": "text", "text": EXPRESSION_PROMPT}]
    return parse_expression(brain._chat([{"role": "user", "content": content}], max_tokens=512))


# --- deciding when to look up ---------------------------------------------------------------
@dataclass
class Moment:
    """What Attention decides from. Built fresh each second from the tutor and the head."""
    looking_at: str                  # notebook | student | elsewhere
    seconds_here: float
    seconds_since_glance: float      # since Sensei last looked at the student
    sensei_asked_a_question: bool    # and is waiting for the answer
    seconds_since_sensei_spoke: float
    seconds_since_student_spoke: float
    student_last_said: str
    seconds_since_page_activity: float  # hand moving / writing on the page
    hints_given: int
    last_expression: str             # from the last glance, or "unknown"
    sensei_thinking: bool


FEELINGS = re.compile(r"\b(hard|difficult|confus\w*|stuck|lost|tired|bored|don'?t (get|understand|know)|"
                      r"give up|frustrat\w*|annoy\w*|hate this|help)\b", re.I)
WANTS_PAGE = re.compile(r"\b(check|look at|is this right|this (one|line|step)|my (work|answer|page))\b", re.I)


class Attention:
    """Where the head should point next: "notebook", "student" or None (stay).

    Rules give a sensible default on their own; Jev (optional) can overrule them when it is
    confident. Guard rails always win: never flick back and forth, keep glances short, don't
    stare at the student more than every GAP_S."""

    MIN_DWELL_S = 3.0      # stay at least this long wherever the head points
    GLANCE_MAX_S = 8.0     # a look at the student ends after this (unless they are talking)
    GLANCE_GAP_S = 20.0    # at most one unprompted glance this often
    STILL_PAGE_S = 40.0    # this long with no writing and no talk: are they stuck?
    ASKED_WINDOW = (1.0, 6.0)  # after Sensei asks a question, look up for the answer in this window
    JEV_MIN = 0.55         # Jev's choice must be at least this confident to overrule the rules
    # With Jev on, anything short of a confident, allowed "student" means the notebook. Jev may
    # only take the head off the page once the page has gone quiet (or the rules see a reason). In the 25 Sep demo it chose "student" at ~0.8 every
    # three seconds from the first second on, while the student was writing: a tutor that
    # looks at your face while you work can't see the work it is meant to be checking.
    JEV_GLANCE_QUIET_S = 12.0

    def __init__(self, jev=None):
        self.jev = jev
        self.last_reason = ""
        self.last_jev: Optional[dict] = None

    def rules(self, m: Moment) -> tuple[Optional[str], str]:
        if m.looking_at == "student":
            if m.seconds_since_student_spoke < 2.0:
                return None, "student is talking"
            if WANTS_PAGE.search(m.student_last_said) and m.seconds_since_student_spoke < 10:
                return "notebook", "student asked about their work"
            if m.seconds_here >= self.GLANCE_MAX_S:
                return "notebook", "glance over"
            return None, "still looking at the student"
        # at the notebook (or elsewhere)
        if m.seconds_since_glance < self.GLANCE_GAP_S:
            return None, "looked recently"
        lo, hi = self.ASKED_WINDOW
        if m.sensei_asked_a_question and lo <= m.seconds_since_sensei_spoke <= hi:
            return "student", "waiting for their answer to Sensei's question"
        if FEELINGS.search(m.student_last_said) and m.seconds_since_student_spoke < 8:
            return "student", "they sound stuck or tired"
        if (m.seconds_since_page_activity >= self.STILL_PAGE_S and m.seconds_since_student_spoke >= 20
                and not m.sensei_thinking):
            return "student", "page still for a long time"
        return None, "working on the page"

    def decide(self, m: Moment) -> Optional[str]:
        if m.seconds_here < self.MIN_DWELL_S:
            return None
        target, reason = self.rules(m)
        if self.jev is not None and getattr(self.jev, "enabled", False):
            target, reason = self._jev_decides(m, target)
        if target == "student" and m.looking_at != "student" and m.seconds_since_glance < self.GLANCE_GAP_S:
            target, reason = None, "looked recently"
        if m.looking_at == "student" and m.seconds_here >= self.GLANCE_MAX_S * 2:
            target, reason = "notebook", "looked long enough"  # even Jev can't keep it there
        self.last_reason = reason
        return target if target != m.looking_at else None

    def _jev_decides(self, m: Moment, rules_target: Optional[str]) -> tuple[Optional[str], str]:
        """With Jev on, Jev decides; whenever it can't (unsure, down, or asking for something
        the guard rails won't allow) the answer is the notebook. The page is the default: a
        tutor that isn't sure where to look should be watching the work.

        Taking the head off the page needs a reason: the page has gone quiet, or the rules see
        one (waiting for an answer to Sensei's question, the student sounding stuck). And a
        glance lasts only as long as the rules keep it; "stay" can't stretch it."""
        j = self._ask_jev(m)
        if j is None:
            return "notebook", "jev unavailable: notebook"
        choice, conf = j
        if conf < self.JEV_MIN:
            return "notebook", f"jev unsure ({choice} {conf:.2f}): notebook"
        wants_student = choice == "student" or (choice == "stay" and m.looking_at == "student")
        if not wants_student:
            return "notebook", f"jev {choice} ({conf:.2f})"
        if m.looking_at == "student":
            allowed = rules_target is None
        else:
            allowed = not m.sensei_thinking and (rules_target == "student"
                                                 or m.seconds_since_page_activity >= self.JEV_GLANCE_QUIET_S)
        if not allowed:
            return "notebook", f"jev {choice} ({conf:.2f}) overruled: notebook"
        return "student", f"jev {choice} ({conf:.2f})"

    def _ask_jev(self, m: Moment) -> Optional[tuple[str, float]]:
        state = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in asdict(m).items()}
        questions = {"look": {
            "type": "choice",
            "instructions": "Sensei is a tutor whose camera can point at the student's notebook or at "
                            "the student's face, not both. Where should it point now?",
            "criteria": {
                "notebook": "The student is working, writing, or asked about their work: Sensei needs "
                            "to see the page.",
                "student": "Seeing their face helps now: they are answering Sensei, sound stuck, "
                           "frustrated or tired, or have been still for a long time.",
                "stay": "Keep looking where Sensei is looking now.",
            }}}
        try:
            a = self.jev.ask(state, questions)["look"]
            self.last_jev = {"choice": a["choice"], "confidence": round(float(a.get("confidence", 0)), 2)}
            return a["choice"], float(a.get("confidence", 0.0))
        except Exception as e:
            log.debug("jev look decision unavailable: %s", e)
            self.last_jev = None
            return None
