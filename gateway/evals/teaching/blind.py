"""Blind the finished conversations for judging, and tally the judges' verdicts.

  python evals/teaching/blind.py export                       # current + teach -> blind/, judged/
  python evals/teaching/blind.py export current state round2  # chosen variants -> blind-round2/
  python evals/teaching/blind.py report [round2]              # judged[-round2]/ + key -> results
"""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PERSONAS = {json.loads(l)["id"]: json.loads(l) for l in (HERE / "personas.jsonl").read_text().splitlines() if l.strip()}


def export(variants=("current", "teach"), name=""):
    out = HERE / f"blind{'-' + name if name else ''}"
    out.mkdir(exist_ok=True)
    runs = sorted(p for p in (HERE / "runs").glob("*.json") if p.stem.rsplit("-", 1)[-1] in variants)
    rng = random.Random(7)
    codes = rng.sample(range(1000, 9999), len(runs))
    key = {}
    for code, path in zip(codes, runs):
        run = json.loads(path.read_text())
        pid, variant = path.stem.rsplit("-", 1)
        p = PERSONAS[pid]
        lines = [f"PROBLEM: {p['problem']}", f"THE REAL MISTAKE ON THE PAGE: {p['mistake']}",
                 f"STUDENT PERSONA: {p['persona']} - {p['misconception']}", "", "TRANSCRIPT:"]
        for e in run["transcript"]:
            if e["who"] == "student":
                lines.append(f"STUDENT: {e['text']}")
            elif e.get("why") not in ("ack", "busy"):
                lines.append(f"SENSEI: {e['text']}")
        (out / f"{code}.txt").write_text("\n".join(lines) + "\n")
        key[str(code)] = {"persona": pid, "variant": variant}
    (out / "key.json").write_text(json.dumps(key, indent=1))
    print(f"exported {len(runs)} conversations to {out}")


def report(name=""):
    sfx = f"-{name}" if name else ""
    key = json.loads((HERE / f"blind{sfx}" / "key.json").read_text())
    by = defaultdict(list)
    for code, meta in key.items():
        f = HERE / f"judged{sfx}" / f"{code}.json"
        if f.exists():
            by[meta["variant"]].append({**json.loads(f.read_text()), **meta})
    num = ["diagnosis", "teaching_when_stuck", "correctness", "responsiveness", "voice_fit", "overall"]
    print(f"{'':22}" + "".join(f"{v:>12}" for v in sorted(by)))
    for k in num:
        print(f"{k:22}" + "".join(f"{sum(r[k] for r in by[v]) / len(by[v]):>12.2f}" for v in sorted(by)))
    for k in ("student_learned", "answer_leak"):
        print(f"{k:22}" + "".join(f"{sum(bool(r[k]) for r in by[v]):>9}/{len(by[v]):<2}" for v in sorted(by)))
    for v in sorted(by):
        turns = [r["turns_to_fix"] for r in by[v] if r.get("turns_to_fix")]
        tags = defaultdict(int)
        for r in by[v]:
            for t in r.get("failure_tags", []):
                tags[t] += 1
        print(f"\n{v}: median turns to fix {sorted(turns)[len(turns)//2] if turns else '-'}; "
              f"failure tags: {dict(sorted(tags.items(), key=lambda x: -x[1]))}")
    per = defaultdict(dict)
    for v in by:
        for r in by[v]:
            per[r["persona"]][v] = r["overall"]
    print(f"\nper student ({' -> '.join(sorted(by))}):")
    for pid in sorted(per):
        print(f"  {pid:12} " + " -> ".join(str(per[pid].get(v, '-')) for v in sorted(by)))


if __name__ == "__main__":
    if sys.argv[1] == "export":
        export(tuple(sys.argv[2:-1]) or ("current", "teach"), sys.argv[-1] if len(sys.argv) > 3 else "")
    else:
        report(sys.argv[2] if len(sys.argv) > 2 else "")
