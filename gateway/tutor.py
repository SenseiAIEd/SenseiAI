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

    def __init__(self, still_samples: int = 3):
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


# --- Reading the page ----------------------------------------------------------------------
@dataclass
class Assessment:
    page: str = "none"                  # "none" | "unreadable" | "work" | "other" (anything else it can see)
    problem: Optional[str] = None       # the problem being solved, as written
    steps: list[str] = field(default_factory=list)
    first_error: Optional[int] = None   # 1-based index into steps, or None
    error_kind: Optional[str] = None    # e.g. "sign", "arithmetic", "rule"
    finished: bool = False              # reached a final answer
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
     other: anything else you can make out (a screen of code or text, a diagram, a book, an object, a scene)
     unreadable: something is there but too blurry, dark or covered to read
     none: nothing meaningful in view
  "problem": "the problem being solved, or null",
  "steps": ["each line of the working (or of the readable text you see), in order, as written"],
  "first_error": <1-based index of the FIRST incorrect step, or null if all correct so far>,
  "error_kind": "sign" | "arithmetic" | "rule" | "concept" | "copying" | null,
  "finished": <true if the student reached a final answer>,
  "say": "what you say to the student now (spoken aloud), or null"
}

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


def strip_reasoning(text: str) -> str:
    """Drop a thinking model's reasoning and keep its answer. Handles <think>...</think>,
    a lone </think> (when the chat template opened the block), and <answer>...</answer>."""
    if "</think>" in text:
        text = text.split("</think>")[-1]
    m = re.search(r"<answer>(.*?)(</answer>|$)", text, flags=re.S)
    return (m.group(1) if m else text).strip()


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
    return Assessment(
        page=str(data.get("page") or "work"),
        problem=data.get("problem") or None,
        steps=steps,
        first_error=first_error,
        error_kind=data.get("error_kind") or None,
        finished=bool(data.get("finished")),
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
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
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

    def _chat(self, messages: list, max_tokens: int) -> str:
        res = httpx.post(self.url, headers=self.headers, timeout=self.timeout_s, json={
            "model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.2})
        res.raise_for_status()
        # With a reasoning parser (e.g. vLLM --reasoning-parser qwen3) the thinking arrives in
        # "reasoning_content" and "content" is just the answer; without one, both are in content.
        return res.json()["choices"][0]["message"].get("content") or ""

    def assess(self, img: Optional[np.ndarray], instructions: str) -> Assessment:
        content = [{"type": "text", "text": instructions}]
        if img is not None:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{to_jpeg_b64(img)}"}})
        reply = self._chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ], max_tokens=self.max_tokens)
        return parse_assessment(reply)

    def wrap_up(self, notes: str) -> str:
        reply = self._chat([
            {"role": "system", "content": "You are Sensei, a warm Socratic tutor. Speak simply; this is read aloud."},
            {"role": "user", "content": "The tutoring session is over. In two or three short spoken sentences, "
                                        "tell the student what they worked on, what they fixed, and one thing to "
                                        f"remember next time. Session notes:\n{notes}"},
        ], max_tokens=self.max_tokens)
        return strip_reasoning(reply)


# --- The session ---------------------------------------------------------------------------
GREETING = ("Hi, I'm Sensei. Put your notebook under the camera and start solving. "
            "I'll watch, and ask you a question if something looks off. Tap Hint any time you want help.")
NO_PAGE = "I can't see your page yet. Please put your notebook under the camera."
UNREADABLE = "I can't read that clearly. Could you move your hand, or write a little larger?"
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

Speak = Callable[[str, str], Awaitable[None]]    # (text, why) -> spoken on the phone
Notify = Callable[[dict], Awaitable[None]]        # state update for the phone


class Tutor:
    """One interactive session. Feed it frames (`on_frame`) and button presses
    (`request`); it speaks through `speak` and reports its state through `notify`."""

    MIN_GAP_S = 8.0          # between unprompted remarks
    MEMORY_TURNS = 20        # conversation turns kept for context
    IDLE_S = 90.0            # no new writing for this long -> a gentle check-in
    NO_PAGE_REPEAT_S = 30.0  # how often to repeat "I can't see your page"

    def __init__(self, brain: Optional[Brain], speak: Speak, notify: Notify, minutes: float = 10,
                 clock: Callable[[], float] = time.monotonic, log_event: Callable[..., None] = lambda *a, **k: None,
                 save_frame: Callable[[np.ndarray], Optional[str]] = lambda img: None):
        self.brain = brain
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
        self.last_spoke_at = -1e9
        self.last_activity = 0.0
        self.last_no_page_at = -1e9
        self.problem: Optional[str] = None
        self.mistake: Optional[tuple[int, str]] = None
        self.hint_level = 0
        self.mistakes_found: list[str] = []
        self.mistakes_fixed: list[str] = []
        self.hints_given = 0
        self.problems_finished = 0
        self._finished_problem: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        # What was said, in order ({"who": "student" | "sensei", "text", "t"}), and a note of what
        # Sensei last saw, so spoken follow-ups ("what did I ask?", "why?") have context.
        self.conversation: list[dict] = []
        self.last_seen: Optional[str] = None
        self.paused_at = 0.0

    # -- state ---------------------------------------------------------------------------
    def remaining_s(self) -> float:
        if self.phase == "paused":  # the clock stops while paused
            return max(0.0, self.ends_at - self.paused_at)
        return max(0.0, self.ends_at - self.clock()) if self.phase == "watching" else 0.0

    def state(self) -> dict:
        return {"type": "tutor", "phase": self.phase, "remaining_s": round(self.remaining_s()),
                "thinking": self.thinking, "problem": self.problem, "hint_level": self.hint_level,
                "hints_given": self.hints_given}

    async def notify(self):
        await self.notify_cb(self.state())

    def remember(self, who: str, text: str):
        self.conversation.append({"who": who, "text": text, "t": round(self.clock() - self.started_at, 1)})
        del self.conversation[:-self.MEMORY_TURNS]

    def recent_conversation(self, skip_last: int = 0) -> str:
        turns = self.conversation[:len(self.conversation) - skip_last] if skip_last else self.conversation
        return "\n".join(f"{'Student' if c['who'] == 'student' else 'Sensei'}: {c['text']}" for c in turns)

    async def speak(self, text: str, why: str):
        if why not in ("ack", "busy"):  # "let me look" fillers aren't part of the conversation
            self.remember("sensei", text)
        self.last_said, self.last_spoke_at = text, self.clock()
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
        notes = (f"Problem: {self.problem or 'unknown'}. Problems finished: {self.problems_finished}. "
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

    async def hear(self, text: str, img: Optional[np.ndarray]):
        """The student said something (voice mode): answer it, looking at the page too."""
        self.log_event("student_said", text=text)
        self.remember("student", text)
        if self.phase != "watching":
            return
        if self.brain is None:
            await self.speak(NO_BRAIN, "no_brain")
        elif self.thinking:
            if self.clock() - self.last_spoke_at > self.MIN_GAP_S:
                await self.speak(STILL_LOOKING, "busy")
        else:
            self.last_activity = self.clock()
            await self._judge(img, request="talk", said=text)

    # -- deciding what to say ------------------------------------------------------------
    def _instructions(self, request: Optional[str], said: Optional[str] = None) -> str:
        if request == "talk":
            history = self.recent_conversation(skip_last=1)  # the last turn is `said` itself
            return ("You are in a spoken conversation with the student.\n"
                    + (f"Conversation so far (oldest first):\n{history}\n" if history else "")
                    + (f"What you last saw in the camera: {self.last_seen}\n" if self.last_seen else "")
                    + f"The student just said out loud: \"{said}\"\n"
                    "Answer them as Sensei in \"say\": one to three short spoken sentences. Use the image and the "
                    "conversation. If they ask what they asked, what you understood or what you said, answer from "
                    "the conversation, quoting briefly. If they ask what you see, describe it. If they answered "
                    "your question or explained a step, tell them honestly whether it's on the right track, "
                    "without giving away the final answer. If what they said is unclear or seems misheard, say "
                    "what you heard and ask them to repeat. If it's small talk, reply warmly and steer back to "
                    "their work."
                    + (f" Earlier you asked about step {self.mistake[0]} of their work." if self.mistake else ""))
        parts = []
        if self.problem:
            parts.append(f"The student is working on: {self.problem}.")
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
            parts.append("If this is work and every step so far is correct and unfinished, set \"say\" to null. If they "
                         "finished correctly, congratulate them and ask them to explain why their key step works.")
        return " ".join(parts)

    async def _judge(self, img: Optional[np.ndarray], request: Optional[str], said: Optional[str] = None):
        if self.brain is None:
            if request:
                await self.speak(NO_BRAIN, "no_brain")
            return
        self.thinking = True
        await self.notify()
        frame_file = self.save_frame(img) if img is not None else None
        frame_size = [img.shape[1], img.shape[0]] if img is not None else None
        t0 = self.clock()
        try:
            a = await asyncio.to_thread(self.brain.assess, img, self._instructions(request, said))
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
        self.log_event("tutor_assessment", latency_s=round(self.clock() - t0, 2), request=request,
                       frame=frame_file, frame_size=frame_size,
                       page=a.page, problem=a.problem, steps=a.steps, first_error=a.first_error,
                       error_kind=a.error_kind, finished=a.finished, say=a.say)
        await self._react(a, request)
        await self.notify()

    @staticmethod
    def _describe_seen(a: Assessment) -> str:
        kind = {"work": "student work", "other": "something other than homework",
                "unreadable": "something too unclear to read", "none": "nothing meaningful"}.get(a.page, a.page)
        lines = "; ".join(a.steps[:6])
        return f"{kind}" + (f" ({a.problem})" if a.problem else "") + (f": {lines}" if lines else "")

    async def _react(self, a: Assessment, request: Optional[str]):
        now = self.clock()
        if request == "talk":  # the student spoke: answer them
            await self.speak(a.say or SAY_AGAIN, "reply")
            return
        if request == "look":  # "What do you see?": just describe it
            await self.speak(a.say or (UNREADABLE if a.page == "unreadable" else NO_PAGE), "look")
            return
        if a.page == "other":
            # Not study work, but something Sensei can talk about: say what it sees, ask about it.
            if a.say and (request or now - self.last_spoke_at > self.MIN_GAP_S):
                await self.speak(a.say, "other")
            return
        if a.page != "work":
            if request or now - self.last_no_page_at > self.NO_PAGE_REPEAT_S:
                self.last_no_page_at = now
                # The model's own words say what it actually sees ("This isn't a math problem...");
                # the fixed lines are only for when it gives none.
                await self.speak(a.say or (UNREADABLE if a.page == "unreadable" else NO_PAGE), a.page)
            return
        if a.problem:
            if self.problem and a.problem != self.problem and self._finished_problem == self.problem:
                self.mistake, self.hint_level = None, 0  # moved on to a new problem
            self.problem = a.problem

        mistake = a.mistake
        if mistake and mistake == self.mistake and request is None and now - self.last_spoke_at < self.MIN_GAP_S:
            return  # same mistake, but we just spoke: don't nag, escalate on a later look
        why = None
        if mistake:
            if mistake == self.mistake:
                self.hint_level = min(3, self.hint_level + 1)
                why = f"hint_{self.hint_level}"
            else:
                self.mistake, self.hint_level = mistake, 1
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

        if why is None or not a.say:
            return  # on track: stay quiet
        await self.speak(a.say, why)
