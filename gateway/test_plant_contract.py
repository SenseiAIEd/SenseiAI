"""Harness-only contract tests for the locked Fri plant: physics/basic/medium.

These encode the README first-error contract (bad_1 line1 `v_y = 20`, good_1 clear)
through Assessment / Tutor — no live LLM, Spark, or Jev.

Skip boundary: image→VL transcription of the PNGs into steps[] is not exercised here.
ScriptedBrain supplies the README narrative as Assessment.steps; catching the plant
from pixels alone still requires a live Brain (eval_brain.py), which is out of scope.
"""
import re
from pathlib import Path

import cv2
import pytest

from tutor import Assessment

from test_tutor import Harness, ScriptedBrain

REPO = Path(__file__).resolve().parent.parent
PLANT_DIR = REPO / "datasets" / "samples" / "physics" / "basic" / "medium"

# Locked by datasets/samples/physics/README.md (medium: vector never resolved).
PROBLEM = (
    "A ball is launched at 20 m/s, 60 degrees above the horizontal. "
    "How long until it returns to launch height? (g = 9.8 m/s^2)"
)
BAD_STEPS = [
    "v_y = 20 m/s",
    "t_up = v_y / g = 20 / 9.8 = 2.04 s",
    "t_total = 2 x 2.04 = 4.08 s",
]
GOOD_STEPS = [
    "v_y = 20 sin(60) = 17.32 m/s",
    "t_up = v_y / g = 17.32 / 9.8 = 1.77 s",
    "t_total = 2 x 1.77 = 3.54 s",
]
# Spoken path must not Photomath-dump these (Fri plant / LOCKED-DECISIONS).
FORBIDDEN_SAY_FRAGMENTS = (
    "17.32",
    "3.54",
    "20 sin",
    "20sin",
    "sin(60)",
    "sin 60",
)

SOCRATIC_HINT = (
    "Your method in lines 2 and 3 is exactly right. Go back to line 1: "
    "the ball is moving at 20 m/s, but is all of that motion upward?"
)


def _load(name: str):
    img = cv2.imread(str(PLANT_DIR / name))
    assert img is not None, f"missing or unreadable plant fixture: {PLANT_DIR / name}"
    return img


def _spoken_texts(h: Harness) -> list[str]:
    return [text for _, text in h.said]


def _assert_no_answer_dump(texts: list[str]):
    blob = " ".join(texts).lower().replace(" ", "")
    for frag in FORBIDDEN_SAY_FRAGMENTS:
        needle = frag.lower().replace(" ", "")
        assert needle not in blob, f"spoken path leaked forbidden fragment {frag!r}: {texts}"


def test_medium_plant_fixtures_present():
    """ques / bad_1 / good_1 PNGs exist and load (paths locked for Fri demo)."""
    for name in ("ques.png", "bad_1.png", "good_1.png"):
        path = PLANT_DIR / name
        assert path.is_file(), f"expected plant fixture at {path}"
        assert cv2.imread(str(path)) is not None


def test_bad_plant_assessment_mistake_identity_is_vy_equals_20():
    """README: first error is line 1 content `v_y = 20 m/s` (normalize spaces away)."""
    a = Assessment(
        page="work",
        problem=PROBLEM,
        steps=BAD_STEPS,
        first_error=1,
        error_kind="concept",
        say=SOCRATIC_HINT,
    )
    assert a.first_error == 1
    assert a.mistake is not None
    step, line = a.mistake
    assert step == 1
    assert "v_y=20" in line
    # Control shape: without first_error, mistake identity is clear.
    clear = Assessment(page="work", problem=PROBLEM, steps=GOOD_STEPS, first_error=None)
    assert clear.mistake is None and clear.first_error is None


def test_check_on_bad_plant_flags_first_error_and_does_not_dump_answer():
    """Check on bad_1: FakeBrain Assessment catches line1; spoken path stays Socratic."""
    plant = Assessment(
        page="work",
        problem=PROBLEM,
        steps=BAD_STEPS,
        first_error=1,
        error_kind="concept",
        say=SOCRATIC_HINT,
    )
    brain = ScriptedBrain(plant)
    h = Harness(brain)
    h.run(h.tutor.start())
    h.run(h.tutor.request("check", _load("bad_1.png")))

    assert h.tutor.mistake is not None
    assert h.tutor.mistake[0] == 1
    assert "v_y=20" in h.tutor.mistake[1]
    assert h.tutor.hint_level == 1
    assert any(w == "hint_1" for w in h.whys())
    _assert_no_answer_dump(_spoken_texts(h))


def test_check_on_good_plant_stays_clear():
    """Control good_1: first_error null — Check must not invent a plant catch."""
    control = Assessment(
        page="work",
        problem=PROBLEM,
        steps=GOOD_STEPS,
        first_error=None,
        finished=True,
        say="Well done. Can you explain why the vertical component uses the sine of the launch angle?",
    )
    brain = ScriptedBrain(control)
    h = Harness(brain)
    h.run(h.tutor.start())
    h.run(h.tutor.request("check", _load("good_1.png")))

    assert h.tutor.mistake is None
    assert h.tutor.hint_level == 0
    assert "hint_1" not in h.whys()
    # Finished acknowledgement is fine; must still not be a first_error catch.
    assert any(w in ("finished", "check", "ack", "greeting") for w in h.whys())


def test_hint_on_bad_plant_spoken_path_rejects_photomath_dump():
    """Hint tap with plant Assessment: harness-spoken text must not dump corrected values."""
    dump = Assessment(
        page="work",
        problem=PROBLEM,
        steps=BAD_STEPS,
        first_error=1,
        error_kind="concept",
        # Deliberately non-compliant say — contract asserts the harness-visible path
        # would fail if a brain dumped like Photomath. ScriptedBrain returns this
        # only so the assertion below documents the forbidden surface.
        say="Set v_y = 20 sin(60) = 17.32 m/s so t_total is 3.54 s.",
    )
    # The product brain is instructed never to do this; we still prove the
    # contract checker flags it when the spoken path carries those numbers.
    with pytest.raises(AssertionError):
        _assert_no_answer_dump([dump.say])

    socratic = Assessment(
        page="work",
        problem=PROBLEM,
        steps=BAD_STEPS,
        first_error=1,
        error_kind="concept",
        say=SOCRATIC_HINT,
    )
    brain = ScriptedBrain(socratic)
    h = Harness(brain)
    h.run(h.tutor.start())
    h.run(h.tutor.request("hint", _load("bad_1.png")))
    assert h.tutor.mistake is not None and "v_y=20" in h.tutor.mistake[1]
    _assert_no_answer_dump(_spoken_texts(h))


def test_readme_contract_constants_match_locked_plant():
    """Keep BAD/GOOD step text aligned with physics README medium section."""
    def norm(s: str) -> str:
        return re.sub(r"\s+", "", s).lower()

    assert Assessment(page="work", steps=BAD_STEPS, first_error=1).mistake[1] == "v_y=20m/s"
    # Control first line is a different identity (includes sin), not the plant.
    assert norm(GOOD_STEPS[0]) != norm(BAD_STEPS[0])
    assert "sin" in norm(GOOD_STEPS[0])
