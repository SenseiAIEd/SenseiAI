"""
Talk to the real Sensei tutor one student turn at a time, for synthetic-student evaluations.

The tutor side is the live code: tutor.Tutor with the configured vision model, Jev and the real
prompts, looking at a real page image. Only the student is simulated (by whoever calls this).
State is saved between calls, so a simulated student can take its time over each reply.

  python evals/teaching/sensei_turn.py start --id s01-current --page ../datasets/samples/math/basic/easy/bad_1.png --variant current
  python evals/teaching/sensei_turn.py say   --id s01-current --text "I don't understand what I did wrong"
  python evals/teaching/sensei_turn.py show  --id s01-current

Opening: the greeting, then the page is read twice (as in a live session) and the first hint is
spoken. Each `say` is heard exactly like speech in voice mode (Jev decides whether to answer).
Run with sensei.env loaded: set -a; source sensei.env; set +a.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))  # gateway/

from jev import Jev  # noqa: E402
from tutor import Brain, Tutor  # noqa: E402

RUNS = HERE / "runs"
KEEP = ["conversation", "problem", "subject", "topic", "mistake", "candidate_mistake", "hint_level",
        "hints_on_mistake", "hints_given", "mistakes_found", "mistakes_fixed", "last_said", "last_why",
        "last_spoke_at", "last_seen", "other_problems", "steps_confirmed", "_finished_problem", "let_go",
        "started_at", "ends_at", "phase", "_rechecks", "last_focus_change", "last_activity", "_heard_count",
        "answer_key"]
TURN_S = 15.0  # simulated seconds between student turns


def load(run_id: str) -> dict:
    return json.loads((RUNS / f"{run_id}.json").read_text())


def save(state: dict):
    (RUNS / f"{state['id']}.json").write_text(json.dumps(state, indent=1, default=list))


def make_tutor(state: dict, said: list) -> Tutor:
    async def speak(text, why):
        said.append({"why": why, "text": text})

    async def notify(_):
        pass

    brain = Brain.from_env()
    t = Tutor(brain, speak, notify, minutes=15, clock=lambda: state["now"],
              chat_brain=Brain.chat_from_env(), decider=Jev.from_env(),
              teach=state["variant"] == "teach", log_event=lambda e, **k: state["events"].append({"event": e, **k}))
    for k, v in state.get("tutor", {}).items():
        if k == "mistake" or k == "candidate_mistake":
            v = tuple(v) if v else None
        elif k == "let_go":
            v = {tuple(x) for x in v}
        setattr(t, k, v)
    return t


def snapshot(t: Tutor) -> dict:
    out = {}
    for k in KEEP:
        v = getattr(t, k)
        out[k] = [list(x) for x in v] if k == "let_go" else v
    return out


def record(state: dict, who: str, entries: list):
    for e in entries:
        state["transcript"].append({"who": who, **e})


async def start(args):
    RUNS.mkdir(exist_ok=True)
    state = {"id": args.id, "page": str(Path(args.page).resolve()), "variant": args.variant, "now": 1000.0,
             "transcript": [], "events": []}
    said: list = []
    t = make_tutor(state, said)
    img = cv2.imread(state["page"])
    await t.start()
    for _ in range(2):  # the page settles; a mistake must be seen on two looks before a hint
        state["now"] += 4
        t.watcher.mark_judged()
        await t._judge(img, request=None)
    await asyncio.gather(*list(t._background))  # the answer key is made in the background
    record(state, "sensei", said)
    state["tutor"] = snapshot(t)
    save(state)
    for s in said:
        print(f"SENSEI ({s['why']}): {s['text']}")


async def say(args):
    state = load(args.id)
    state["now"] += TURN_S
    said: list = []
    t = make_tutor(state, said)
    state["transcript"].append({"who": "student", "text": args.text})
    await t.hear(args.text, cv2.imread(state["page"]))
    await asyncio.gather(*list(t._background))
    record(state, "sensei", said)
    state["tutor"] = snapshot(t)
    save(state)
    spoken = [s for s in said if s["why"] not in ("ack", "busy")]
    if not spoken:
        print("SENSEI: (no reply)")
    for s in spoken:
        print(f"SENSEI ({s['why']}): {s['text']}")


def show(args):
    for e in load(args.id)["transcript"]:
        tag = "STUDENT" if e["who"] == "student" else f"SENSEI ({e.get('why')})"
        print(f"{tag}: {e['text']}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("start")
    a.add_argument("--id", required=True)
    a.add_argument("--page", required=True)
    a.add_argument("--variant", choices=["current", "teach", "state"], default="current")
    b = sub.add_parser("say")
    b.add_argument("--id", required=True)
    b.add_argument("--text", required=True)
    c = sub.add_parser("show")
    c.add_argument("--id", required=True)
    args = ap.parse_args()
    if args.cmd == "show":
        show(args)
    else:
        asyncio.run(start(args) if args.cmd == "start" else say(args))


if __name__ == "__main__":
    main()
