# Lane D — cold stub (logan/ft-samples)

**Status:** COLD. Open only if Lane A fails catch rate or shows false alarms on `good_*`.

## Intent (no implementation this week unless A fails)

LoRA / sample fine-tune on `datasets/samples/**` so thinking VL catches planted first-errors without dumping answers. Primary target remains `physics/basic/medium` (`v_y = 20`).

## Explicitly not now

- No training runs
- No prompt-chase as substitute for FT
- No app / gateway product changes for FT plumbing until Log opens the lane

## Open criteria (from EVAL-CHECKLIST)

| Lane A result | Action |
|---------------|--------|
| Catch soft OR false alarms on correct pages | Open this branch Thu |
| Catch OK but slow | Prefer Lane C; FT optional later |
| Freeze bar met | Keep this stub closed |

## When opened

1. Paste Lane A `eval_results.jsonl` summary into this folder’s notes.
2. Inventory `bad_*` / `good_*` coverage (false alarms hurt more than misses).
3. Train only against locked tutoring policy: first-error → quiet → Hint → Check; no answer leak.

Owner: Logan / Log. Docs only until Log says open.
