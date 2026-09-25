"""
Score Jev's "student finished speaking" decisions against labelled real utterances.

    set -a; source sensei.env; set +a
    python jev_eval.py                       # the configured backend (SENSEI_JEV_BACKEND, default jevk5)
    python jev_eval.py --backend hosted      # or semif, jevk5
    python jev_eval.py --backend semif --sweep    # accuracy at every reply floor, to pick one

Reports, per question: accuracy at the thresholds the tutor actually uses, accuracy when Jev
was confident, and for "respond" the same numbers for today's word-list rule, so a threshold
change or a model change is judged against the thing it would replace.
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

from jev import BACKENDS, Jev, decide_utterance
from tutor import Tutor, is_filler


def state_for(case: dict) -> dict:
    last = case["sensei_last_said"]
    return {
        "problem_in_focus": case["problem_in_focus"] or "none yet",
        "page_as_last_read": case["page_as_last_read"],
        "other_problems_on_page": case["other_problems_on_page"],
        "recent_conversation": [f"Sensei: {last}"],
        "sensei_last_said": last,
        "sensei_asked_a_question": last.rstrip().endswith("?"),
        "student_just_said": case["said"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="evals/utterances.jsonl")
    ap.add_argument("--backend", choices=sorted(BACKENDS))
    ap.add_argument("--url", help="a custom /v1/systemone URL (floors of the chosen backend)")
    ap.add_argument("--sweep", action="store_true", help="silences vs extra replies at each reply floor")
    ap.add_argument("--show", action="store_true", help="print every case")
    args = ap.parse_args()

    jev = Jev.from_env()
    if jev is None:
        sys.exit("Jev is not configured: set SENSEI_JEV_BACKEND, or a key for hosted")
    if args.backend:
        jev.use(args.backend)
    if args.url:
        jev.url = args.url
    jev.timeout_s = max(jev.timeout_s, 5.0)

    cases = [json.loads(l) for l in Path(args.cases).read_text().splitlines() if l.strip()]
    rows, latencies = [], []
    for c in cases:
        d = decide_utterance(jev, state_for(c), c["other_problems_on_page"])
        latencies.append(d.latency_ms)
        asked = c["sensei_last_said"].rstrip().endswith("?")
        floor = jev.skip_if_answer_below if asked else jev.skip_below
        rows.append((c, d, {
            "respond": d.respond >= floor,
            "rule": not (is_filler(c["said"]) and not asked),   # today's behaviour
            "about": d.about,
            "needs_page": d.needs_page >= jev.no_page_below,
            "new_problem": d.new_problem >= Tutor.JEV_NEW_PROBLEM_MIN,
        }))
        if args.show:
            L = c["labels"]
            print(f"  {c['said'][:60]:60} respond {d.respond:.2f}/{L['respond']}  about {d.about}/{L['about']}"
                  f"  page {d.needs_page:.2f}/{L['needs_page']}  new {d.new_problem:.2f}/{L['new_problem']}")

    def score(name, pred_key, label_key, confidence=None):
        # Whether the page is needed only matters for things Sensei should answer at all.
        keep = [(c, d, p) for c, d, p in rows if c["labels"][label_key] is not None
                and (label_key != "needs_page" or c["labels"]["respond"] == 1)]
        scored = [(p[pred_key], bool(c["labels"][label_key]) if label_key != "about" else c["labels"][label_key], d)
                  for c, d, p in keep]
        right = [pred == want for pred, want, _ in scored]
        line = f"  {name:22} {sum(right):2}/{len(right)} right"
        if confidence:
            sure = [r for (_, _, d), r in zip(scored, right) if confidence(d)]
            line += f"   when confident: {sum(sure)}/{len(sure)}"
        print(line)
        return [(c["said"], pred, want) for (c, d, p), (pred, want, _) in zip(keep, scored) if pred != want]

    if args.sweep:
        print(f"\n{jev.backend}: reply floor | silent on a real question | unneeded reply")
        for f in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45):
            fq = max(0.05, f - 0.10)
            pts = [(d.respond, c["labels"]["respond"], c["sensei_last_said"].rstrip().endswith("?"))
                   for c, d, _ in rows if c["labels"]["respond"] is not None]
            silent = sum(1 for p, l, q in pts if l == 1 and p < (fq if q else f))
            extra = sum(1 for p, l, q in pts if l == 0 and p >= (fq if q else f))
            print(f"          {f:.2f}      |  {silent:2}                       |  {extra:2}")
    print(f"\n{len(cases)} utterances · {jev.backend} ({jev.where}, reply floor {jev.skip_below}) · latency median "
          f"{statistics.median(latencies):.0f} ms, max {max(latencies):.0f} ms\n")
    wrong = {}
    wrong["respond"] = score("respond (Jev)", "respond", "respond", lambda d: abs(d.respond - 0.5) >= 0.4)
    wrong["rule"] = score("respond (word list)", "rule", "respond")
    wrong["about"] = score("about", "about", "about", lambda d: d.about_confidence >= Tutor.JEV_ABOUT_MIN)
    wrong["needs_page"] = score("needs_page", "needs_page", "needs_page", lambda d: abs(d.needs_page - 0.5) >= 0.4)
    wrong["new_problem"] = score("new_problem", "new_problem", "new_problem", lambda d: abs(d.new_problem - 0.5) >= 0.4)
    print()
    for q, misses in wrong.items():
        for said, pred, want in misses:
            print(f"  miss  {q:12} said={said[:58]!r}  got={pred}  want={want}")


if __name__ == "__main__":
    main()
