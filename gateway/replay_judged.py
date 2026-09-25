"""Replay saved judged frames through the current prompt, to see what Sensei would say now.

    python replay_judged.py sessions/<id> judged_021.jpg judged_028.jpg ...

Reads SENSEI_LLM_* from the environment (source sensei.env first). Prints, per frame, the
model's reading and whether the safety nets would have stopped it from speaking.
"""
import sys
import time
from pathlib import Path

import cv2

from tutor import Brain

folder = Path(sys.argv[1])
names = sys.argv[2:] or sorted(p.name for p in folder.glob("judged_*.jpg"))
brain = Brain.from_env()
if brain is None:
    sys.exit("set SENSEI_LLM_URL and SENSEI_LLM_MODEL first")
print(f"model: {brain.model}\n")

for name in names:
    img = cv2.imread(str(folder / name))
    t0 = time.time()
    a = brain.assess(img, "If there is a mistake, use hint level 1.")
    print(f"--- {name}  ({time.time() - t0:.1f}s)")
    print(f"    page={a.page} hand_over_page={a.hand_over_page} rotated={a.rotated}")
    print(f"    problem={a.problem!r} given={a.given!r}")
    print(f"    steps={a.steps}")
    print(f"    first_error={a.first_error} kind={a.error_kind} finished={a.finished}")
    print(f"    say={a.say!r}")
    if a.hand_over_page:
        print("    -> SILENT: still writing")
    elif a.rotated:
        print("    -> asks to turn the page round")
    elif a.first_error:
        print("    -> would need a second look to agree before saying anything")
    print()
