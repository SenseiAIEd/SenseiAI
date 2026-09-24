# Sensei Scout — plant brief (Fri vectors)

Plant: `physics/basic/medium` — `bad_1` writes `v_y=20` (never resolved); control `good_1`.  
Forbidden Hint leaks: `17.32`, `3.54`, full solve path.  
Loop: first wrong step → quiet → one Socratic Hint → rewrite → Check. ASR OFF.

## 1) OCR + grading pitfalls

- **v vs v_y vs v_x** — tiny subscript y often dropped or read as x / blank.
- **sin vs cos** — VLMs “fix” trig names to match physics; Eyes must transcribe ink, Brain grades transcript.
- **20 vs 2.0 / 70**; decimal on 17.32 / 3.54 disappears under glare → 1732 / 354.
- **Units** m/s vs s — Check must know which slot is checked.
- Capture breaks: faint ink, overhead skew, shadow, digits too small.
- **Critical:** do not let VL repair `v_y=20` into the right component (hides the plant).

Sources: FADE (faint numeric OCR) https://aclanthology.org/2026.alvr-main.23/ · PINK over-correction https://arxiv.org/html/2604.22774 · Daheim verify-then-generate https://arxiv.org/abs/2407.09136

## 2) Hint bank (never leak 17.32 or 3.54)

1. "Which component are you writing — the full speed, or only the vertical part?"
2. "Look at the triangle: which side matches the direction you labeled v_y?"
3. "Did you use the angle with the horizontal, or with the vertical?"
4. "Before the number, what trig function belongs with that component?"
5. "Is 20 the magnitude you should plug into the y-equation, or something else?"

TTS: one short sentence. No sin(60), no answer values.

## 3) Soft Lane A gate (no FT, no new deps claimed)

A. Eyes: faithful read of first equation only (copy ink, do not fix math).  
B. Typed gate: normalize → flag if first claim ~ `v_y\s*=\s*20` (and not rewritten).  
C. On flag: quiet until Hint → emit template 1–5 (not free lecture).  
D. Check = binary “first wrong step gone?” vs plant / good_1 first step — not Photomath.

Prefer B+C over free VL tutoring every frame if Lane A soft.

## 4) Check binary accept

(See §4b for repo contract.)

- Fail `bad_1` (`v_y=20` still there); pass `good_1`.
- Hint never speaks 17.32 or 3.54.
- Score student ink, not VL-repaired text.

## Risks

Quiet looks broken · OCR misses the 20 · Hint leaks · over-correcting VL hides plant · Check grades wrong line.

## 4b) Amendment — SenseiAI Check contract (from repo)

Source: https://github.com/SenseiAIEd/SenseiAI — `gateway/tutor.py` + `gateway/test_tutor.py` (not the unrelated nihatkutukoglu/SenseiAI CRM).

Steal for Check binary (`bad_1` fail / `good_1` pass):
- Dataset: `datasets/samples/.../good_1.png` vs `bad_1.png`
- VL JSON: `steps[]`, `first_error` (1-based or null), `error_kind`, `say`
- **Check PASS** iff `first_error` is None; **FAIL** until rewrite removes that mistake identity
- Quiet while on-track; re-judge only on new writing (PageWatcher still + change frac)
- Hints escalate `hint_1`→`hint_2`→`hint_3` on SAME mistake; cooldown; acknowledge fixed then finished
- SYSTEM rule: never give answer/corrected line; if unsure treat step as correct (prefer miss over false alarm)
- `request("check", PAGE)` forces a look vs background quiet

Closest alt if org goes private: eth-lre/verify-then-generate.

OCR add-on: OmniHandwritingOCR multi-line drop + hallucinated ink fixes — https://arxiv.org/html/2608.18586v1
