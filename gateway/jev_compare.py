"""
Compare Jev backends side by side on Sensei's own decisions (evals/utterances.jsonl).

    set -a; source sensei.env; set +a
    python jev_compare.py                          # every backend in jev.BACKENDS that answers
    python jev_compare.py jevk5 hosted             # just these

Each backend is scored at its own floors (jev.BACKENDS) with the tutor's actual policy, including
"being redirected is never ignored", and asked everything twice to see whether it is deterministic.
Columns: reply = right on "should Sensei reply"; silent = real questions it would ignore; extra =
replies nobody needed; new = "moved to another problem"; about = what it was about; bad-drop =
questions answered without the image that needed it; p50/p95 = latency of one six-question call.
"""
import json
import sys
from pathlib import Path

from jev import BACKENDS, Jev, decide_utterance
from jev_eval import state_for
from tutor import Tutor

IDEA = ("subject", "social", "sensei")


def score(j: Jev, cases: list) -> dict:
    runs = [[decide_utterance(j, state_for(c), c["other_problems_on_page"]) for c in cases] for _ in range(2)]
    deterministic = all(abs(a.respond - b.respond) < 1e-3 and a.about == b.about for a, b in zip(*runs))
    lat = sorted(d.latency_ms for r in runs for d in r)
    r = dict(reply=0, n_reply=0, silent=0, extra=0, new=0, n_new=0, about=0, n_about=0, bad_drop=0)
    for c, d in zip(cases, runs[0]):
        L, asked = c["labels"], c["sensei_last_said"].rstrip().endswith("?")
        steering = (d.new_problem >= Tutor.JEV_NEW_PROBLEM_MIN
                    or (d.about == "steer" and d.about_confidence >= Tutor.JEV_ABOUT_MIN))
        reply = d.respond >= (j.skip_if_answer_below if asked else j.skip_below) or steering
        if L["respond"] is not None:
            r["n_reply"] += 1
            r["reply"] += reply == bool(L["respond"])
            r["silent"] += L["respond"] == 1 and not reply
            r["extra"] += L["respond"] == 0 and reply
        if L["new_problem"] is not None:
            r["n_new"] += 1
            r["new"] += (d.new_problem >= Tutor.JEV_NEW_PROBLEM_MIN) == bool(L["new_problem"])
        if L["about"] is not None:
            r["n_about"] += 1
            r["about"] += d.about == L["about"]
        if L["respond"] == 1 and L["needs_page"] == 1 and d.needs_page < j.no_page_below and d.about in IDEA:
            r["bad_drop"] += 1
    r.update(p50=round(lat[len(lat) // 2]), p95=round(lat[int(len(lat) * 0.95)]), deterministic=deterministic)
    return r


def main():
    cases = [json.loads(l) for l in Path("evals/utterances.jsonl").read_text().splitlines() if l.strip()]
    base = Jev.from_env()
    names = sys.argv[1:] or list(BACKENDS)
    print(f"{'backend':11} {'reply':>6} {'silent':>6} {'extra':>5} {'new':>6} {'about':>6} {'bad-drop':>8} {'p50':>5} {'p95':>5}  deterministic")
    for name in names:
        j = Jev(key=base.key if base else "", timeout_s=30)
        try:
            j.use(name)
            r = score(j, cases)
        except Exception as e:
            print(f"{name:11} unavailable ({type(e).__name__}: {str(e)[:60]})")
            continue
        print(f"{name:11} {r['reply']:>3}/{r['n_reply']:<2} {r['silent']:>6} {r['extra']:>5} {r['new']:>3}/{r['n_new']:<2} "
              f"{r['about']:>3}/{r['n_about']:<2} {r['bad_drop']:>8} {r['p50']:>5} {r['p95']:>5}  {r['deterministic']}")


if __name__ == "__main__":
    main()
