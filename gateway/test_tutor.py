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
    assert h.whys()[-2:] == ["ack", "hint"] and "tapped Hint" in brain.instructions[-1]
    h.run(h.tutor.request("repeat", None))
    assert h.said[-1] == ("repeat", "What could you do with the brackets first?")


def test_background_looks_stay_quiet_about_non_work_but_requests_get_answers():
    h = Harness(ScriptedBrain(Assessment(page="none"), Assessment(page="unreadable"), Assessment(page="none")))
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    h.settle(page_with("ab"))
    assert h.whys() == ["greeting"]  # nothing to say about an empty or blurry view unprompted
    h.run(h.tutor.request("check", PAGE))
    assert h.said[-1] == ("none", tutor.NO_PAGE)


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
    # template opened <think>; the reasoning itself contains braces
    'Line 1 is {fine}, so {"x": 1} is not it.</think>{"page": "work", "steps": ["a"], "first_error": null, "say": null}',
    # Cosmos-Reason2 style
    '<think>check {each} line</think>\n<answer>{"page": "work", "steps": ["a"], "first_error": null, "say": null}</answer>',
])
def test_parse_assessment_tolerates_model_formatting(reply):
    a = parse_assessment(reply)
    assert a.page == "work" and a.steps == ["a"] and a.first_error is None and a.say is None and a.mistake is None


def test_not_work_uses_the_models_own_words():
    h = Harness(ScriptedBrain(Assessment(page="unreadable", say="This isn't a math problem. Can you show your math work?"),
                              Assessment(page="none")))
    h.run(h.tutor.start())
    h.run(h.tutor.request("check", PAGE))
    assert h.said[-1] == ("unreadable", "This isn't a math problem. Can you show your math work?")
    h.run(h.tutor.request("check", PAGE))
    assert h.said[-1] == ("none", tutor.NO_PAGE)  # no words from the model: fixed fallback


def test_extra_taps_while_thinking_get_an_answer_not_silence():
    h = Harness(ScriptedBrain())
    h.run(h.tutor.start())
    h.tutor.thinking = True  # a look is already in progress
    h.now += 20
    h.run(h.tutor.request("hint", PAGE))
    h.run(h.tutor.request("hint", PAGE))  # right after: not repeated
    assert h.whys()[1:] == ["busy"]


def test_reply_after_the_call_ended_is_dropped():
    class SlowBrain(ScriptedBrain):
        def assess(self, img, instructions):
            h.tutor.stop()  # the call drops while the model is thinking
            return MISTAKE

    h = Harness(SlowBrain())
    h.run(h.tutor.start())
    h.run(h.tutor.request("hint", PAGE))
    assert h.whys() == ["greeting", "ack"] and "tutor_late_reply" in h.events


def test_anything_in_view_is_described_when_asked_not_unprompted():
    seen = Assessment(page="other", steps=["$ uvicorn server:app"],
                      say="I see a computer terminal running a server. What do you think it's doing?")
    h = Harness(ScriptedBrain(seen, seen))
    h.run(h.tutor.start())
    h.now += 20
    h.settle(page_with("a"))
    assert h.whys() == ["greeting"]  # the room isn't narrated unasked
    h.run(h.tutor.request("look", PAGE))
    assert h.said[-1] == ("look", "I see a computer terminal running a server. What do you think it's doing?")
    assert h.tutor.mistake is None and h.tutor.hints_given == 0
    assert "never refuse just because it isn't homework" in tutor.SYSTEM_PROMPT


def test_what_do_you_see_describes_the_view():
    brain = ScriptedBrain(Assessment(page="other", say="I see a laptop screen showing a terminal with Python commands."))
    h = Harness(brain)
    h.run(h.tutor.start())
    h.run(h.tutor.request("look", PAGE))
    assert h.whys()[-2:] == ["ack", "look"] and "what do you see" in brain.instructions[-1]
    assert h.said[-1][1].startswith("I see a laptop screen")


def test_student_speaks_and_sensei_answers_about_what_it_sees():
    brain = ScriptedBrain(Assessment(page="work", steps=STEPS[:1],
                                     say="Almost! Check what happens to the minus four inside the brackets."))
    h = Harness(brain)
    h.run(h.tutor.start())
    h.run(h.tutor.hear("is my first line right?", PAGE))
    assert h.said[-1] == ("reply", "Almost! Check what happens to the minus four inside the brackets.")
    assert '"is my first line right?"' in brain.instructions[-1] and "student_said" in h.events


def test_spoken_follow_ups_see_the_conversation_so_far():
    brain = ScriptedBrain(
        Assessment(page="other", steps=["BOTTLE"], say="That's a blue water bottle."),
        Assessment(page="other", say="You asked me what the object in your hand is."),
    )
    h = Harness(brain)
    h.run(h.tutor.start())
    h.run(h.tutor.hear("what is this in my hand?", PAGE))
    h.now += 5
    h.run(h.tutor.hear("what did I ask you?", PAGE))
    second = brain.instructions[-1]
    # the model gets the earlier question, its own answer and what it saw, but not the new
    # question twice
    assert "Student: what is this in my hand?" in second
    assert "Sensei: That's a blue water bottle." in second
    assert "What you last saw in the camera: something other than homework: BOTTLE" in second
    assert second.count("what did I ask you?") == 1
    assert [c["who"] for c in h.tutor.conversation[-4:]] == ["student", "sensei", "student", "sensei"]


def test_fillers_are_not_remembered_and_memory_is_bounded():
    h = Harness(ScriptedBrain(*[Assessment(page="other", say=f"answer {i}") for i in range(30)]))
    h.run(h.tutor.start())
    for i in range(30):
        h.now += 10
        h.run(h.tutor.hear(f"question {i}", PAGE))
    assert len(h.tutor.conversation) == Tutor.MEMORY_TURNS
    assert h.tutor.conversation[-1]["text"] == "answer 29"
    assert not any(c["text"] in tutor.LOOKING.values() for c in h.tutor.conversation)


def test_pause_stops_looking_listening_and_the_clock():
    brain = ScriptedBrain(Assessment(page="other", say="I see a notebook."))
    h = Harness(brain)
    h.run(h.tutor.start())
    h.now += 60
    h.run(h.tutor.request("pause", None))
    left = h.tutor.remaining_s()
    assert h.tutor.phase == "paused" and h.whys()[-1] == "pause" and left == 540

    h.now += 300  # a long break
    h.settle(page_with("a"))
    h.run(h.tutor.hear("what is this?", PAGE))
    h.run(h.tutor.tick())
    assert brain.instructions == [] and h.tutor.remaining_s() == left  # nothing judged, clock stopped

    h.run(h.tutor.request("resume", None))
    assert h.tutor.phase == "watching" and h.whys()[-1] == "resume"
    assert abs(h.tutor.remaining_s() - left) < 1
    h.run(h.tutor.request("end", None))
    assert h.tutor.phase == "ended"


def test_a_question_asked_while_busy_is_answered_not_dropped():
    brain = ScriptedBrain(Assessment(page="other", say="(background look)"),
                          Assessment(page="other", say="That's a red pen."))

    class Harness2(Harness):
        pass

    h = Harness(brain)
    h.run(h.tutor.start())

    async def busy_then_ask():
        # the student speaks while a background look is still running
        orig = brain.assess

        def slow(img, instructions):
            if not h.tutor.pending_question and "said out loud" not in instructions:
                h.tutor.pending_question = ("what colour is my pen?", PAGE)  # as hear() does when busy
            return orig(img, instructions)

        brain.assess = slow
        h.tutor.watcher.mark_judged = lambda: None
        await h.tutor._judge(page_with("a"), request=None)

    h.run(busy_then_ask())
    # the background result was dropped in favour of the question, which got ack + answer
    assert "tutor_superseded" in h.events
    assert h.whys()[-2:] == ["ack", "reply"] and h.said[-1][1] == "That's a red pen."
    assert h.tutor.pending_question is None


def test_hearing_while_thinking_queues_the_question_and_says_so():
    h = Harness(ScriptedBrain())
    h.run(h.tutor.start())
    h.tutor.thinking = True
    h.run(h.tutor.hear("is my second line right?", PAGE))
    assert h.tutor.pending_question[0] == "is my second line right?"
    assert h.said[-1] == ("busy", tutor.HEARD_WHILE_BUSY)


def test_every_question_is_acknowledged_before_the_answer():
    h = Harness(ScriptedBrain(Assessment(page="work", steps=["x = 5"], say="Yes, x equals five checks out.")))
    h.run(h.tutor.start())
    h.run(h.tutor.hear("is x five?", PAGE))
    assert h.whys()[-2:] == ["ack", "reply"] and h.said[-2][1] in tutor.HEARD
