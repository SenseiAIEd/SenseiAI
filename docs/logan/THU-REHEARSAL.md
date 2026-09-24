# Thu rehearsal scorecard — Sensei planted-mistake demo

Demo Coach owns this. Fri noon live. Cut to backup if **two fails in a row**.

**DEMO PLANT LOCKED (Log, Wed Sep 23):** primary = physics vectors  
`datasets/samples/physics/basic/medium` — algebra demoted. Score against this plant only.

**UX LOCK (Logan via Log, Wed Sep 23):**
- Long-term: arm auto-focus on paper → phone screen + short audio (TTS out).
- Fri fallback: if arm not solid by **Thu noon** → fixed DJI.
- TTS **after** Hint/Check OK if non-leaking (does not spoil answer). Voice-**IN** still OFF.
- Tutoring unchanged: quiet after first-error detect; on-demand Socratic Hint; Check; no spoon-feed.
- Polish only: note “screen+TTS played?” — **not a fail** if missing.

## Rules (enforce every run)
- Voice-**IN** OFF. Hint / Check taps only. TTS out after tap OK if non-leaking.
- Quiet after first-error detect; Hint on demand; Check; no spoon-feed / answer-dump.
- Same planted sheet every clean run (physics bad_1 below).
- Camera: arm if solid Thu noon, else fixed DJI. Same angle as Fri.
- Do not chase SYSTEM_PROMPT mid-rehearsal.
- Quiet until Logan asks to run the scorecard.

## Plant (live paper — write large, dark ink)
Problem:
```
A ball is launched at 20 m/s, 60 degrees above the horizontal.
How long until it returns to launch height? (g = 9.8 m/s^2)
```

Planted bad (bad_1):
```
v_y = 20 m/s
t_up = v_y / g = 20 / 9.8 = 2.04 s
t_total = 2 x 2.04 = 4.08 s
```

Planted error: line 1 uses full speed instead of `20 sin 60`.  
Correct fix Sensei should recognize (~3.54 s):
```
v_y = 20 sin60 = 17.32 m/s
t_up = 17.32 / 9.8 = 1.77 s
t_total = 2 x 1.77 = 3.54 s
```

Control sheet: `good_1` (same problem, correct components) — zero false alarms.

Sample refs: `repo/datasets/samples/physics/basic/medium/{ques,bad_1,good_1}.png`

## Bar before Fri noon
| Gate | Target | Status |
|------|--------|--------|
| Clean runs (beats 1–5) | **5** | 0/5 |
| Control good sheet | 1 run, zero false alarm on good_1 | pending |
| Backup video | 60–90 s take after run 3 | pending |
| Assess latency (warm) | median ~8–12 s | not measured |
| Arm vs DJI | Arm solid by Thu noon, else DJI | pending Thu noon |
| Cut rule | 2 fails in a row → backup | armed |

## Run log

Mark Y/N/partial. Latency = seconds to assess after Check (warm).  
**Screen+TTS** = polish only (Y/N/partial); missing does **not** fail the run.

| # | Time | Catch plant (line1 v_y=20) | Hint useful (components, not dump) | Recognized fix (~3.54 s) | Latency (s) | OCR slips | Screen+TTS? (polish) | Clean? | Notes |
|---|------|----------------------------|-------------------------------------|--------------------------|-------------|-----------|----------------------|--------|-------|
| 1 | | | | | | | | | |
| 2 | | | | | | | | | |
| 3 | | | | | | | | | ← film backup after this if clean enough |
| 4 | | | | | | | | | |
| 5 | | | | | | | | | |
| C | | — | — | — | | false alarm? | | | good_1 control |

### Fail streak
Consecutive fails: **0** (cut at 2)

## Track definitions
- **Catch plant** — flags line 1 `v_y = 20` (missing sin 60); stays quiet after detect until Hint; does not spoil `t ≈ 3.54 s`
- **Hint useful** — Socratic on components / vertical projection; not answer-dump / spoon-feed
- **Recognized fix** — after rewrite to ~3.54 s path, congrats / no false re-flag
- **Latency feel** — snappy / ok / drag (>12–15 s warm is drag)
- **OCR slips** — misread numbers/symbols, glare, re-center needed
- **Screen+TTS (polish)** — phone screen + short non-leaking TTS after Hint/Check; not required for Clean

## Props checklist (Thu night)
- [ ] Planted physics sheet written large, dark ink (bad_1)
- [ ] good_1 control sheet ready
- [ ] Camera: arm solid **or** fixed DJI locked (decide by Thu noon)
- [ ] DGX Spark + thinking VL loaded (Lane A freeze)
- [ ] Voice-IN OFF in app; TTS out OK if non-leaking
- [ ] Backup take saved on phone + copied here if possible

## Owners
- Scorecard / cut call: Demo Coach
- Live run: Logan
- Camera mount: Andy (or Logan locks DJI)
- Decision ping: Log (Lane C/D only if A numbers force it)

## Fri go / no-go
Go only if: 5 clean + control OK + backup filmed. Else open with backup video, then one live attempt if time.  
Missing screen+TTS does **not** block go.

Note: `plan/FRIDAY-DEMO-SCRIPT.md` still shows algebra plant — **demoted**. Scorecard + live paper follow this physics lock until Log changes it.

## Stage pack (Demo Coach → Log)
- `SCORECARD.md` — pass/fail rows for 5 + control
- `JUDGE-LINES.md` — 90s one-liner + 3 Check-fail backups
- `PROP-CHECKLIST.md`
- `CALL-SCRIPT.md` — Logan reads before each run
