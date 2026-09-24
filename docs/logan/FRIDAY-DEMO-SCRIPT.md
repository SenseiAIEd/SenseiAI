# Friday noon demo script — Sensei Desk (Eyes + Brain)

**First problem: physics vectors** (not algebra).  
Repo demo sample: `datasets/samples/physics/basic/medium/` — *the* vector-resolution plant.

Goal: one reliable **offline** tutoring loop. Not vision Q&A.

Audience bar: planted mistake → guiding hint → student fix → Sensei recognizes fix.

## Problem (print `ques.png` or hand-copy)

A ball is launched at **20 m/s**, **60°** above the horizontal. How long until it returns to launch height? (g = 9.8 m/s²)

Correct: v_y = 20 sin(60) ≈ 17.32 m/s → t_up ≈ 1.77 s → t_total ≈ 3.54 s

## Planted student work (under camera at Check #1)

```
v_y = 20 m/s
t_up = v_y / g = 20 / 9.8 = 2.04 s
t_total = 2 x 2.04 = 4.08 s
```

**First error: line 1** — used full speed as vertical component (never resolved the vector).  
Lines 2–3 are correct *method* on the wrong number. That is the teaching point.

**Socratic opening (do not state the error):**  
"Your method in lines 2 and 3 is exactly right. Go back to line 1: the ball is moving at 20 m/s, but is all of that motion upward?"

Repo video-script line to aim for: *"You did not fail projectile motion. You failed vector decomposition, two prerequisites back."*

## Fix (student rewrites after Hint)

```
v_y = 20 sin(60) = 17.32 m/s
t_up = 17.32 / 9.8 = 1.77 s
t_total = 2 x 1.77 = 3.54 s
```

## Props
- Phone on **fixed DJI overhead** (or Andy’s head if ready)
- DGX Spark; thinking VL from Lane A freeze
- Print or hand-copy `physics/basic/medium/bad_1.png` energy; keep `good_1.png` for control
- Voice **OFF** — **Hint / Check** taps only
- Backup: 60–90 s clean take Thu night

## Beat sheet (~90 s)

| # | You do | Sensei should | If it fails |
|---|--------|---------------|-------------|
| 1 | Start 5 min | Camera watching | Restart app |
| 2 | Slide planted sheet; wait 2 s | Stable frame | Re-center; kill glare |
| 3 | Tap **Check** | Flags `v_y = 20`; Socratic on components — **does not say 3.54 s** | Tap **Hint** once; else backup video |
| 4 | Rewrite fix (large dark ink) | Sees update | Pause between lines |
| 5 | Tap **Check** | Congrats / asks why sin; no false re-flag | One Hint then end |
| 6 | Stop | — | — |

## Lane A when key lands (physics-first)
```bash
python eval_brain.py --limit 6
python eval_brain.py physics/basic/medium
python eval_brain.py physics/basic/easy physics/basic/medium physics/basic/hard
```
Freeze only if `bad_1` caught (flags line with `v_y = 20`) and `good_1` left alone.

## Hard no’s
- No algebra plant as primary Fri problem
- No voice tutoring mode
- No SYSTEM_PROMPT chase mid-demo
- No ESP32 dependency — DJI fallback OK
