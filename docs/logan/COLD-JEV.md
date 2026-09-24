# Lane C — cold stub (logan/jev-gate)

**Status:** COLD. Open only if Lane A is soft or too slow (median >> 12–15 s warm, or catch borderline).

## Intent (no implementation this week unless A fails)

Typed “mistake?” gate in front of the VL assess path — force a cheap binary / structured check before full Socratic generation. Goal: cut latency or stabilize catch without prompt-chasing.

## Explicitly not now

- No code under `gateway/` or `app/`
- No OpenJev force-fit as math OCR (Scout rejected wrong-domain fit)
- No SYSTEM_PROMPT rewrite as the main bet

## Open criteria (from EVAL-CHECKLIST)

| Lane A result | Action |
|---------------|--------|
| Catch OK, median >> 12–15 s live | Open this branch Thu |
| Catch soft OR false alarms | Prefer Lane D first; Jev only if typed gate still helps |
| Freeze bar met | Keep this stub closed |

## When opened

1. Re-read `LOCKED-DECISIONS.md` + Lane A paste numbers.
2. Spec a minimal gate interface (input: OCR/steps; output: mistake? + first-error index).
3. Measure against same `physics/basic/medium` plant — still must catch `bad_1`, leave `good_1`.

Owner: Logan / Log. Docs only until Log says open.
