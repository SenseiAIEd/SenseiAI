"""
Sensei's tutoring loop: watches the camera frames, asks the vision model about the page
when something new is written, and decides what (if anything) to say.

Domain:
  PageWatcher   when to look: the page is still (hand lifted) and has new writing
  Brain         reads a page image and answers as JSON (OpenAI-compatible vision model)
  Assessment    what the brain saw: problem, steps, first wrong step, what Sensei could say
  Tutor         one 5-15 minute session: greeting, watching, hints that escalate on the same
                mistake, acknowledging fixes, time warnings, and a spoken wrap-up

The model reads and words things; this code decides *whether* to speak, so Sensei stays
quiet while the student is on track and never repeats itself too fast.
"""
import asyncio
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

import cv2
import httpx
import numpy as np

from jev import decide_utterance

log = logging.getLogger("sensei.tutor")

# --- When to look -------------------------------------------------------------------------
# Frames are compared as the fraction of pixels that changed by more than PIXEL_DIFF, on a
# blurred 320x240 gray copy. One new handwritten line changes ~0.2 % of pixels; a hand ~15 %.
PIXEL_DIFF = 25
MOTION_FRAC = float(os.environ.get("SENSEI_MOTION_FRAC", 0.02))    # vs previous sample: hand moving
CHANGE_FRAC = float(os.environ.get("SENSEI_CHANGE_FRAC", 0.0008))  # vs last judged page: new writing


def small_gray(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(cv2.resize(img, (320, 240)), cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(g, (5, 5), 0)


def changed_frac(a: np.ndarray, b: np.ndarray) -> float:
    return np.count_nonzero(cv2.absdiff(a, b) > PIXEL_DIFF) / a.size


class PageWatcher:
    """Says when a page is worth judging: still for a while, and changed since last judged."""

    # A student pausing mid-stroke holds the pen still for a second or two, so a short window
    # catches half-written lines. Six samples (~3 s) is long enough that they have moved on.
    def __init__(self, still_samples: int = 6):
        self.still_samples = still_samples  # consecutive still samples (~0.5 s apart) needed
        self.prev: Optional[np.ndarray] = None
        self.judged: Optional[np.ndarray] = None
        self.still = 0

    def update(self, img: np.ndarray) -> bool:
        g = small_gray(img)
        moving = self.prev is None or changed_frac(g, self.prev) > MOTION_FRAC
        self.still = 0 if moving else self.still + 1
        self.prev = g
        if self.still < self.still_samples:
            return False
        return self.judged is None or changed_frac(g, self.judged) > CHANGE_FRAC

    def mark_judged(self):
        self.judged = self.prev


# --- Subjects --------------------------------------------------------------------------------
# Knowing the subject changes how Sensei teaches, not just what it says. In algebra the mistake
# is a line; in physics it is usually before any algebra (units, components, which force); in
# chemistry it is often what a formula means. Each subject has a fixed list of mistake kinds,
# from the ranked lists in datasets/samples/<subject>/README.md, so that counting a student's
# recurring mistakes means something (docs/tutor-plan.md §3). The model picks from the list.
SUBJECTS = ("math", "physics", "chemistry", "other")

MISTAKE_KINDS = {
    "math": ("sign", "distribution", "false_linearity", "chain_rule", "one_term_only",
             "order_of_operations", "lost_parentheses", "dropped_root", "arithmetic", "copying", "concept"),
    "physics": ("units", "components", "normal_force", "sign_convention", "quantity_confusion",
                "extra_force", "wrong_formula", "arithmetic", "copying", "concept"),
    "chemistry": ("subscript_changed", "limiting_reagent", "kelvin", "grams_as_moles", "diatomic",
                  "early_rounding", "wrong_R", "arithmetic", "copying", "concept"),
    "other": ("sign", "arithmetic", "rule", "concept", "copying"),
}

# How a good teacher of each subject finds the mistake, hints at it, and gets the student to
# check their own answer. Only the detected subject's playbook goes to the model: a thinking
# model reasons over every line it is given, so the other two would only slow it down.
PLAYBOOKS = {
    "math": (
        "MATH. Check each line against the one above it; the usual culprits are a minus sign not "
        "carried through brackets, (a + b) squared treated as a squared plus b squared, the chain rule "
        "missed, or only one term divided. Hint level 2 names the rule as a question (\"what happens to "
        "each term inside the brackets when you subtract them?\"). Hint level 3 gives a tiny parallel "
        "example that isolates the same rule, with small numbers (\"what is 5 minus (2 minus 4)?\"), "
        "never their own problem's next line. Their own check: put the answer back into the original "
        "equation."),
    "physics": (
        "PHYSICS. Mistakes usually happen before any algebra: units not converted, a vector used whole "
        "instead of in components, the normal force on a slope taken as mg, a sign convention flipped, "
        "mass confused with weight. Check the units on every line. Hint level 2 asks about the physics, "
        "not the arithmetic (\"which direction does this force act in?\", \"what unit is this speed "
        "in?\"). Hint level 3 is a simpler situation that shows the same idea (\"on flat ground, what "
        "would the normal force be?\"). Their own check: do the units come out right, and is the size "
        "and direction of the answer sensible?"),
    "chemistry": (
        "CHEMISTRY. Common mistakes: balancing by changing a subscript (which makes a different "
        "substance) instead of a coefficient, a limiting reagent picked without dividing by the "
        "coefficients, Celsius used in a gas law, grams used in a mole ratio, O instead of O2. Hint "
        "level 2 asks what the formula or number means (\"is H2O2 still water?\", \"which temperature "
        "scale does the gas law need?\"). Hint level 3 is a simpler parallel case. Their own check: "
        "count every atom and the charge on both sides, and check the units."),
}

# The subject's own way for the student to check a finished answer: the habit worth teaching.
SELF_CHECK = {
    "math": "put your answer back into the original equation",
    "physics": "check the units, and whether the size and direction make sense",
    "chemistry": "count every atom and the charge on both sides",
}

SUBJECT_WORDS = {
    "math": re.compile(r"\b(math|maths|algebra|calculus|geometry|trig\w*|equation|derivative|integral)\b", re.I),
    "physics": re.compile(r"\bphysics\b", re.I),
    "chemistry": re.compile(r"\b(chemistry|chem)\b", re.I),
}


def named_subject(text: str) -> Optional[str]:
    """The subject a student names out loud ("help me with my physics homework"), if exactly one."""
    hits = [s for s, rx in SUBJECT_WORDS.items() if rx.search(text or "")]
    return hits[0] if len(hits) == 1 else None


# --- Reading the page ----------------------------------------------------------------------
@dataclass
class Assessment:
    page: str = "none"                  # "none" | "unreadable" | "work" | "other" (anything else it can see)
    problem: Optional[str] = None       # the problem being solved, as written
    given: Optional[str] = None         # the problem as set (printed, or copied by the student)
    steps: list[str] = field(default_factory=list)
    other_problems: list[str] = field(default_factory=list)  # further problems on the same page
    focus: Optional[str] = None         # the problem the student just asked to work on
    first_error: Optional[int] = None   # 1-based index into steps, or None
    error_kind: Optional[str] = None    # e.g. "sign", "arithmetic", "rule"
    finished: bool = False              # reached a final answer
    hand_over_page: bool = False        # still writing: the reading is half a line, don't act on it
    rotated: bool = False               # the page is upside down or sideways in view
    about: Optional[str] = None         # what the student's words were about (see ABOUT)
    subject: Optional[str] = None       # one of SUBJECTS, when the view shows study work
    topic: Optional[str] = None         # e.g. "linear equations", "balancing equations"
    say: Optional[str] = None           # what Sensei could say now

    @property
    def mistake(self) -> Optional[tuple[int, str]]:
        """Identity of the first mistake: its position and (normalized) line."""
        if self.first_error is None or not (1 <= self.first_error <= len(self.steps)):
            return None
        return self.first_error, re.sub(r"\s+", "", self.steps[self.first_error - 1]).lower()


SYSTEM_PROMPT = """You are Sensei, a warm, patient, curious Socratic tutor for school students
(math, physics, chemistry and beyond). You see what the student shows you through a camera:
usually their written work, but it may be a printed page, a whiteboard, a screen, a book or
an object. Always try to understand what is in view; never refuse just because it isn't homework.

Look carefully and reply with ONE JSON object and nothing else:
{
  "page": "work" | "other" | "unreadable" | "none",
     work: a problem, exercise or someone's working (any subject, handwritten or printed)
  "subject": "math" | "physics" | "chemistry" | "other" | null,
  "topic": "a few words, e.g. linear equations, unit conversion, balancing equations; or null",
     other: anything else you can make out (a screen of code or text, a diagram, a book, an object, a scene)
     unreadable: something is there but too blurry, dark or covered to read
     none: nothing meaningful in view
  "problem": "the problem being solved, or null",
  "given": "the problem as it was set, if you can see where it came from (a printed sheet, a
     textbook, a screen); null if all you can see is what the student wrote",
  "steps": ["the lines of working FOR THAT ONE PROBLEM, in order, as written"],
  "other_problems": ["any OTHER problem written on the page that is not the one in focus"],
  "first_error": <1-based index of the FIRST incorrect step, or null if all correct so far>,
  "error_kind": "one word or snake_case name for the kind of mistake (the user message may give the
     list for this subject), or null",
  "finished": <true if the student reached a final answer>,
  "hand_over_page": <true if a hand, pen or anything else covers part of the writing, or a line
     looks half-written: you are seeing the work mid-stroke>,
  "rotated": <true if the page is upside down or sideways from where you are looking>,
  "say": "what you say to the student now (spoken aloud), or null"
}

Before you judge anything, check these two:
- hand_over_page: a student writing has their hand on the page. What you can read then is a
  fragment, not their work. Set it true and set first_error to null; you will see the page
  again when they lift their hand.
- rotated: if the letters and digits look wrong-way-up, the page is turned, not wrong. A "7"
  upside down looks like an "L"; a "5" looks like an "S". Set rotated true, set first_error
  to null, and in "say" ask them to turn the page to face you.

A page usually holds more than one problem:
- The user message names the problem in focus. Judge THAT problem only.
- A student who has finished one problem writes the next one underneath. A line that starts a
  different problem is NOT the next step of this one, and is NEVER first_error. It goes in
  "other_problems". Writing "e^x = 39" under a finished "x + 5 = 7" is a new question, not a
  mistake - treat it as the student moving on.
- If a line does not follow from the one above it, ask yourself whether it is a wrong step or
  the start of something new, and prefer "something new" when the problem above is finished.

The problem itself is not a mistake:
- The first line is usually the student copying the problem down. Writing the problem is not
  an error. Only report "copying" when you can actually SEE the original in "given" and the
  student's line differs from it. If "given" is null you cannot know they mis-copied, so don't.
- Judge only what the student DERIVED: the lines after the problem.

For "other": in "say", tell the student in one short sentence what you see, then ask one
curious, open question about it that gets them thinking. Leave first_error null.

Rules for "say":
- NEVER give the answer, the corrected line, or the next line. Ask; don't tell.
- One short spoken sentence or question, at most two. Simple words. No LaTeX, no symbols
  that are awkward to read aloud: say "x squared", "minus", "equals".
- If there is a mistake, ask ONE guiding question about that step at the requested hint level:
    level 1: point at the line and ask what they did there.
    level 2: name the idea or rule to check, as a question.
    level 3: a very specific question that nearly reveals it, still without the answer.
- Follow the extra instructions in the user message about what happened before.
- A correct step is never a mistake. Finding an error in correct work is worse than
  missing one: if unsure, treat the step as correct.
"""


# Talking doesn't need the marking rulebook. A thinking model reasons over every line of its
# system prompt, so handing it the full one to answer "what do you see?" cost 24-46 s against
# 9-11 s for a short one. Same model, same call - just nothing irrelevant to think about.
# What the student's words were about. "view" rather than "page", because the assessment also
# has a "page" field and a model handed both will happily answer {"page": "page"}.
ABOUT = ("view", "subject", "sensei", "social", "steer", "unclear")

CHAT_SYSTEM_PROMPT = """You are Sensei, a warm, curious tutor talking with a school student.
You can see what their camera is pointed at, and you have just heard them say something.

Reply with ONE JSON object and nothing else:
{
  "page": "work" | "other" | "unreadable" | "none",
  "about": "view" | "subject" | "sensei" | "social" | "steer" | "unclear",
  "focus": "the problem they want to work on now, written out, or null",
  "say": "what you say to the student now, spoken aloud"
}

FIRST decide "about": what were their words about?
  view     what is in front of the camera - "is my second line right?", "check this",
           "what do you see?", "does this look correct?"
  subject  the maths or the idea, not the picture - "why does subtracting work?",
           "what is a coefficient?", "what is x when e to the x is 39?"
  sensei   you, or the conversation - "what did you say?", "say that again", "stop",
           "can you hear me?"
  social   small talk, or how they feel - "I'm tired", "this is hard", "thanks"
  steer    they are TELLING you what to do, not asking - "I'm giving you a new problem",
           "help me with this one instead", "forget that", "let's move on", "solve this now"
  unclear  you could not make out what they meant, it sounds misheard, or it could mean
           several different things

"focus": set it whenever the student points you at a problem to work on - an equation or an
exercise - by naming it out loud, or by saying the one in front of you is the one they want help
with now. This is how they change the subject, and they are allowed to. A question about an idea
("what is a coefficient?", "why does that work?") is NOT a new problem: leave focus null.

THEN answer, and let "about" decide what you talk about:
- view: answer about what you can see. This is the ONLY case where you describe the view.
- subject: answer the question itself, from the conversation and the problem in focus. Do NOT
  mention or describe what is in the camera. They asked about an idea, not a photo.
- sensei: answer from the conversation so far, briefly.
- social: reply warmly in a few words, then steer back to their work. Don't describe the view.
- steer: DO WHAT THEY ASKED. Do not argue, do not describe the page, and never tell them to go
  back to a problem they have just moved on from. If they hand you a new problem, take it and
  ask your first guiding question about THAT problem.
- unclear: do NOT guess and do NOT repeat their words back at them. Ask one short, friendly
  question to find out what they meant.

The student decides what you work on. You decide how to help them with it.

"say" is ONE short sentence, two at most, about 25 words. Simple words, nothing awkward to read
aloud: say "x squared", "minus", "equals". Never give away an answer they are working towards.
"""


def strip_reasoning(text: str) -> str:
    """Drop a thinking model's reasoning and keep its answer. Handles <think>...</think>,
    a lone </think> (when the chat template opened the block), and <answer>...</answer>."""
    if "</think>" in text:
        text = text.split("</think>")[-1]
    m = re.search(r"<answer>(.*?)(</answer>|$)", text, flags=re.S)
    return (m.group(1) if m else text).strip()


def _same_line(a: Optional[str], b: Optional[str]) -> bool:
    """Two written lines that say the same thing, ignoring spacing and case."""
    if not a or not b:
        return False
    norm = lambda s: re.sub(r"\s+", "", s).lower()
    return norm(a) == norm(b)


def parse_assessment(text: str) -> Assessment:
    """Pull the JSON object out of a model reply (tolerates code fences and reasoning)."""
    text = strip_reasoning(text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"no JSON object in reply: {text[:200]!r}")
    data = json.loads(text[start:end + 1])
    steps = [str(s) for s in (data.get("steps") or []) if str(s).strip()]
    first_error = data.get("first_error")
    try:
        first_error = int(first_error) if first_error is not None else None
    except (TypeError, ValueError):
        first_error = None
    say = data.get("say")
    given = data.get("given") or None
    problem = data.get("problem") or None
    hand_over_page = bool(data.get("hand_over_page"))
    rotated = bool(data.get("rotated"))
    about = str(data.get("about") or "").strip().lower() or None
    if about not in ABOUT:
        about = None
    subject = str(data.get("subject") or "").strip().lower() or None
    if subject in ("maths", "mathematics"):
        subject = "math"
    if subject is not None and subject not in SUBJECTS:
        subject = "other"
    error_kind = str(data.get("error_kind") or "").strip().lower().replace(" ", "_") or None
    others = [str(s) for s in (data.get("other_problems") or []) if str(s).strip()]
    # A step that belongs to a different problem is not this problem's mistake, whatever the
    # model says: a new question written under finished work is the student moving on.
    if first_error and 1 <= first_error <= len(steps):
        if any(_same_line(steps[first_error - 1], other) for other in others):
            first_error = None
    # Safety nets, because a model that agrees with a rule in the prompt still breaks it:
    # a half-written or turned page cannot support a verdict about the maths.
    if hand_over_page or rotated:
        first_error = None
    # Nor can writing the problem down be the student's mistake (it was ours to begin with).
    if first_error == 1 and steps and _same_line(steps[0], given or problem):
        first_error = None
    return Assessment(
        page=str(data.get("page") or "work"),
        problem=problem,
        given=given,
        steps=steps,
        other_problems=others,
        focus=(str(data.get("focus")).strip() if data.get("focus") else None),
        first_error=first_error,
        error_kind=error_kind if first_error else None,
        finished=bool(data.get("finished")),
        hand_over_page=hand_over_page,
        rotated=rotated,
        about=about,
        subject=subject,
        topic=(str(data.get("topic")).strip() or None) if data.get("topic") else None,
        say=str(say).strip() if say and str(say).strip() else None,
    )


def to_jpeg_b64(img: np.ndarray, max_side: int = 1600) -> str:
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(jpg.tobytes()).decode()


class Brain:
    """A vision model behind an OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout_s: float = 120,
                 max_tokens: int = 4096):
        self.base_url = base_url.rstrip("/")
        self.url = self.base_url + "/chat/completions"
        self.model = model  # swappable at runtime: a vLLM router loads it on the next request
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens  # thinking models reason for hundreds of tokens before answering

    @classmethod
    def from_env(cls) -> Optional["Brain"]:
        url = os.environ.get("SENSEI_LLM_URL")
        if not url:
            return None
        return cls(url, os.environ.get("SENSEI_LLM_MODEL", ""), os.environ.get("SENSEI_LLM_KEY", ""),
                   timeout_s=float(os.environ.get("SENSEI_LLM_TIMEOUT", 120)),
                   max_tokens=int(os.environ.get("SENSEI_LLM_MAX_TOKENS", 4096)))

    @classmethod
    def chat_from_env(cls) -> Optional["Brain"]:
        """The fast model that carries the conversation, if one is configured. It answers in
        seconds, so the student isn't left in silence while the careful model reads their maths.

        IMPORTANT: point SENSEI_CHAT_URL at a *different* server from SENSEI_LLM_URL. A router
        that serves one model at a time reloads on every switch - measured at 161 s for a 30B
        thinking model - so sharing one endpoint between the two brains is far worse than
        using one brain for everything. Without SENSEI_CHAT_MODEL, the judging brain does both.
        """
        model = os.environ.get("SENSEI_CHAT_MODEL", "")
        url = os.environ.get("SENSEI_CHAT_URL") or os.environ.get("SENSEI_LLM_URL")
        if not model or not url:
            return None
        key = os.environ.get("SENSEI_CHAT_KEY", os.environ.get("SENSEI_LLM_KEY", ""))
        return cls(url, model, key, timeout_s=float(os.environ.get("SENSEI_CHAT_TIMEOUT", 60)),
                   max_tokens=int(os.environ.get("SENSEI_CHAT_MAX_TOKENS", 1024)))

    def _chat(self, messages: list, max_tokens: int) -> str:
        res = httpx.post(self.url, headers=self.headers, timeout=self.timeout_s, json={
            "model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2})
        res.raise_for_status()
        # With a reasoning parser (e.g. vLLM --reasoning-parser qwen3) the thinking arrives in
        # "reasoning_content" and "content" is just the answer; without one, both are in content.
        return res.json()["choices"][0]["message"].get("content") or ""

    def assess(self, img: Optional[np.ndarray], instructions: str, system: str = "") -> Assessment:
        content = [{"type": "text", "text": instructions}]
        if img is not None:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{to_jpeg_b64(img)}"}})
        reply = self._chat([
            {"role": "system", "content": system or SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ], max_tokens=self.max_tokens)
        return parse_assessment(reply)

    def list_models(self) -> list[str]:
        """What the endpoint can serve, so a model can be picked without a restart."""
        res = httpx.get(self.base_url + "/models", headers=self.headers, timeout=15)
        res.raise_for_status()
        return sorted(str(m.get("id")) for m in res.json().get("data", []) if m.get("id"))

    def ping(self) -> str:
        """A question small enough to be free, asked only to make the model resident."""
        return self._chat([{"role": "user", "content": "Reply with one word: ready"}], max_tokens=16)

    def wrap_up(self, notes: str) -> str:
        reply = self._chat([
            {"role": "system", "content": "You are Sensei, a warm Socratic tutor. Speak simply; this is read aloud."},
            {"role": "user", "content": "The tutoring session is over. In two or three short spoken sentences, "
                                        "tell the student what they worked on, what they fixed, and one thing to "
                                        f"remember next time. Session notes:\n{notes}"},
        ], max_tokens=self.max_tokens)
        return strip_reasoning(reply)


# --- The session ---------------------------------------------------------------------------
# Thirteen seconds of preamble before the student can start is not how a tutor says hello.
GREETING = "Hi, I'm Sensei. Put your page under the camera and start solving. I'm watching."
NO_PAGE = "I can't see your page yet. Please put your notebook under the camera."
UNREADABLE = "I can't read that clearly. Could you move your hand, or write a little larger?"
STILL_WRITING = "Take your time, I'll look when you've finished that line."
MOVE_ON = "Let's leave that one for now. What would you like to look at?"
TURN_PAGE = "Could you turn the page around to face me? I'm reading it upside down."
IDLE_NUDGE = "How is it going? If you're stuck, tap Hint, or tell me which step you're on."
ONE_MINUTE = "About one minute left. Let's finish the step you're on."
SAY_AGAIN = "Sorry, I didn't catch that. Could you say it again?"
NO_BRAIN = "My thinking part isn't connected right now, so I can only watch. Please tell your teacher."
BRAIN_ERROR = "Sorry, I lost my train of thought for a moment. Keep going, and I'll look again."
PAUSED = "Okay, I'll wait. Tap Resume when you're ready."
RESUMED = "Welcome back. Let's keep going."
GOODBYE = "That's our time. Nice work today. See you next time!"
LOOKING = {"hint": "Let me look at your work.", "check": "Okay, let me check your work.", "look": "Let me look."}
STILL_LOOKING = "Still looking at your work, one moment."
HEARD = ["Okay, let me think.", "Good question. One moment.", "Let me see.", "Got it. Give me a second."]
HEARD_WHILE_BUSY = "Got it. I'll answer that in a moment."
# Correct work used to get total silence until the end: a student can't tell a tutor that is
# happy with them from one that has stopped watching. One short line now and then says "I see it,
# it's right" without breaking their flow; see Tutor.PROGRESS_*.
PROGRESS = ["That's right so far. Keep going.", "Good, those steps check out.", "You're on track. Nice and careful."]

# Short acknowledgements that carry no question. Whisper also emits these for coughs, breaths
# and the tail of Sensei's own voice, so they are the bulk of what a quiet room "says".
FILLERS = {"okay", "ok", "yeah", "yes", "no", "mm", "mhm", "hmm", "uh", "um", "ah", "oh", "right",
           "sure", "thank", "thanks", "you", "bye", "hi", "hello", "yep", "nope", "good",
           "great", "nice", "cool", "alright", "so", "and", "but", "well"}


# "Say that again" needs no model and no camera: Sensei already knows what it said. Answering
# these from memory is both instant and impossible to get wrong.
SAY_AGAIN_RE = re.compile(
    r"\b(say (that|it) again|repeat (that|it|please)|can you repeat|what did you say|"
    r"i didn'?t (hear|catch) (that|you))\b", re.I)


def wants_repeat(text: str) -> bool:
    return bool(SAY_AGAIN_RE.search(text or ""))


def is_filler(text: str) -> bool:
    """True for a word or two that isn't addressed to anyone: nothing to answer."""
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not words or len(words) > 2:
        return False
    if "?" in (text or ""):
        return False  # "why?" is a real question, however short
    return all(w in FILLERS for w in words)


Speak = Callable[[str, str], Awaitable[None]]    # (text, why) -> spoken on the phone
Notify = Callable[[dict], Awaitable[None]]        # state update for the phone


class Tutor:
    """One interactive session. Feed it frames (`on_frame`) and button presses
    (`request`); it speaks through `speak` and reports its state through `notify`."""

    MIN_GAP_S = 8.0          # between unprompted remarks
    HINT_WAIT_S = 20.0       # after a hint, give them this long to find it before asking again
    MEMORY_TURNS = 20        # conversation turns kept for context
    IDLE_S = 90.0            # no new writing for this long -> a gentle check-in
    NO_PAGE_REPEAT_S = 30.0  # how often to repeat "I can't see your page"
    ACK_AFTER_S = 1.8        # a natural pause before "let me think": under this, just answer
    REPEAT_GAP_S = 60.0      # don't say the same sentence again within this
    MAX_RECHECKS = 3         # second looks at one page before giving up on pinning a mistake down
    ANSWER_WINDOW_S = 30.0   # after Sensei's question, even "no" is an answer worth taking
    MAX_HINTS_PER_MISTAKE = 3  # ask three times, then let it go: a fourth is nagging
    STEER_GRACE_S = 20.0     # after the student redirects us, background looks hold their tongue
    PROGRESS_STEPS = 2       # new correct lines since Sensei last said "that's right" ...
    PROGRESS_GAP_S = 45.0    # ... and this long since Sensei said anything: one word of encouragement
    # Jev thresholds (see docs/jev-in-sensei.md). The reply floors and no_page_below are really
    # per backend (jev.BACKENDS) and are read from the decider; these are the fallbacks.
    # 0.40 from evals/utterances.jsonl (24 Sep, 47 cases): what Jev wrongly let through scored
    # 0.32-0.42, and the lowest real greeting 0.48. Small set - rerun jev_eval.py as it grows.
    JEV_SKIP_BELOW = 0.40             # P(should reply) under this: stay quiet, no model call
    JEV_SKIP_IF_ANSWER_BELOW = 0.30   # ... a little lower right after Sensei asked something (Jev
                                      # already sees that; 0.10 let "thank you" through at 0.23)
    JEV_NO_PAGE_BELOW = 0.30          # P(needs the page) under this: answer without the image
    JEV_NEW_PROBLEM_MIN = 0.60        # P(moved to another problem) to switch focus
    JEV_WHICH_MIN = 0.50              # and confidence in which one
    JEV_ABOUT_MIN = 0.60              # confidence to pass the route on to the writing model

    def __init__(self, brain: Optional[Brain], speak: Speak, notify: Notify, minutes: float = 10,
                 clock: Callable[[], float] = time.monotonic, log_event: Callable[..., None] = lambda *a, **k: None,
                 save_frame: Callable[[np.ndarray], Optional[str]] = lambda img: None,
                 chat_brain: Optional[Brain] = None, decider=None):
        self.brain = brain
        self.chat_brain = chat_brain or brain  # the quick one, for talking back
        self.decider = decider                 # jev.Jev: fast typed decisions, used when .enabled
        self.other_problems: list[str] = []    # other problems the page showed at the last read
        self.speak_cb, self.notify_cb, self.log_event = speak, notify, log_event
        self.save_frame = save_frame  # keeps each judged frame, so we can see what the model saw
        self.clock = clock
        self.minutes = max(1.0, min(30.0, float(minutes)))
        self.watcher = PageWatcher()
        self.phase = "idle"                 # idle -> watching -> ended
        self.started_at = 0.0
        self.ends_at = 0.0
        self.warned_one_minute = False
        self.thinking = False
        self.last_said = ""
        self.last_why = ""
        self.last_spoke_at = -1e9
        self.last_activity = 0.0
        self.last_no_page_at = -1e9
        self.problem: Optional[str] = None
        self.subject: Optional[str] = None  # math | physics | chemistry | other, from the page or the student
        self.topic: Optional[str] = None
        self.mistake: Optional[tuple[int, str]] = None
        # A mistake seen once but not yet spoken about: a second look has to agree before Sensei
        # says anything, because one bad read (a shadow, half a line) should never become a hint.
        self.candidate_mistake: Optional[tuple[int, str]] = None
        self._rechecks = 0  # second looks spent trying to pin down one unstable reading
        self.hint_level = 0
        self.hints_on_mistake = 0        # asked about this one mistake this many times
        self.let_go: set = set()         # mistakes we stopped asking about: don't start over
        self.last_focus_change = -1e9    # when the student last moved us to another problem
        self.mistakes_found: list[str] = []
        self.mistakes_fixed: list[str] = []
        self.hints_given = 0
        self.student_face: Optional[tuple[str, float]] = None  # (expression, clock) from the head's last glance
        self.problems_finished = 0
        self._finished_problem: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        # What was said, in order ({"who": "student" | "sensei", "text", "t"}), and a note of what
        # Sensei last saw, so spoken follow-ups ("what did I ask?", "why?") have context.
        self.conversation: list[dict] = []
        self.last_seen: Optional[str] = None
        self.paused_at = 0.0
        # A question that arrived while Sensei was busy: answered as soon as the current look ends.
        self.pending_question: Optional[tuple[str, Optional[np.ndarray]]] = None
        self.pending_extra = ""  # what Jev said about that question, for when it is answered
        self._jev_turn = None    # Jev's decision on the utterance being answered, if any
        self.pending_jev = None
        self._heard_count = 0
        self.steps_confirmed = 0  # correct lines of the current problem Sensei has already praised
        self._progress_count = 0

    # -- state ---------------------------------------------------------------------------
    def remaining_s(self) -> float:
        if self.phase == "paused":  # the clock stops while paused
            return max(0.0, self.ends_at - self.paused_at)
        return max(0.0, self.ends_at - self.clock()) if self.phase == "watching" else 0.0

    def state(self) -> dict:
        return {"type": "tutor", "phase": self.phase, "remaining_s": round(self.remaining_s()),
                "thinking": self.thinking, "problem": self.problem, "subject": self.subject,
                "topic": self.topic, "hint_level": self.hint_level,
                "hints_given": self.hints_given}

    async def notify(self):
        await self.notify_cb(self.state())

    def remember(self, who: str, text: str):
        self.conversation.append({"who": who, "text": text, "t": round(self.clock() - self.started_at, 1)})
        del self.conversation[:-self.MEMORY_TURNS]

    def recent_conversation(self, skip_last: int = 0) -> str:
        turns = self.conversation[:len(self.conversation) - skip_last] if skip_last else self.conversation
        return "\n".join(f"{'Student' if c['who'] == 'student' else 'Sensei'}: {c['text']}" for c in turns)

    def switch_to(self, problem: str, who: str):
        """Work on a different problem now.

        The camera says what is written; the student says what we are working on. When a
        student moves to a new question, everything Sensei believed about the old one - the
        mistake it was chasing, how far it had escalated - has to go, or the background looks
        keep arguing about work the student has left behind."""
        was, self.problem = self.problem, problem
        self.other_problems = [p for p in self.other_problems if not _same_line(p, problem)]
        if was and self.mistake:
            self.log_event("tutor_dropped_mistake", step=self.mistake[0], problem=was)
        self.mistake = self.candidate_mistake = None
        self.hint_level = self.hints_on_mistake = self._rechecks = self.steps_confirmed = 0
        if who == "student":
            # Only a person gets the quiet moment afterwards; the page noticing a new problem
            # is Sensei talking to itself and shouldn't gag it.
            self.last_focus_change = self.clock()
        self.log_event("tutor_focus", was=was, now=problem, set_by=who)

    def set_subject(self, subject: Optional[str], topic: Optional[str], who: str):
        """The subject decides which playbook Sensei teaches from. "other" never replaces a
        subject we know: a blurry read shouldn't turn a physics lesson generic."""
        if not subject or (subject == "other" and self.subject):
            return
        if subject != self.subject:
            self.log_event("tutor_subject", was=self.subject, now=subject, topic=topic, set_by=who)
            self.subject = subject
        if topic:
            self.topic = topic

    async def speak(self, text: str, why: str):
        # Saying the same sentence for the same reason is how a person sounds when they aren't
        # listening. A new reason (a hint escalating, or "repeat" itself) always goes through,
        # even when the model hands back the same words.
        if (why == self.last_why and why != "repeat" and _same_line(text, self.last_said)
                and self.clock() - self.last_spoke_at < self.REPEAT_GAP_S):
            self.log_event("tutor_repeat_suppressed", text=text, why=why)
            return
        if why not in ("ack", "busy"):  # "let me look" fillers aren't part of the conversation
            self.remember("sensei", text)
        self.last_said, self.last_spoke_at, self.last_why = text, self.clock(), why
        self.log_event("tutor_say", text=text, why=why)
        await self.speak_cb(text, why)

    # -- lifecycle -----------------------------------------------------------------------
    async def start(self):
        now = self.clock()
        self.phase, self.started_at, self.ends_at = "watching", now, now + self.minutes * 60
        self.last_activity = now
        self.log_event("tutor_start", minutes=self.minutes)
        await self.speak(GREETING if self.brain else f"{GREETING} {NO_BRAIN}", "greeting")
        await self.notify()

    def stop(self):
        """The call is gone: end without speaking; a reply still on its way is dropped."""
        if self.phase != "ended":
            self.phase = "ended"
            self.log_event("tutor_end", reason="disconnected")

    async def end(self, reason: str = "ended"):
        if self.phase == "ended":
            return
        self.phase = "ended"
        summary = await self._summary()
        self.log_event("tutor_end", reason=reason, summary=summary)
        await self.speak(summary, "wrap_up")
        await self.notify_cb({"type": "session_ended", "summary": summary, **self._stats()})

    def _stats(self) -> dict:
        return {"minutes": round((self.clock() - self.started_at) / 60, 1), "hints_given": self.hints_given,
                "mistakes_found": len(self.mistakes_found), "mistakes_fixed": len(self.mistakes_fixed),
                "problems_finished": self.problems_finished}

    async def _summary(self) -> str:
        notes = (f"Subject: {self.subject or 'unknown'}{f' ({self.topic})' if self.topic else ''}. "
                 f"Problem: {self.problem or 'unknown'}. Problems finished: {self.problems_finished}. "
                 f"Mistakes found: {self.mistakes_found or 'none'}. Fixed: {self.mistakes_fixed or 'none'}. "
                 f"Hints given: {self.hints_given}.")
        if self.brain and (self.mistakes_found or self.problems_finished):
            try:
                text = await asyncio.to_thread(self.brain.wrap_up, notes)
                if text:
                    return text
            except Exception as e:  # the summary is a nicety: never fail the ending over it
                log.warning("wrap-up failed: %s", e)
        return GOODBYE

    # -- inputs --------------------------------------------------------------------------
    async def tick(self):
        """Timers: call about once a second."""
        if self.phase != "watching":
            return
        left = self.remaining_s()
        if left <= 0:
            await self.end("time_up")
            return  # session_ended is the last message the phone gets
        if left <= 60 and not self.warned_one_minute:
            self.warned_one_minute = True
            await self.speak(ONE_MINUTE, "time")
        elif (self.clock() - self.last_activity > self.IDLE_S and self.clock() - self.last_spoke_at > self.IDLE_S
              and not self.thinking):
            self.last_activity = self.clock()
            await self.speak(IDLE_NUDGE, "idle")
        await self.notify()

    async def on_frame(self, img: np.ndarray):
        """A new camera frame (call ~2x a second). Judges the page when it has settled."""
        if self.phase != "watching" or self.thinking:
            return
        settled = self.watcher.update(img)
        if self.watcher.still == 0:  # hand moving over the page
            self.last_activity = self.clock()
        if settled:
            self.watcher.mark_judged()
            await self._judge(img, request=None)

    async def request(self, what: str, img: Optional[np.ndarray]):
        """A button on the phone: "hint", "check", "look" (what do you see?), "repeat", "pause",
        "resume" or "end"."""
        self.log_event("tutor_request", what=what)
        if what == "end":
            await self.end("student")
        elif what == "pause" and self.phase == "watching":
            self.phase, self.paused_at = "paused", self.clock()  # stops looking, listening and the clock
            self.log_event("tutor_pause")
            await self.speak(PAUSED, "pause")
            await self.notify()
        elif what == "resume" and self.phase == "paused":
            now = self.clock()
            self.ends_at += now - self.paused_at  # paused time doesn't count
            self.phase, self.last_activity = "watching", now
            self.watcher = PageWatcher()  # the page may have changed while paused: look afresh
            self.log_event("tutor_resume", paused_s=round(now - self.paused_at, 1))
            await self.speak(RESUMED, "resume")
            await self.notify()
        elif what == "repeat":
            if self.last_said:
                await self.speak(self.last_said, "repeat")
        elif what in ("hint", "check", "look") and self.phase == "watching":
            if img is None:
                await self.speak(NO_PAGE, "no_page")
            elif self.thinking:
                # Already looking: say so (not on every tap) instead of ignoring the student.
                if self.clock() - self.last_spoke_at > self.MIN_GAP_S:
                    await self.speak(STILL_LOOKING, "busy")
            else:
                self.watcher.mark_judged()
                if self.brain is not None:
                    # A thinking model takes a while: answer the tap right away.
                    await self.speak(LOOKING[what], "ack")
                await self._judge(img, request=what)

    def awaiting_answer(self) -> bool:
        """Sensei has just asked the student a question and is owed a reply."""
        last = self.conversation[-1] if self.conversation else None
        return bool(last and last["who"] == "sensei" and last["text"].rstrip().endswith("?")
                    and self.clock() - self.last_spoke_at < self.ANSWER_WINDOW_S)

    async def hear(self, text: str, img: Optional[np.ndarray]):
        """The student said something (voice mode): answer it, looking at the page too."""
        decision = None
        if (self.phase == "watching" and self.brain is not None and self.decider is not None
                and self.decider.enabled and not wants_repeat(text)):
            decision = await self._consult_jev(text)
        if decision is not None:
            # Jev decided. The floor is lower right after Sensei asked something: a bare "no"
            # is then an answer, and wrongly ignoring an answer is worse than a spare reply.
            # The floors belong to the backend: each spreads its probabilities differently.
            j = self.decider
            floor = (getattr(j, "skip_if_answer_below", self.JEV_SKIP_IF_ANSWER_BELOW) if self.awaiting_answer()
                     else getattr(j, "skip_below", self.JEV_SKIP_BELOW))
            steering = (decision.new_problem >= self.JEV_NEW_PROBLEM_MIN
                        or (decision.about == "steer" and decision.about_confidence >= self.JEV_ABOUT_MIN))
            if decision.respond < floor and not steering:  # being redirected is never ignorable
                self.log_event("student_said", text=text, answered=False, by="jev")
                self.last_activity = self.clock()
                return
        elif is_filler(text) and not self.awaiting_answer():
            # "Okay.", "Thank you.", a cough Whisper turned into a word. A tutor hears these and
            # carries on; answering each one buries the student in talk they didn't ask for.
            # But a bare "no" right after Sensei asked something IS the answer, not noise.
            self.log_event("student_said", text=text, answered=False)
            self.last_activity = self.clock()
            return
        self.log_event("student_said", text=text)
        self.remember("student", text)
        if named_subject(text):
            self.set_subject(named_subject(text), None, who="student")
        if self.phase != "watching":
            return
        if wants_repeat(text) and self.last_said:
            self.log_event("tutor_about", about="sensei", answered_from="memory")
            await self.speak(self.last_said, "repeat")
            return
        self.last_activity = self.clock()
        extra = ""
        self._jev_turn = decision  # _react consults it before letting a reply move the focus
        if decision is not None:
            img, extra = self._apply_jev(decision, img)
        if self.brain is None:
            await self.speak(NO_BRAIN, "no_brain")
        elif self.thinking:
            # Never drop a question: keep it (the latest one wins) and answer when the look ends.
            self.pending_question, self.pending_extra, self.pending_jev = (text, img), extra, decision
            await self.speak(HEARD_WHILE_BUSY, "busy")
        else:
            await self._answer(text, img, extra)

    # -- fast decisions (jev.py) ---------------------------------------------------------
    def _jev_state(self, said: str) -> dict:
        """What Jev needs to judge one utterance, and nothing else: accuracy falls as the state
        fills with irrelevant detail, so this is built on purpose, not dumped from the log."""
        turns = [f"{'Student' if c['who'] == 'student' else 'Sensei'}: {c['text']}"
                 for c in self.conversation[-6:]]
        return {
            "problem_in_focus": self.problem or "none yet",
            "subject": self.subject or "unknown",
            "page_as_last_read": self.last_seen or "nothing read yet",
            "other_problems_on_page": self.other_problems,
            "recent_conversation": turns,
            "sensei_last_said": self.last_said or "",
            "sensei_asked_a_question": self.awaiting_answer(),
            "student_just_said": said,
        }

    async def _consult_jev(self, said: str):
        """One Jev call for "the student finished speaking". None means decide without it:
        Jev being off, slow or down must never stop Sensei answering."""
        try:
            d = await asyncio.to_thread(decide_utterance, self.decider, self._jev_state(said),
                                        list(self.other_problems))
        except Exception as e:
            log.warning("jev unavailable, deciding without it: %s", e)
            self.log_event("jev_error", error=str(e)[:200])
            return None
        self.log_event("jev", on="utterance", said=said, **d.summary())
        return d

    def _apply_jev(self, d, img: Optional[np.ndarray]) -> tuple[Optional[np.ndarray], str]:
        """Turn a decision into what the answer needs: which problem, whether to send the
        page, and a note to the writing model. Returns (image or None, note)."""
        notes = []
        if (d.new_problem >= self.JEV_NEW_PROBLEM_MIN and d.which_problem
                and d.which_confidence >= self.JEV_WHICH_MIN
                and not _same_line(d.which_problem, self.problem)):
            self.switch_to(d.which_problem, who="student")
            notes.append(f"They have moved on to a new problem: {d.which_problem}. Help with that one.")
        if d.about_confidence >= self.JEV_ABOUT_MIN:
            notes.append(f"A quick read of what they said: it is about \"{d.about}\" "
                         "(use that for \"about\" unless you clearly see otherwise).")
        no_page_below = getattr(self.decider, "no_page_below", self.JEV_NO_PAGE_BELOW)
        if img is not None and d.needs_page < no_page_below and d.about in ("subject", "social", "sensei"):
            img = None  # an idea, small talk or "what did you say" doesn't need the desk
            notes.append("No camera image this time: answer from the conversation and the problem, "
                         "and don't mention the camera.")
        return img, " ".join(notes)

    async def _answer(self, text: str, img: Optional[np.ndarray], extra: str = ""):
        """Answer with eyes and ears. A beat of silence is how people talk, so Sensei only fills
        it if the model is taking longer than a natural pause — otherwise the answer just comes."""
        task = asyncio.ensure_future(self._judge(img, request="talk", said=text, extra=extra))
        done, _ = await asyncio.wait([task], timeout=self.ACK_AFTER_S)
        jev = self._jev_turn
        small_talk = jev is not None and jev.about == "social" and jev.about_confidence >= self.JEV_ABOUT_MIN
        if not done and not small_talk:  # "Okay, let me think." before "Hello back!" sounds odd
            self._heard_count += 1
            await self.speak(HEARD[(self._heard_count - 1) % len(HEARD)], "ack")
        await task

    # -- deciding what to say ------------------------------------------------------------
    FACE_FRESH_S = 60.0

    def note_face(self, expression: str):
        """The pan-tilt head glanced at the student (gaze.py) and saw this expression."""
        self.student_face = (expression, self.clock())
        self.log_event("student_face", expression=expression)

    def _face_note(self) -> str:
        if not self.student_face or self.clock() - self.student_face[1] > self.FACE_FRESH_S:
            return ""
        expression, at = self.student_face
        if expression in ("engaged", "happy", "away"):
            return ""
        return (f"A glance at the student's face {self.clock() - at:.0f} seconds ago: they looked {expression}. "
                "Let that set your tone (patient, encouraging); don't mention their face.\n")

    def _instructions(self, request: Optional[str], said: Optional[str] = None) -> str:
        if request == "talk":
            history = self.recent_conversation(skip_last=1)  # the last turn is `said` itself
            return ("You are in a spoken conversation with the student.\n"
                    + (f"Conversation so far (oldest first):\n{history}\n" if history else "")
                    # Context, NOT an instruction to stay put: handing the model the current
                    # problem made it answer "that's a different equation, let's focus on
                    # x + 5 = 7 first" to a student who had said four times that they had
                    # moved on.
                    + (f"Up to now they have been working on: {self.problem}. If they are asking "
                       "about something else, go with them - set \"focus\" and help with the new "
                       "one. Never tell them to go back.\n" if self.problem else "")
                    + (f"Subject: {self.subject}{f' ({self.topic})' if self.topic else ''}.\n"
                       if self.subject and self.subject != "other" else "")
                    + (f"What you last saw in the camera: {self.last_seen}\n" if self.last_seen else "")
                    + self._face_note()
                    + f"The student just said out loud: \"{said}\"\n"
                    "Decide \"about\" first, then answer accordingly. The camera image is attached so you can "
                    "use it IF they were asking about it - if they weren't, it is background you should ignore, "
                    "not something to describe. If they answered your question or explained a step, tell them "
                    "honestly whether it's on the right track, without giving away the final answer."
                    + (f" Earlier you asked them about step {self.mistake[0]} of their work."
                       if self.mistake else ""))
        parts = [self._face_note().strip()] if self._face_note() else []
        if self.subject in PLAYBOOKS and request != "look":
            parts.append(PLAYBOOKS[self.subject] + " For \"error_kind\" use one of: "
                         + ", ".join(MISTAKE_KINDS[self.subject]) + ".")
        else:
            parts.append("Set \"subject\" and \"topic\" from what you see.")
        if self.problem:
            parts.append(f"The problem in focus is: {self.problem}. Judge only the lines that "
                         "belong to it; put any other problem on the page in \"other_problems\".")
        if self.mistake:
            parts.append(f"Earlier you flagged step {self.mistake[0]} as the first mistake and gave a "
                         f"level {self.hint_level} hint. If that same mistake is still there, use hint level "
                         f"{min(3, self.hint_level + 1)}. If it is now fixed, briefly say so warmly and "
                         "encourage them to continue. If there is a different first mistake, use level 1.")
        else:
            parts.append("If there is a mistake, use hint level 1.")
        if request == "look":
            return ("The student asked: what do you see? In \"say\", describe what is in view right now "
                    "in one or two short spoken sentences, reading out any important text. Do not look for "
                    "mistakes; set first_error to null.")
        if request == "hint":
            parts.append("The student tapped Hint and wants help now. If there is no mistake, ask one "
                         "question that helps them take the next step, without doing it for them.")
        elif request == "check":
            parts.append("The student asked you to check their work. Tell them in one sentence whether it "
                         "looks right so far; if not, ask your guiding question.")
        else:
            check = SELF_CHECK.get(self.subject or "", "check it another way")
            parts.append("If this is work and every step so far is correct and unfinished, set \"say\" to null. If they "
                         f"finished correctly, congratulate them in a few words and ask them to {check} themselves.")
        return " ".join(parts)

    async def _judge(self, img: Optional[np.ndarray], request: Optional[str], said: Optional[str] = None,
                     extra: str = ""):
        if self.brain is None:
            if request:
                await self.speak(NO_BRAIN, "no_brain")
            return
        # Talking needs to come back in a couple of seconds; judging maths needs to be right.
        # When a second, faster model is configured, conversation goes to it and the page to the
        # careful one. With only one model configured, both are the same brain.
        brain = self.chat_brain if request in ("talk", "look") else self.brain
        self.thinking = True
        await self.notify()
        frame_file = self.save_frame(img) if img is not None else None
        frame_size = [img.shape[1], img.shape[0]] if img is not None else None
        t0 = self.clock()
        system = CHAT_SYSTEM_PROMPT if request in ("talk", "look") else SYSTEM_PROMPT
        try:
            a = await asyncio.to_thread(brain.assess, img, self._instructions(request, said) + (f"\n{extra}" if extra else ""), system)
        except Exception as e:
            log.warning("assessment failed: %s", e)
            self.log_event("tutor_error", error=str(e)[:300], frame=frame_file, frame_size=frame_size)
            if request:
                await self.speak(BRAIN_ERROR, "error")
            return
        finally:
            self.thinking = False
        if self.phase != "watching":  # the session ended (or the call dropped) while we thought
            self.log_event("tutor_late_reply", latency_s=round(self.clock() - t0, 2), request=request)
            return
        self.last_seen = self._describe_seen(a)
        if a.page == "work" and request != "talk":
            self.set_subject(a.subject, a.topic, who="page")
        if a.page == "work" and request != "talk":  # replies to speech don't re-read the whole page
            self.other_problems = [p for p in a.other_problems if not _same_line(p, self.problem)]
        self.log_event("tutor_assessment", latency_s=round(self.clock() - t0, 2), request=request,
                       model=getattr(brain, "model", None), frame=frame_file, frame_size=frame_size,
                       page=a.page, about=a.about, focus=a.focus, problem=a.problem,
                       subject=a.subject, topic=a.topic,
                       other_problems=a.other_problems, given=a.given, steps=a.steps,
                       first_error=a.first_error, error_kind=a.error_kind, finished=a.finished,
                       hand_over_page=a.hand_over_page, rotated=a.rotated, say=a.say)
        if self.pending_question and request is None:
            # The student asked something while this background look ran: their question comes first.
            self.log_event("tutor_superseded", page=a.page)
        else:
            await self._react(a, request)
        await self.notify()
        if self.pending_question and self.phase == "watching":
            text, img = self.pending_question
            extra, self.pending_question, self.pending_extra = self.pending_extra, None, ""
            self._jev_turn, self.pending_jev = self.pending_jev, None
            await self._answer(text, img, extra)

    @staticmethod
    def _describe_seen(a: Assessment) -> str:
        kind = {"work": "student work", "other": "something other than homework",
                "unreadable": "something too unclear to read", "none": "nothing meaningful"}.get(a.page, a.page)
        lines = "; ".join(a.steps[:6])
        return f"{kind}" + (f" ({a.problem})" if a.problem else "") + (f": {lines}" if lines else "")

    async def _react(self, a: Assessment, request: Optional[str]):
        now = self.clock()
        if request == "talk":  # the student spoke: answer them
            # "why" stays "reply" (the phone keys off it); the route is in tutor_assessment.about
            self.log_event("tutor_about", about=a.about or "unrouted")
            # They get to decide what we work on. Nothing the camera saw outranks this.
            jev = self._jev_turn
            # The writing model sets "focus" as a side effect of composing a reply, and has moved
            # it to "what is a coefficient?" before now. When Jev judged this turn, only move if
            # Jev also thinks they moved to another problem.
            if (a.focus and not _same_line(a.focus, self.problem)
                    and (jev is None or jev.new_problem >= self.JEV_NEW_PROBLEM_MIN)):
                self.switch_to(a.focus, who="student")
            await self.speak(a.say or SAY_AGAIN, "reply")
            return
        if request == "look":  # "What do you see?": just describe it
            await self.speak(a.say or (UNREADABLE if a.page == "unreadable" else NO_PAGE), "look")
            return
        if a.hand_over_page:
            # Mid-stroke: whatever we read is a fragment. Wait for the hand to lift.
            self.last_activity = now
            if request in ("hint", "check"):
                await self.speak(STILL_WRITING, "writing")
            return
        if a.rotated and a.page == "work":
            if request or now - self.last_no_page_at > self.NO_PAGE_REPEAT_S:
                self.last_no_page_at = now
                await self.speak(a.say or TURN_PAGE, "rotated")
            return
        if a.page != "work" and request is None:
            return  # background looks only speak up about study work; the room is talked about on request
        if a.page == "other":
            if a.say:
                await self.speak(a.say, "other")
            return
        if a.page != "work":
            if request or now - self.last_no_page_at > self.NO_PAGE_REPEAT_S:
                self.last_no_page_at = now
                # The model's own words say what it actually sees ("This isn't a math problem...");
                # the fixed lines are only for when it gives none.
                await self.speak(a.say or (UNREADABLE if a.page == "unreadable" else NO_PAGE), a.page)
            return
        if request is None and now - self.last_focus_change < self.STEER_GRACE_S:
            # The student has just told us what to work on. Let them get on with it instead of
            # a background look immediately talking about something else.
            return
        if a.problem:
            if self.problem is None or not _same_line(a.problem, self.problem):
                if self.problem is None or self._finished_problem == self.problem:
                    self.switch_to(a.problem, who="page")
        # Finished this one and written the next underneath? Congratulate on the one they
        # finished first, then move across - so the switch happens after we have spoken, not
        # before, or the praise would be aimed at a problem they haven't started.
        next_problem = a.other_problems[0] if (a.finished and a.other_problems) else None

        mistake = a.mistake
        if mistake and mistake in self.let_go and request is None:
            return  # we already asked about this one three times; only a direct ask reopens it
        if mistake and mistake == self.mistake and request is None and now - self.last_spoke_at < self.HINT_WAIT_S:
            return  # same mistake, just hinted: let them find it themselves before asking again
        if mistake and mistake != self.mistake and request is None:
            # A mistake we haven't raised yet. One look is not enough: read it again first.
            # A student who writes a wrong line and then sits still never changes the page, so
            # ask the watcher for another look at this same page rather than waiting for one.
            if mistake != self.candidate_mistake and self._rechecks < self.MAX_RECHECKS:
                self.candidate_mistake = mistake
                self._rechecks += 1
                self.watcher.judged = None
                self.log_event("tutor_unconfirmed", step=mistake[0], line=a.steps[mistake[0] - 1])
                return
        self._rechecks = 0
        if not mistake:
            self.candidate_mistake = None
        why = None
        if mistake:
            if mistake == self.mistake:
                self.hints_on_mistake += 1
                if self.hints_on_mistake > self.MAX_HINTS_PER_MISTAKE:
                    # Asking a fourth time in different words is not teaching, it is nagging.
                    # Let it go, say so once, and leave the student room to do something else.
                    self.log_event("tutor_let_it_go", step=mistake[0], hints=self.hints_on_mistake)
                    self.let_go.add(mistake)  # or the next look starts the same chase at level 1
                    self.mistake, self.hint_level, self.hints_on_mistake = None, 0, 0
                    await self.speak(MOVE_ON, "let_it_go")
                    return
                self.hint_level = min(3, self.hint_level + 1)
                why = f"hint_{self.hint_level}"
            else:
                self.mistake, self.hint_level, self.hints_on_mistake = mistake, 1, 1
                self.candidate_mistake = None
                self.mistakes_found.append(f"step {mistake[0]}: {a.steps[mistake[0] - 1]} ({a.error_kind or 'error'})")
                why = "hint_1"
            self.hints_given += 1
        elif self.mistake:
            self.mistakes_fixed.append(f"step {self.mistake[0]}")
            self.mistake, self.hint_level = None, 0
            why = "fixed"
        elif a.finished and self._finished_problem != (a.problem or self.problem):
            self._finished_problem = a.problem or self.problem
            self.problems_finished += 1
            why = "finished"
        elif request:
            why = request
        say = a.say
        if why is None and not a.finished and len(a.steps) - self.steps_confirmed >= self.PROGRESS_STEPS \
                and now - self.last_spoke_at >= self.PROGRESS_GAP_S:
            self._progress_count += 1
            why, say = "progress", PROGRESS[(self._progress_count - 1) % len(PROGRESS)]
        if why in ("fixed", "finished", "progress", "check", "hint"):
            self.steps_confirmed = len(a.steps) if not mistake else self.steps_confirmed

        if why is not None and say:
            await self.speak(say, why)
        if next_problem and not _same_line(next_problem, self.problem):
            # Only now: they have finished one problem and started another on the same page.
            self.switch_to(next_problem, who="page")
            self.log_event("tutor_next_problem", problem=self.problem)
