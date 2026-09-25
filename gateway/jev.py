"""
Fast typed decisions for Sensei: Jev, a "System One" model (docs/jev-in-sensei.md).

Jev takes a small state and some typed questions and returns probabilities, in one call of a
few hundred milliseconds. It can't see the page and it can't write a sentence; Sensei uses it to
decide things the vision model used to decide as a side effect of writing a reply.

  Jev                 client for the /v1/systemone format, spoken by hosted Jev (api.typesafe.ai)
                      and by our local JevK5 and SemIf servers (~/projects/jev-local/serve.py)
  decide_utterance    the "student finished speaking" event: answer it or not, what it is about,
                      whether it needs the page, whether they moved to another problem

Configuration (all optional; with USE_JEV unset Sensei behaves exactly as before):
  USE_JEV             1 to use it from startup (it can also be switched at runtime: POST /jev)
  SENSEI_JEV_BACKEND  jevk5 (default, local, offline) | semif | decider | decider-v2 (local)
                      | hosted (TypeSafe)
  SENSEI_JEV_URL      override the chosen backend's URL
  SENSEI_JEV_MODEL    model name sent to hosted Jev, default jev-latest
  SENSEI_JEV_KEY      hosted only: the API key; or TYPESAFEAI_KEY; or read from SENSEI_JEV_KEY_FILE
                      (default ~/projects/jev/.env, so the key isn't copied around)
  SENSEI_JEV_TIMEOUT  seconds, default 1.5: slower than that and Sensei decides without it
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger("sensei.jev")

HOSTED_URL = "https://api.typesafe.ai/v1/systemone"

# Each backend's probabilities are spread differently, so the floors travel with the backend,
# not the tutor. Chosen on evals/utterances.jsonl (25 Sep, 47 cases), counting a silence on a
# real question as worse than an unneeded reply (jev_eval.py --backend NAME --sweep). With the
# "being redirected is never ignored" rule:
#                        floor  silent  extra  reply   new problem  median
#   jevk5 (default)       0.25     1      3    43/47     43/44      375 ms
#   semif                 0.15     0      8    39/47     43/44      384 ms
#   decider (4b v2.1)     0.35     1      5    41/47     44/44      585 ms
#   decider-v2 (4b v2)    0.25     1      6    40/47     44/44      685 ms
#   hosted                0.40     1      1    45/47     43/44      264 ms
# no_page_below: under it (and asked about an idea), answer without the image. Set a little
# under the lowest score each model gave a question that did need the page.
BACKENDS = {
    "jevk5": {"url": "http://127.0.0.1:8095/v1/systemone",
              "skip_below": 0.25, "skip_if_answer_below": 0.15, "no_page_below": 0.15},
    "semif": {"url": "http://127.0.0.1:8096/v1/systemone",
              "skip_below": 0.15, "skip_if_answer_below": 0.05, "no_page_below": 0.15},
    "decider": {"url": "http://127.0.0.1:8097/v1/systemone",
                "skip_below": 0.35, "skip_if_answer_below": 0.25, "no_page_below": 0.10},
    "decider-v2": {"url": "http://127.0.0.1:8098/v1/systemone",
                   "skip_below": 0.25, "skip_if_answer_below": 0.15, "no_page_below": 0.05},
    "hosted": {"url": HOSTED_URL,
               "skip_below": 0.40, "skip_if_answer_below": 0.30, "no_page_below": 0.30},
}


def _key_from_file(path: Path) -> str:
    try:
        text = path.read_text()
    except OSError:
        return ""
    m = re.search(r"^\s*(?:export\s+)?(?:TYPESAFEAI_KEY|SENSEI_JEV_KEY)\s*=\s*(.+?)\s*$", text, re.M)
    return m.group(1).strip().strip("'\"") if m else ""


class Jev:
    def __init__(self, url: str = HOSTED_URL, key: str = "", model: str = "jev-latest",
                 timeout_s: float = 1.5, enabled: bool = False, backend: Optional[str] = None):
        self.model, self.timeout_s, self.enabled = model, timeout_s, enabled
        self.key = key
        self.url, self.headers, self.backend = url, {}, backend or ("hosted" if url == HOSTED_URL else "custom")
        self._set_thresholds(BACKENDS.get(self.backend, BACKENDS["hosted"]))
        self.headers = {"Authorization": f"Bearer {key}"} if (key and url == HOSTED_URL) else {}
        # After a failure, don't try again for a while: with the network unplugged every call
        # would otherwise wait out the whole timeout before Sensei answers without it.
        self.backoff_s = float(os.environ.get("SENSEI_JEV_BACKOFF", 30))
        self.down_until = 0.0

    def _set_thresholds(self, cfg: dict):
        self.skip_below = cfg["skip_below"]
        self.skip_if_answer_below = cfg["skip_if_answer_below"]
        self.no_page_below = cfg["no_page_below"]

    @classmethod
    def from_env(cls) -> Optional["Jev"]:
        """A client for the configured backend (default jevk5), or None if it can't work (hosted
        without a key). Exists even when USE_JEV is off, so it can be switched on without a restart."""
        backend = os.environ.get("SENSEI_JEV_BACKEND", "jevk5")
        if backend not in BACKENDS:
            raise ValueError(f"SENSEI_JEV_BACKEND={backend!r}: expected one of {sorted(BACKENDS)}")
        key = (os.environ.get("SENSEI_JEV_KEY") or os.environ.get("TYPESAFEAI_KEY")
               or _key_from_file(Path(os.environ.get("SENSEI_JEV_KEY_FILE", "~/projects/jev/.env")).expanduser()))
        if backend == "hosted" and not key:
            return None
        url = os.environ.get("SENSEI_JEV_URL") or BACKENDS[backend]["url"]
        return cls(url, key, os.environ.get("SENSEI_JEV_MODEL", "jev-latest"),
                   float(os.environ.get("SENSEI_JEV_TIMEOUT", 1.5)),
                   enabled=os.environ.get("USE_JEV", "0").lower() in ("1", "true", "yes", "on"),
                   backend=backend)

    def use(self, backend: str):
        """Switch backend at runtime; its URL and its floors come with it."""
        if backend not in BACKENDS:
            raise ValueError(f"unknown backend {backend!r}: expected one of {sorted(BACKENDS)}")
        if backend == "hosted" and not self.key:
            raise ValueError("hosted Jev needs a key (SENSEI_JEV_KEY or ~/projects/jev/.env)")
        cfg = BACKENDS[backend]
        self.backend, self.url = backend, cfg["url"]
        self.headers = {"Authorization": f"Bearer {self.key}"} if backend == "hosted" else {}
        self._set_thresholds(cfg)
        self.down_until = 0.0  # a new backend deserves a fresh try

    @property
    def where(self) -> str:
        return "hosted" if self.url == HOSTED_URL else "local"

    def ask(self, state, questions: dict) -> dict:
        """One /v1/systemone call: {question id: answer}. Raises on any failure; callers fall back."""
        if time.monotonic() < self.down_until:
            raise ConnectionError(f"jev failed recently; retrying in {self.down_until - time.monotonic():.0f} s")
        try:
            res = httpx.post(self.url, headers=self.headers, timeout=self.timeout_s,
                             json={"model": self.model, "state": state, "questions": questions})
            res.raise_for_status()
            return res.json()["answers"]
        except Exception:
            self.down_until = time.monotonic() + self.backoff_s
            raise


# --- The "student finished speaking" event ---------------------------------------------------
ABOUT_CRITERIA = {
    "view": "Asks about what is in front of the camera: whether their written work is right, "
            "what Sensei can see, to check a line.",
    "subject": "Asks about the maths or an idea itself, not about the picture: why a method "
               "works, what a word means, the answer to a problem they name.",
    "sensei": "About Sensei or the conversation: asks it to repeat, what it said, whether it can hear.",
    "social": "Small talk or how they feel: thanks, tiredness, 'this is hard'.",
    "steer": "Tells Sensei what to do rather than asking: move on, work on a different problem, "
             "stop, help with this one instead.",
    "unclear": "Cannot tell what they meant: garbled, cut off, or could mean several things.",
}


def utterance_questions(other_problems: list[str]) -> dict:
    questions = {
        "respond": {
            "type": "noul",
            "instructions": "Should Sensei reply out loud to `student_just_said`?",
            "criteria": {
                "true": "It is addressed to Sensei and calls for an answer: a question, a request, "
                        "an instruction, or a reply to a question Sensei just asked "
                        "(see `sensei_asked_a_question`), even a one-word reply like 'no'.",
                "false": "It needs no reply: 'okay', 'thank you', 'mm', muttering while writing, "
                         "reading their own work aloud, a fragment, or talking to someone else.",
            },
        },
        "about": {
            "type": "choice",
            "instructions": "What is `student_just_said` about?",
            "criteria": ABOUT_CRITERIA,
        },
        "followup": {
            "type": "noul",
            "instructions": "Is `student_just_said` a reply to `sensei_last_said`?",
        },
        "needs_page": {
            "type": "noul",
            "instructions": "To answer `student_just_said` well, does Sensei need to look at the "
                            "student's page right now?",
            "criteria": {
                "true": "The answer depends on what is written or shown: checking a line, "
                        "'what do you see', 'is this right', 'this one'.",
                "false": "It can be answered from the conversation and `problem_in_focus` alone: "
                         "a general maths question, small talk, 'what did you say'.",
            },
        },
        "new_problem": {
            "type": "noul",
            "instructions": "Does the student want help with a problem other than `problem_in_focus`?",
            "criteria": {
                "true": "They name, point to, or ask for the answer of a different problem, or say "
                        "they have moved on to a new one.",
                "false": "They are still on `problem_in_focus`, or not talking about a problem at all.",
            },
        },
    }
    if other_problems:
        # Speculative fan-out: only matters if new_problem is yes, but costs nothing extra to ask.
        questions["which_problem"] = {
            "type": "choice",
            "instructions": "Which problem does the student want help with now?",
            "criteria": {**{f"p{i}": p for i, p in enumerate(other_problems)},
                         "none_of_these": "A problem not listed here, or no particular problem."},
        }
    return questions


@dataclass
class UtteranceDecision:
    respond: float                 # P(Sensei should reply)
    about: str
    about_confidence: float
    followup: float
    needs_page: float
    new_problem: float
    which_problem: Optional[str] = None   # the problem text, if one of the listed ones was chosen
    which_confidence: float = 0.0
    latency_ms: float = 0.0
    raw: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"respond": round(self.respond, 2), "about": self.about,
                "about_conf": round(self.about_confidence, 2), "followup": round(self.followup, 2),
                "needs_page": round(self.needs_page, 2), "new_problem": round(self.new_problem, 2),
                "which_problem": self.which_problem, "which_conf": round(self.which_confidence, 2),
                "latency_ms": round(self.latency_ms)}


def decide_utterance(jev: Jev, state: dict, other_problems: list[str]) -> UtteranceDecision:
    t0 = time.perf_counter()
    a = jev.ask(state, utterance_questions(other_problems))
    which, which_conf = None, 0.0
    if "which_problem" in a:
        choice = a["which_problem"]["choice"]
        which_conf = float(a["which_problem"].get("confidence", 0.0))
        if choice.startswith("p") and choice[1:].isdigit() and int(choice[1:]) < len(other_problems):
            which = other_problems[int(choice[1:])]
    return UtteranceDecision(
        respond=float(a["respond"]["noul"]),
        about=a["about"]["choice"],
        about_confidence=float(a["about"].get("confidence", 0.0)),
        followup=float(a["followup"]["noul"]),
        needs_page=float(a["needs_page"]["noul"]),
        new_problem=float(a["new_problem"]["noul"]),
        which_problem=which, which_confidence=which_conf,
        latency_ms=(time.perf_counter() - t0) * 1000, raw=a)
