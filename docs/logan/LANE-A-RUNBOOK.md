# Lane A runbook — eval_brain when Spark key lands

Repo path on Spark (or local clone talking to Spark LLM): `gateway/`  
Harness: `gateway/eval_brain.py`  
Fri plant folder: `datasets/samples/physics/basic/medium`

Do **not** invent metrics. Paste only what the harness prints.

## 0) Env (from Andy / sensei.env)

```bash
cd gateway
export SENSEI_LLM_URL=http://localhost:<port>/v1   # model server, not Open WebUI
export SENSEI_LLM_MODEL=qwen3-vl-30b-a3b-thinking
export SENSEI_LLM_KEY=<if required>
# Funnel / phone path separately:
# export SENSEI_KEY=<funnel key>
```

Confirm thinking VL loaded — not instruct GGUF.

## 1) Exact commands (physics-first)

```bash
# smoke first
python eval_brain.py --limit 6

# Fri plant
python eval_brain.py physics/basic/medium

# physics ladder
python eval_brain.py physics/basic/easy physics/basic/medium physics/basic/hard

# optional race after smoke OK
python eval_brain.py --limit 6 --models qwen3-vl-30b-a3b-thinking,cosmos-reason2-8b,cosmos-reason2-32b
```

Warm-up on model swap may take minutes — that is expected; do not count it as median.

## 2) Freeze bar (Fri model lock)

Freeze the model **only if**:

| Must | Meaning |
|------|---------|
| Catch `bad_1` | Flags the line with `v_y = 20` (vector never resolved) |
| Leave `good_1` | No false alarm on the correct control page |
| Median tolerable | Live target ~8–12 s warm; >>12–15 s → consider Lane C |

Broader freeze guidance (from EVAL-CHECKLIST): catch ≥ ~75% of `bad_*` in the folders you ran, false alarms on `good_*` rare/zero, median tolerable → freeze. Soft catch / FA → Lane D. Soft/slow → Lane C. Env fail → fix with Andy; do not prompt-chase.

## 3) Numbers to paste back to Log

From the printed summary (and `eval_results.jsonl`):

- `model` name
- `right/total`
- `mistakes caught` (e.g. `caught: 1/1` on medium)
- `good left alone` (e.g. `left_alone: 1/1`)
- `median_s` / `max_s`
- path to `eval_results.jsonl`
- any `XX` rows: page path + what it flagged / said

**Minimum Fri paste:** for `physics/basic/medium` alone — did it catch `bad_1`? leave `good_1`? median_s?

## 4) Out of scope

- Rewriting `SYSTEM_PROMPT`
- ASR / Ears
- ESP32 / Body firmware
- Opening Jev or FT until the freeze table says so
