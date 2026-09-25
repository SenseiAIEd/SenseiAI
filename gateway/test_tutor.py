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

    def assess(self, img, instructions, system=""):
        self.instructions.append(instructions)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def wrap_up(self, notes):
        return "Today you fixed a sign mistake. Remember: a minus outside brackets flips every sign inside."


class Harness:
    def __init__(self, brain, minutes=10, tap_only=False):
        self.now = 1000.0
        self.said: list[tuple[str, str]] = []
        self.notes: list[dict] = []
        self.events: list[str] = []

        async def speak(text, why):
            self.said.append((why, text))

        async def notify(state):
            self.notes.append(state)

        self.tutor = Tutor(brain, speak, notify, minutes=minutes, clock=lambda: self.now,
                           log_event=lambda event, **k: self.events.append(event),
                           tap_only=tap_only)

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


def test_a_mistake_seen_once_is_not_spoken_about_until_a_second_look_agrees():
    """One bad read - a shadow, a half-written line - must never become a hint."""
    brain = ScriptedBrain(MISTAKE, MISTAKE)
    h = Harness(brain)
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    assert h.whys() == ["greeting"] and "tutor_unconfirmed" in h.events
    h.settle(page_with("ab"))  # the same mistake again: now it is real
    assert h.whys()[-1] == "hint_1" and h.tutor.hint_level == 1


def test_a_mistake_that_does_not_come_back_is_never_mentioned():
    h = Harness(ScriptedBrain(MISTAKE, ON_TRACK, ON_TRACK))
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    h.settle(page_with("ab"))
    assert h.whys() == ["greeting"] and h.tutor.mistake is None and not h.tutor.mistakes_found


def test_hints_escalate_on_the_same_mistake_but_not_too_fast():
    brain = ScriptedBrain(MISTAKE, MISTAKE, MISTAKE, MISTAKE, MISTAKE)
    h = Harness(brain)
    h.run(h.tutor.start())
    h.settle(page_with("a"))   # confirm first
    h.settle(page_with("ab"))
    assert h.whys()[-1] == "hint_1" and h.tutor.hint_level == 1

    h.settle(page_with("abc"))  # new writing right away, same mistake: too soon, stay quiet
    assert h.whys()[-1] == "hint_1" and h.tutor.hint_level == 1

    h.now += 20
    h.settle(page_with("abcd"))
    assert h.whys()[-1] == "hint_2" and "hint level 2" in brain.instructions[-1]
    h.now += 20
    h.settle(page_with("abcde"))
    assert h.tutor.hint_level == 3


def test_acknowledges_a_fix_then_a_finished_problem_once():
    h = Harness(ScriptedBrain(MISTAKE, MISTAKE, FIXED, DONE, DONE))
    h.run(h.tutor.start())
    for extra in ("a", "ab", "abc", "abcd", "abcde"):
        h.now += 20
        h.settle(page_with(extra))
    assert h.whys() == ["greeting", "hint_1", "fixed", "finished"]
    assert h.tutor.mistakes_fixed and h.tutor.problems_finished == 1


def test_nothing_is_judged_while_the_student_is_still_writing():
    """A hand resting on the page means the line is half written: whatever it reads is a
    fragment. Sensei waits instead of correcting work that doesn't exist yet."""
    writing = Assessment(page="work", problem="x + x", steps=["x + x = 7"], first_error=1,
                         error_kind="copying", hand_over_page=True, say="What did you write there?")
    h = Harness(ScriptedBrain(writing, writing))
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    assert h.whys() == ["greeting"] and h.tutor.mistake is None
    h.run(h.tutor.request("hint", PAGE))  # asked directly: say something, but not a correction
    assert h.said[-1] == ("writing", tutor.STILL_WRITING)


def test_an_upside_down_page_is_turned_round_not_corrected():
    """'x + 5 = 7' seen upside down reads as 'L = 5 + x'. That is a camera problem, not a
    mistake, and telling the student they mis-copied it is nonsense."""
    upside_down = Assessment(page="work", problem="L = 5 + x", steps=["L = 5 + x"], first_error=1,
                             error_kind="copying", rotated=True, say=None)
    h = Harness(ScriptedBrain(upside_down))
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    assert h.said[-1] == ("rotated", tutor.TURN_PAGE) and h.tutor.mistake is None


def test_writing_the_problem_down_is_not_the_students_mistake():
    """The first line is the student copying the question. Only a visible original can show
    they copied it wrongly; without one, a 'copying' error is invented."""
    a = parse_assessment('{"page": "work", "problem": "x + 5 = 7", "given": null, '
                         '"steps": ["x + 5 = 7"], "first_error": 1, "error_kind": "concept", '
                         '"say": "What do you think x should be?"}')
    assert a.first_error is None and a.error_kind is None

    # But a mis-copied problem, with the original in view, is a real thing to catch.
    b = parse_assessment('{"page": "work", "problem": "x + 5 = 7", "given": "x + 5 = 9", '
                         '"steps": ["x + 5 = 7"], "first_error": 1, "error_kind": "copying", '
                         '"say": "Check the number on the right against the sheet."}')
    assert b.first_error == 1 and b.error_kind == "copying"


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
    h = Harness(ScriptedBrain(MISTAKE, MISTAKE, FIXED), minutes=5)
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    h.settle(page_with("ab"))
    h.now += 20
    h.settle(page_with("abc"))
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
        def assess(self, img, instructions, system=""):
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

        def slow(img, instructions, system=""):
            if not h.tutor.pending_question and "said out loud" not in instructions:
                h.tutor.pending_question = ("what colour is my pen?", PAGE)  # as hear() does when busy
            return orig(img, instructions, system)

        brain.assess = slow
        h.tutor.watcher.mark_judged = lambda: None
        await h.tutor._judge(page_with("a"), request=None)

    h.run(busy_then_ask())
    # the background result was dropped in favour of the question, which got answered
    assert "tutor_superseded" in h.events
    assert h.whys()[-1] == "reply" and h.said[-1][1] == "That's a red pen."
    assert h.tutor.pending_question is None


def test_hearing_while_thinking_queues_the_question_and_says_so():
    h = Harness(ScriptedBrain())
    h.run(h.tutor.start())
    h.tutor.thinking = True
    h.run(h.tutor.hear("is my second line right?", PAGE))
    assert h.tutor.pending_question[0] == "is my second line right?"
    assert h.said[-1] == ("busy", tutor.HEARD_WHILE_BUSY)


def test_a_quick_answer_just_comes_without_a_filler_first():
    """A pause of a second or two is how people talk. Only a slow model needs covering."""
    h = Harness(ScriptedBrain(Assessment(page="work", steps=["x = 5"], say="Yes, x equals five checks out.")))
    h.run(h.tutor.start())
    h.run(h.tutor.hear("is x five?", PAGE))
    assert h.whys()[-1:] == ["reply"] and "ack" not in h.whys()


def test_a_slow_answer_is_covered_by_a_word_so_the_student_is_not_left_hanging():
    slow = Assessment(page="work", steps=["x = 5"], say="Yes, x equals five checks out.")
    h = Harness(ScriptedBrain(slow))
    h.tutor.ACK_AFTER_S = 0.0  # pretend the model is slower than a natural pause
    h.run(h.tutor.start())
    h.run(h.tutor.hear("is x five?", PAGE))
    assert h.whys()[-2:] == ["ack", "reply"] and h.said[-2][1] in tutor.HEARD


def test_short_acknowledgements_are_heard_but_not_answered():
    """"Okay." and "Thank you." are not questions; replying to each one buries the student."""
    h = Harness(ScriptedBrain(Assessment(page="work", steps=["x = 2"], say="Because both sides stay equal.")))
    h.run(h.tutor.start())
    for filler in ("Okay.", "Thank you.", "mm", "yeah"):
        h.run(h.tutor.hear(filler, PAGE))
    assert h.whys() == ["greeting"]
    assert [c for c in h.tutor.conversation if c["who"] == "student"] == []
    h.run(h.tutor.hear("why?", PAGE))  # short, but a real question
    assert [c["text"] for c in h.tutor.conversation if c["who"] == "student"] == ["why?"]
    assert h.whys()[-1] == "reply"


SOLVED = ["x + 5 = 7", "x = 7 - 5", "x = 2"]


def test_a_new_problem_written_under_finished_work_is_not_a_mistake():
    """Session 20260924-165953: the student solved x + 5 = 7, wrote e^x = 39 underneath, and
    Sensei called it step 4 and the first error - then asked "what made you switch to that
    equation?" six times while the student said five times that it was a new problem."""
    a = parse_assessment(
        '{"page": "work", "problem": "x + 5 = 7", "steps": ["x + 5 = 7", "x = 7 - 5", "x = 2",'
        ' "e^x = 39"], "other_problems": ["e^x = 39"], "first_error": 4, "error_kind": "concept",'
        ' "finished": true, "say": "What made you switch to that equation?"}')
    assert a.first_error is None and a.other_problems == ["e^x = 39"]


def test_a_second_problem_on_the_page_is_followed_not_corrected():
    """Praise the problem they finished, then move across to the one they have started -
    rather than treating the new one as a wrong fourth step of the old one."""
    done_and_next = Assessment(page="work", problem="x + 5 = 7", steps=SOLVED, finished=True,
                               other_problems=["e^x = 39"],
                               say="Nicely solved. Why does subtracting five from both sides work?")
    h = Harness(ScriptedBrain(done_and_next))
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    assert h.whys()[-1] == "finished" and h.tutor.problems_finished == 1
    assert h.tutor.problem == "e^x = 39"  # focus moved after the praise, not before
    assert "tutor_next_problem" in h.events


def test_the_student_can_move_sensei_to_a_new_problem_by_saying_so():
    """"I'm just giving a new problem" has to actually move the session."""
    moved = Assessment(page="work", about="steer", focus="e^x = 39",
                       say="Okay, let's look at e to the x equals 39. What undoes an exponential?")
    h = Harness(ScriptedBrain(moved))
    h.run(h.tutor.start())
    h.tutor.problem = "x + 5 = 7"
    h.tutor.mistake, h.tutor.hint_level, h.tutor.hints_on_mistake = (4, "e^x=39"), 3, 3

    h.run(h.tutor.hear("I'm just giving a new problem", PAGE))
    assert h.tutor.problem == "e^x = 39"
    assert h.tutor.mistake is None and h.tutor.hint_level == 0  # the old chase is dropped
    assert "tutor_focus" in h.events and "tutor_dropped_mistake" in h.events


def test_after_the_student_redirects_the_page_loop_holds_its_tongue():
    """The old failure was a background look re-raising the abandoned problem seconds later."""
    stale = Assessment(page="work", problem="x + 5 = 7", steps=SOLVED + ["e^x = 39"],
                       first_error=4, error_kind="concept", say="What made you write that?")
    h = Harness(ScriptedBrain(Assessment(page="work", about="steer", focus="e^x = 39",
                                         say="Sure, let's do that one."), stale, stale))
    h.run(h.tutor.start())
    h.tutor.problem = "x + 5 = 7"
    h.run(h.tutor.hear("help me with this new problem instead", PAGE))
    said_after_steer = len(h.said)

    h.settle(page_with("a"))  # a background look lands moments later
    assert len(h.said) == said_after_steer, "spoke over the student right after they redirected"

    h.now += 60  # once the moment has passed, normal watching resumes
    h.settle(page_with("ab"))
    assert h.tutor.problem == "e^x = 39"


def test_sensei_stops_asking_after_three_tries_instead_of_nagging():
    """hint_3 fired at t=193, 227 and 275 in the same session, reworded each time so the
    repeat-suppression missed it. Four times is nagging, not teaching."""
    h = Harness(ScriptedBrain(*[MISTAKE] * 8))
    h.run(h.tutor.start())
    for i in range(6):
        h.now += 20
        h.settle(page_with("a" * (i + 1)))
    whys = [w for w in h.whys() if w.startswith("hint_") or w == "let_it_go"]
    assert whys == ["hint_1", "hint_2", "hint_3", "let_it_go"]
    assert h.said[-1][1] == tutor.MOVE_ON and h.tutor.mistake is None


def test_what_the_student_asked_about_is_routed_and_recorded():
    """A question about the idea is not a request to describe the desk. Eight replies in the
    23 Sep session opened with "I see a white envelope..." because the page always won."""
    reply = Assessment(page="other", about="subject", say="Because both sides have to stay equal.")
    h = Harness(ScriptedBrain(reply))
    h.run(h.tutor.start())
    h.run(h.tutor.hear("why does subtracting work?", PAGE))
    assert h.said[-1] == ("reply", "Because both sides have to stay equal.")
    assert "tutor_about" in h.events

    # The model is told to route before answering, and the image is there to be ignored.
    instructions = h.tutor._instructions("talk", said="why does subtracting work?")
    assert '"about"' in instructions and "ignore" in instructions


def test_unroutable_json_still_answers():
    """An older or sloppier model that omits "about" must not break the reply."""
    a = parse_assessment('{"page": "work", "say": "Try the left side first."}')
    assert a.about is None and a.say == "Try the left side first."
    assert parse_assessment('{"page": "work", "about": "nonsense", "say": "hi"}').about is None


def test_say_that_again_is_answered_from_memory_not_the_model():
    """No camera, no model call, no chance of getting it wrong - Sensei knows what it said."""
    h = Harness(ScriptedBrain())  # no scripted replies: a model call would raise
    h.run(h.tutor.start())
    h.run(h.tutor.speak("What happens to the minus four?", "hint_1"))
    h.now += 5
    for asked in ("what did you say?", "Can you repeat that", "say it again please"):
        h.run(h.tutor.hear(asked, PAGE))
        assert h.said[-1] == ("repeat", "What happens to the minus four?")


def test_a_bare_no_is_an_answer_when_sensei_has_just_asked_something():
    """Sensei asks "what does x plus x equal?", the student says "No." - ignoring that as a
    filler would leave Sensei silent after its own question."""
    h = Harness(ScriptedBrain(Assessment(page="work", steps=["x + x = 7"], say="Which part feels wrong?")))
    h.run(h.tutor.start())
    h.run(h.tutor.speak("What do you think x plus x equals?", "hint_1"))
    h.run(h.tutor.hear("No.", PAGE))
    assert h.whys()[-1] == "reply"

    # Once the moment has passed, the same word is noise again.
    h.now += 60
    h.run(h.tutor.hear("No.", PAGE))
    assert h.whys()[-1] == "reply" and len(h.whys()) == 3


def test_sensei_does_not_say_the_same_sentence_for_the_same_reason_twice():
    h = Harness(ScriptedBrain())
    h.run(h.tutor.start())
    h.run(h.tutor.speak("Have a look at your second line.", "hint_1"))
    h.now += 5
    h.run(h.tutor.speak("Have a look at your second line.", "hint_1"))
    assert h.whys().count("hint_1") == 1 and "tutor_repeat_suppressed" in h.events
    # An escalating hint has a new reason to speak, so the same words still go out.
    h.run(h.tutor.speak("Have a look at your second line.", "hint_2"))
    assert h.whys()[-1] == "hint_2"
    # And "repeat" is the student asking for those exact words again.
    h.run(h.tutor.request("repeat", None))
    assert h.whys()[-1] == "repeat"


# --- Jev: fast decisions on what the student said (jev.py) ---------------------------------
from jev import utterance_questions


class FakeJev:
    """Answers in the real /v1/systemone shape, so decide_utterance's parsing is exercised too."""

    def __init__(self, respond=0.9, about="subject", needs_page=0.9, new_problem=0.0,
                 which=None, enabled=True, fail=False):
        self.enabled, self.fail, self.calls = enabled, fail, []
        self.a = {"respond": respond, "about": about, "needs_page": needs_page,
                  "new_problem": new_problem, "which": which}

    def ask(self, state, questions):
        self.calls.append((state, questions))
        if self.fail:
            raise TimeoutError("jev timed out")
        a = self.a
        answers = {
            "respond": {"type": "noul", "noul": a["respond"]},
            "about": {"type": "choice", "choice": a["about"], "confidence": 0.9,
                      "probabilities": {a["about"]: 0.9}},
            "followup": {"type": "noul", "noul": 0.1},
            "needs_page": {"type": "noul", "noul": a["needs_page"]},
            "new_problem": {"type": "noul", "noul": a["new_problem"]},
        }
        if "which_problem" in questions:
            answers["which_problem"] = {"type": "choice", "choice": a["which"] or "none_of_these",
                                        "confidence": 0.9, "probabilities": {}}
        return answers


class SeeingBrain(ScriptedBrain):
    """Remembers whether each look was sent the camera image."""

    def __init__(self, *replies):
        super().__init__(*replies)
        self.images = []

    def assess(self, img, instructions, system=""):
        self.images.append(img is not None)
        return super().assess(img, instructions, system)


REPLY = Assessment(page="work", about="subject", say="Both sides stay balanced.")


def with_jev(jev, *replies):
    brain = SeeingBrain(*replies)
    h = Harness(brain)
    h.tutor.decider = jev
    h.run(h.tutor.start())
    return h, brain


def test_jev_off_changes_nothing():
    jev = FakeJev(respond=0.0, enabled=False)
    h, brain = with_jev(jev, REPLY)
    h.run(h.tutor.hear("why does that work?", PAGE))
    assert jev.calls == [] and h.whys()[-1] == "reply"


def test_jev_can_decide_not_to_answer_and_no_model_is_called():
    """"Thank you" while Sensei is busy used to get "Got it, I'll answer that in a moment"."""
    h, brain = with_jev(FakeJev(respond=0.05))
    h.tutor.thinking = True
    h.run(h.tutor.hear("thank you so much", PAGE))
    assert h.whys() == ["greeting"] and brain.instructions == [] and h.tutor.pending_question is None
    assert "jev" in h.events


def test_right_after_a_question_only_a_confident_no_skips_the_answer():
    h, brain = with_jev(FakeJev(respond=0.35), REPLY)
    h.run(h.tutor.speak("What do you think x plus x equals?", "hint_1"))
    h.run(h.tutor.hear("no", PAGE))
    assert h.whys()[-1] == "reply"  # 0.35 would skip normally, not when Sensei is owed an answer


def test_a_question_about_an_idea_is_answered_without_the_image():
    h, brain = with_jev(FakeJev(about="subject", needs_page=0.1), REPLY)
    h.run(h.tutor.hear("what is a coefficient?", PAGE))
    assert brain.images == [False] and "No camera image" in brain.instructions[-1]


def test_a_question_about_the_page_keeps_the_image():
    h, brain = with_jev(FakeJev(about="view", needs_page=0.1), REPLY)  # page-ish route wins
    h.run(h.tutor.hear("is this right?", PAGE))
    assert brain.images == [True]


def test_jev_moves_focus_to_the_problem_the_student_names():
    h, brain = with_jev(FakeJev(about="steer", new_problem=0.9, which="p0"), REPLY)
    h.tutor.problem, h.tutor.other_problems = "x + 5 = 7", ["e^x = 39"]
    h.tutor.mistake, h.tutor.hint_level = (4, "e^x=39"), 3
    h.run(h.tutor.hear("I'm just giving a new problem", PAGE))
    assert h.tutor.problem == "e^x = 39" and h.tutor.mistake is None
    assert "moved on to a new problem: e^x = 39" in brain.instructions[-1]
    state, questions = h.tutor.decider.calls[-1]
    assert state["other_problems_on_page"] == ["e^x = 39"] and "which_problem" in questions


def test_when_jev_fails_sensei_decides_as_before():
    h, brain = with_jev(FakeJev(fail=True), REPLY)
    h.run(h.tutor.hear("Okay.", PAGE))  # old filler rule still applies
    assert h.whys() == ["greeting"] and "jev_error" in h.events
    h.run(h.tutor.hear("why does that work?", PAGE))
    assert h.whys()[-1] == "reply"


def test_which_problem_is_only_asked_when_the_page_shows_another_problem():
    assert "which_problem" not in utterance_questions([])
    q = utterance_questions(["e^x = 39"])["which_problem"]
    assert q["type"] == "choice" and q["criteria"]["p0"] == "e^x = 39" and "none_of_these" in q["criteria"]


def test_after_jev_fails_it_is_not_retried_for_a_while():
    """Unplugged, every call would wait out the timeout; one failure buys a quiet backoff."""
    from jev import Jev
    j = Jev("http://127.0.0.1:9/v1/systemone", timeout_s=0.5)
    j.backoff_s = 60
    try:
        j.ask({}, {})
    except Exception:
        pass
    assert j.down_until > 0
    import time as _t
    t0 = _t.monotonic()
    try:
        j.ask({}, {})
    except ConnectionError as e:
        assert "failed recently" in str(e)
    assert _t.monotonic() - t0 < 0.05  # no network attempt


def test_being_redirected_is_answered_even_when_jev_doubts_a_reply_is_needed():
    """End-to-end, 24 Sep: "I'm just giving a new problem" scored respond=0.32, new_problem=0.71,
    and Sensei said nothing. Being steered is never ignorable."""
    h, brain = with_jev(FakeJev(respond=0.32, about="steer", new_problem=0.71, which="p0"), REPLY)
    h.tutor.problem, h.tutor.other_problems = "x + 5 = 7", ["e^x = 39"]
    h.run(h.tutor.hear("I'm just giving a new problem.", PAGE))
    assert h.whys()[-1] == "reply" and h.tutor.problem == "e^x = 39"


def test_a_question_about_an_idea_does_not_move_the_focus_when_jev_says_so():
    """The writing model once set focus to "what is a coefficient?"; Jev (new_problem=0.15) knew better."""
    reply = Assessment(page="work", about="subject", focus="what is a coefficient?",
                       say="A coefficient is the number in front of a variable.")
    h, brain = with_jev(FakeJev(about="subject", new_problem=0.15), reply)
    h.tutor.problem = "x + 5 = 7"
    h.run(h.tutor.hear("What is a coefficient?", PAGE))
    assert h.tutor.problem == "x + 5 = 7" and h.whys()[-1] == "reply"


def test_thanks_after_a_question_is_still_not_an_answer():
    h, brain = with_jev(FakeJev(respond=0.23, about="social"))
    h.run(h.tutor.speak("Can you explain why subtracting five works?", "finished"))
    h.run(h.tutor.hear("Thank you.", PAGE))
    assert h.whys()[-1] == "finished" and brain.instructions == []


def test_each_backend_brings_its_own_floors():
    """Local models spread probabilities differently: 0.30 on JevK5 is a real question."""
    from jev import BACKENDS, Jev
    j = Jev(BACKENDS["jevk5"]["url"], backend="jevk5")
    assert (j.skip_below, j.no_page_below) == (0.25, 0.15) and j.headers == {}
    j.key = "k"
    j.use("hosted")
    assert j.skip_below == 0.40 and j.headers["Authorization"] == "Bearer k" and j.where == "hosted"
    j.use("semif")
    assert j.skip_below == 0.15 and j.url.endswith(":8096/v1/systemone") and j.headers == {}
    with pytest.raises(ValueError):
        j.use("nonsense")


def test_the_tutor_uses_the_backends_floor():
    jev = FakeJev(respond=0.30, about="subject")
    jev.skip_below, jev.skip_if_answer_below, jev.no_page_below = 0.25, 0.15, 0.15   # JevK5's
    h, brain = with_jev(jev, REPLY)
    h.run(h.tutor.hear("why does subtracting work?", PAGE))
    assert h.whys()[-1] == "reply"   # 0.30 is under hosted's 0.40, but over JevK5's 0.25


def test_hosted_cannot_be_chosen_without_a_key():
    from jev import Jev
    j = Jev("http://127.0.0.1:8095/v1/systemone", backend="jevk5")
    with pytest.raises(ValueError):
        j.use("hosted")


# --- tap-only / quiet-until-Hint (SENSEI_TAP_ONLY) ---------------------------------------

def test_tap_only_detects_but_does_not_auto_speak_after_confirm():
    """Background looks still confirm a mistake, but stay quiet until Hint/Check."""
    brain = ScriptedBrain(MISTAKE, MISTAKE, MISTAKE)
    h = Harness(brain, tap_only=True)
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    assert h.whys() == ["greeting"] and "tutor_unconfirmed" in h.events
    h.settle(page_with("ab"))  # second agreeing look: detect, do not speak
    assert h.whys() == ["greeting"]
    assert h.tutor.mistake == MISTAKE.mistake and h.tutor.hint_level == 0
    assert h.tutor.hints_given == 0 and h.tutor.mistakes_found
    assert "tutor_detected" in h.events
    h.now += 20
    h.settle(page_with("abc"))  # further looks: still quiet, no escalate
    assert h.whys() == ["greeting"] and h.tutor.hint_level == 0


def test_tap_only_hint_and_check_still_speak():
    """Explicit Hint / Check taps still speak under tap-only; first Hint is hint_1."""
    brain = ScriptedBrain(MISTAKE, MISTAKE, MISTAKE, FIXED)
    h = Harness(brain, tap_only=True)
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    h.settle(page_with("ab"))  # detected, quiet
    assert h.whys() == ["greeting"] and h.tutor.mistake is not None

    h.run(h.tutor.request("hint", PAGE))
    assert "hint_1" in h.whys() and h.tutor.hint_level == 1 and h.tutor.hints_given == 1
    assert any(w == "ack" for w in h.whys())  # looking ack still fires

    h.now += 20
    h.run(h.tutor.request("check", page_with("fixed")))
    assert h.whys()[-1] == "fixed" and h.tutor.mistake is None


def test_tap_only_off_keeps_auto_hint_after_confirm():
    """Default (flag off): second agreeing look still auto-speaks hint_1."""
    brain = ScriptedBrain(MISTAKE, MISTAKE)
    h = Harness(brain, tap_only=False)
    h.run(h.tutor.start())
    h.settle(page_with("a"))
    h.settle(page_with("ab"))
    assert h.whys()[-1] == "hint_1" and h.tutor.hint_level == 1


def test_tap_only_suppresses_idle_nudge():
    h = Harness(ScriptedBrain(), tap_only=True)
    h.run(h.tutor.start())
    h.now += Tutor.IDLE_S + 1
    h.run(h.tutor.tick())
    assert "idle" not in h.whys()


def test_tap_only_background_looks_do_not_let_it_go():
    """Without Hint taps, tap-only never escalates to let_it_go from background looks."""
    h = Harness(ScriptedBrain(*[MISTAKE] * 8), tap_only=True)
    h.run(h.tutor.start())
    for i in range(6):
        h.now += 20
        h.settle(page_with("a" * (i + 1)))
    assert "let_it_go" not in h.whys()
    assert not any(w.startswith("hint_") for w in h.whys())
    assert h.tutor.mistake is not None and h.tutor.hint_level == 0
