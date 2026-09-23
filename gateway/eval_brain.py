"""
Score the tutor's vision model on the sample solutions, before trusting it with a student.

  export SENSEI_LLM_URL=http://localhost:8000/v1 SENSEI_LLM_MODEL=<model> SENSEI_LLM_KEY=<key>
  python eval_brain.py                      # all subjects
  python eval_brain.py math/basic/easy      # one folder
  python eval_brain.py --limit 6            # quick check

For every good_N page the model should find no mistake; for every bad_N page it should
flag the one wrong line (listed in datasets/samples/<subject>/README.md), and its question
must not give the answer away. Prints one row per page and a summary, and writes all
replies to eval_results.jsonl for a closer look.
"""
import argparse
import json
import time
from pathlib import Path

import cv2

from tutor import Brain

SAMPLES = Path(__file__).resolve().parent.parent / "datasets/samples"
FIRST_LOOK = "If there is a mistake, use hint level 1. If every step so far is correct and unfinished, " \
             "set \"say\" to null. If they finished correctly, congratulate them and ask them to explain " \
             "why their key step works."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="*", help="folders under datasets/samples (default: all)")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many pages")
    ap.add_argument("--out", default="eval_results.jsonl")
    args = ap.parse_args()

    brain = Brain.from_env()
    if brain is None:
        raise SystemExit("Set SENSEI_LLM_URL (and SENSEI_LLM_MODEL / SENSEI_LLM_KEY) first.")

    roots = [SAMPLES / f for f in args.folders] or [SAMPLES]
    pages = sorted(p for r in roots for p in r.rglob("*.png")
                   if p.stem.startswith(("good_", "bad_")) and "ranking" not in p.parts)
    if args.limit:
        pages = pages[:args.limit]

    rows, latencies = [], []
    with open(args.out, "w") as out:
        for p in pages:
            expected_error = p.stem.startswith("bad_")
            t0 = time.time()
            try:
                a = brain.assess(cv2.imread(str(p)), FIRST_LOOK)
                error = None
            except Exception as e:
                a, error = None, str(e)[:200]
            latency = time.time() - t0
            latencies.append(latency)
            found = a is not None and a.first_error is not None
            ok = a is not None and found == expected_error
            flagged = a.steps[a.first_error - 1] if found and a.mistake else None
            rows.append(ok)
            print(f"{'OK ' if ok else 'XX '} {str(p.relative_to(SAMPLES)):48} {latency:5.1f}s  "
                  f"{'flags: ' + repr(flagged) if found else ('error: ' + error if error else 'no mistake')}"
                  f"{'  | says: ' + a.say if a and a.say else ''}")
            out.write(json.dumps({"page": str(p.relative_to(SAMPLES)), "expected_error": expected_error,
                                  "ok": ok, "latency_s": round(latency, 2), "error": error,
                                  "assessment": a.__dict__ if a else None}) + "\n")

    goods = [ok for ok, p in zip(rows, pages) if p.stem.startswith("good_")]
    bads = [ok for ok, p in zip(rows, pages) if p.stem.startswith("bad_")]
    lat = sorted(latencies)
    print(f"\nmodel {brain.model}: {sum(rows)}/{len(rows)} right | "
          f"mistakes caught {sum(bads)}/{len(bads)} | correct work left alone {sum(goods)}/{len(goods)} | "
          f"latency median {lat[len(lat) // 2]:.1f}s, max {lat[-1]:.1f}s")
    print(f"Check the flagged lines against datasets/samples/*/README.md; full replies in {args.out}")


if __name__ == "__main__":
    main()
