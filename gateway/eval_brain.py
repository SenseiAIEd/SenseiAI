"""
Score the tutor's vision model on the sample solutions, before trusting it with a student.

  export SENSEI_LLM_URL=http://localhost:8000/v1 SENSEI_LLM_MODEL=<model> SENSEI_LLM_KEY=<key>
  python eval_brain.py                      # all subjects
  python eval_brain.py math/basic/easy      # one folder
  python eval_brain.py --limit 6            # quick check
  python eval_brain.py --models qwen3-vl-30b-a3b-thinking,cosmos-reason2-8b,cosmos-reason2-32b
                                            # race several models (router swaps between them)
  python eval_brain.py --playbook           # with the subject's playbook, as a live session's
                                            # second look has it (the subject is the page's folder)

For every good_N page the model should find no mistake; for every bad_N page it should
flag the one wrong line (listed in datasets/samples/<subject>/README.md), and its question
must not give the answer away. Prints one row per page and a summary, and writes all
replies to eval_results.jsonl for a closer look.
"""
import argparse
import json
import statistics
import time
from pathlib import Path

import cv2

from tutor import MISTAKE_KINDS, PLAYBOOKS, Brain

SAMPLES = Path(__file__).resolve().parent.parent / "datasets/samples"
FIRST_LOOK = "If there is a mistake, use hint level 1. If every step so far is correct and unfinished, " \
             "set \"say\" to null. If they finished correctly, congratulate them and ask them to explain " \
             "why their key step works."


def instructions_for(page: Path, playbook: bool) -> str:
    subject = page.relative_to(SAMPLES).parts[0]
    if not playbook or subject not in PLAYBOOKS:
        return FIRST_LOOK
    return (f"{PLAYBOOKS[subject]} For \"error_kind\" use one of: {', '.join(MISTAKE_KINDS[subject])}. "
            + FIRST_LOOK)


def run_model(brain: Brain, pages: list[Path], out, playbook: bool = False) -> dict:
    # One warm-up call: a router that keeps one model resident takes minutes to swap models,
    # and that must not count as this model's speed.
    print(f"\n=== {brain.model}: loading (warm-up call)...", flush=True)
    t0 = time.time()
    try:
        brain.assess(cv2.imread(str(pages[0])), FIRST_LOOK)
        print(f"    ready after {time.time() - t0:.0f}s")
    except Exception as e:
        print(f"    warm-up failed after {time.time() - t0:.0f}s: {str(e)[:200]}")

    rows, latencies = [], []
    for p in pages:
        expected_error = p.stem.startswith("bad_")
        t0 = time.time()
        try:
            a = brain.assess(cv2.imread(str(p)), instructions_for(p, playbook))
            error = None
        except Exception as e:
            a, error = None, str(e)[:200]
        latency = time.time() - t0
        latencies.append(latency)
        found = a is not None and a.first_error is not None
        ok = a is not None and found == expected_error
        flagged = a.steps[a.first_error - 1] if found and a.mistake else None
        rows.append((p, ok))
        print(f"{'OK ' if ok else 'XX '} {str(p.relative_to(SAMPLES)):48} {latency:5.1f}s  "
              f"{'flags: ' + repr(flagged) if found else ('error: ' + error if error else 'no mistake')}"
              f"{'  | says: ' + a.say if a and a.say else ''}", flush=True)
        out.write(json.dumps({"model": brain.model, "playbook": playbook, "page": str(p.relative_to(SAMPLES)),
                              "expected_error": expected_error, "ok": ok, "latency_s": round(latency, 2),
                              "error": error, "assessment": a.__dict__ if a else None}) + "\n")
        out.flush()

    bads = [ok for p, ok in rows if p.stem.startswith("bad_")]
    goods = [ok for p, ok in rows if p.stem.startswith("good_")]
    return {"model": brain.model + (" +playbook" if playbook else ""), "right": sum(ok for _, ok in rows), "total": len(rows),
            "caught": f"{sum(bads)}/{len(bads)}", "left_alone": f"{sum(goods)}/{len(goods)}",
            "median_s": statistics.median(latencies), "max_s": max(latencies)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folders", nargs="*", help="folders under datasets/samples (default: all)")
    ap.add_argument("--models", default="", help="comma-separated model names to compare (default: SENSEI_LLM_MODEL)")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many pages")
    ap.add_argument("--out", default="eval_results.jsonl")
    ap.add_argument("--playbook", action="store_true", help="also run each model with the subject playbooks")
    args = ap.parse_args()

    brain = Brain.from_env()
    if brain is None:
        raise SystemExit("Set SENSEI_LLM_URL (and SENSEI_LLM_MODEL / SENSEI_LLM_KEY) first.")
    brain.timeout_s = max(brain.timeout_s, 600)  # allow for model swaps

    roots = [SAMPLES / f for f in args.folders] or [SAMPLES]
    pages = sorted(p for r in roots for p in r.rglob("*.png")
                   if p.stem.startswith(("good_", "bad_")) and "ranking" not in p.parts)
    if args.limit:
        pages = pages[:args.limit]

    models = [m.strip() for m in args.models.split(",") if m.strip()] or [brain.model]
    results = []
    with open(args.out, "w") as out:
        for m in models:
            brain.model = m
            results.append(run_model(brain, pages, out))
            if args.playbook:
                results.append(run_model(brain, pages, out, playbook=True))

    print(f"\n{'model':42} {'right':>7} {'mistakes caught':>16} {'good left alone':>16} {'median':>8} {'max':>7}")
    for r in results:
        print(f"{r['model']:42} {r['right']:>3}/{r['total']:<3} {r['caught']:>16} {r['left_alone']:>16} "
              f"{r['median_s']:>7.1f}s {r['max_s']:>6.1f}s")
    print(f"\nCheck flagged lines against datasets/samples/*/README.md; full replies in {args.out}")


if __name__ == "__main__":
    main()
