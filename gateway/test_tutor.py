"""Tutor decisions with a scripted brain and a fake clock. Run: pytest gateway/"""
import asyncio
from pathlib import Path

import cv2
import pytest

import tutor
from tutor import Assessment, Tutor, parse_assessment

PAGE = cv2.imread(str(Path(__file__).resolve().parent.parent / "datasets/samples/math/basic/easy/bad_1.png"))


def page_with(extra: str):
    """The sample page plus some new writing (so the page watcher sees a change)."""
    img = PAGE.copy()
    cv2.putText(img, extra, (700, 300), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (40, 40, 90), 3)
    return img


STEPS = ["5 - 2x - 4 = 11", "1 - 2x = 11", "-2x = 10", "x = -5"]
ON_TRACK = Assessment(page="work", problem="5 - (2x - 4) = 11", steps=STEPS[:1], say=None)
MISTAKE = Assessment(page="work", problem="5 - (2x - 4) = 11", steps=STEPS[:2], first_error=1,
                     error_kind="sign", say="Look at your first line: what happens to minus four inside the brackets?")
FIXED = Assessment(page="work", problem="5 - (2x - 4) = 11", steps=["5 - 2x + 4 = 11"], say="Nice fix! Keep going.")
DONE = Assessment(page="work", problem="5 - (2x - 4) = 11", steps=["5 - 2x + 4 = 11", "x = -1"], finished=True,
                  say="Well done! Can you explain why the sign changed?")


class ScriptedBrain:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.instructions: list[str] = []

    def assess(self, img, instructions):
        self.instructions.append(instructions)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def wrap_up(self, notes):
        return "Today you fixed a sign mistake. Remember: a minus outside brackets flips every sign inside."


class Harness:
    def __init__(self, brain, minutes=10):
        self.now = 1000.0
        self.said: list[tuple[str, str]] = []
        self.notes: list[dict] = []
        self.events: list[str] = []

        async def speak(text, why):
            self.said.append((why, text))

        async def notify(state):
            self.notes.append(state)

        self.tutor = Tutor(brain, speak, notify, minutes=minutes, clock=lambda: self.now,
                           log_event=lambda event, **k: self.events.append(event))

    def run(self, coro):
        return asyncio.run(coro)

    def settle(self, img):
        """Show the same frame until the watcher judges it (hand lifted, page still)."""
        for _ in range(tutor.PageWatcher().still_samples + 1):
            self.run(self.tutor.on_frame(img))
            self.now += 0.5

    def whys(self):
        return [w for w, _ in self.said]


def test_greets_on_start():
    h = Harness(ScriptedBrain())
    h.run(h.tutor.start())
    assert h.whys() == ["greeting"] and h.tutor.phase == "watching"
    assert h.notes[-1]["remaining_s"] == 600


def test_quiet_while_on_track_and_judges_only_new_writing():
    brain = ScriptedBrain(ON_TRACK)
    h = Harness(brain)
    h.run(h.tutor.start())
    h.settle(PAGE)
    h.settle(PAGE)  # same page again: not judged a second time
    assert h.whys() == ["greeting"] and len(brain.instructions) == 1


def test_hints_escalate_on_the_same_mistake_but_not_too_fast():
    brain = ScriptedBrain(MISTAKE, MISTAKE, MISTAKE, MISTAKE)
    h = Harness(brain)
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    assert h.whys()[-1] == "hint_1" and h.tutor.hint_level == 1

    h.settle(page_with("ab"))  # new writing right away, same mistake: too soon, stay quiet
    assert h.whys()[-1] == "hint_1" and h.tutor.hint_level == 1

    h.now += 20
    h.settle(page_with("abc"))
    assert h.whys()[-1] == "hint_2" and "hint level 2" in brain.instructions[-1]
    h.now += 20
    h.settle(page_with("abcd"))
    h.now += 20
    assert h.tutor.hint_level == 3


def test_acknowledges_a_fix_then_a_finished_problem_once():
    h = Harness(ScriptedBrain(MISTAKE, FIXED, DONE, DONE))
    h.run(h.tutor.start())
    for extra in ("a", "ab", "abc", "abcd"):
        h.now += 20
        h.settle(page_with(extra))
    assert h.whys() == ["greeting", "hint_1", "fixed", "finished"]
    assert h.tutor.mistakes_fixed and h.tutor.problems_finished == 1


def test_hint_button_forces_a_look_and_answers():
    brain = ScriptedBrain(Assessment(page="work", steps=STEPS[:1], say="What could you do with the brackets first?"))
    h = Harness(brain)
    h.run(h.tutor.start())
    h.run(h.tutor.request("hint", PAGE))
    assert h.whys()[-1] == "hint" and "tapped Hint" in brain.instructions[-1]
    h.run(h.tutor.request("repeat", None))
    assert h.said[-1] == ("repeat", "What could you do with the brackets first?")


def test_no_page_is_mentioned_but_not_every_frame():
    h = Harness(ScriptedBrain(Assessment(page="none"), Assessment(page="none")))
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    h.settle(page_with("ab"))  # a few seconds later: don't repeat
    assert h.whys() == ["greeting", "none"]


def test_model_failure_is_quiet_unless_the_student_asked():
    h = Harness(ScriptedBrain(RuntimeError("model down"), RuntimeError("model down")))
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    assert h.whys() == ["greeting"] and "tutor_error" in h.events
    h.run(h.tutor.request("check", PAGE))
    assert h.whys()[-1] == "error" and not h.tutor.thinking


def test_one_minute_warning_then_wrap_up():
    h = Harness(ScriptedBrain(MISTAKE, FIXED), minutes=5)
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    h.now += 20
    h.settle(page_with("ab"))
    h.now = h.tutor.ends_at - 50
    h.run(h.tutor.tick())
    assert h.whys()[-1] == "time"
    h.now = h.tutor.ends_at + 1
    h.run(h.tutor.tick())
    assert h.tutor.phase == "ended" and h.said[-1][0] == "wrap_up"
    assert "sign mistake" in h.said[-1][1]
    ended = h.notes[-1]
    assert ended["type"] == "session_ended" and ended["mistakes_fixed"] == 1 and ended["hints_given"] == 1


def test_end_button_and_no_brain():
    h = Harness(None)
    h.run(h.tutor.start())
    assert "thinking part isn't connected" in h.said[0][1]
    h.run(h.tutor.request("hint", PAGE))
    assert h.whys()[-1] == "no_brain"
    h.run(h.tutor.request("end", None))
    assert h.tutor.phase == "ended" and h.said[-1] == ("wrap_up", tutor.GOODBYE)


def test_idle_check_in():
    h = Harness(ScriptedBrain())
    h.run(h.tutor.start())
    h.now += Tutor.IDLE_S + 1
    h.run(h.tutor.tick())
    assert h.whys()[-1] == "idle"


@pytest.mark.parametrize("reply", [
    '{"page": "work", "steps": ["a"], "first_error": null, "say": null}',
    '```json\n{"page": "work", "steps": ["a"], "first_error": null, "say": null}\n```',
    '<think>let me look</think>Here you go: {"page": "work", "steps": ["a"], "first_error": "null", "say": ""}',
])
def test_parse_assessment_tolerates_model_formatting(reply):
    a = parse_assessment(reply)
    assert a.page == "work" and a.steps == ["a"] and a.first_error is None and a.say is None and a.mistake is None
