# Lane A — eval_brain dry-run checklist (run the second Spark key lands)

Repo: `SenseiAIEd/SenseiAI` → local `/workspace/sensei-desk/repo`  
Harness: `gateway/eval_brain.py`  
Samples: `datasets/samples/**/{good_,bad_}*.png` (~25 good / ~89 bad; false alarms hurt more than misses)

## 0) Access (blockers — get from Andy)
- [ ] SSH or console on Spark (`spark-e257…` funnel already reachable from Log’s machine)
- [ ] `SENSEI_KEY` for funnel console / API
- [ ] Current `SENSEI_LLM_URL`, `SENSEI_LLM_MODEL`, `SENSEI_LLM_KEY` from `sensei.env`
- [ ] Confirm **thinking** VL is loaded (prefer `qwen3-vl-30b-a3b-thinking`), **not** instruct GGUF

## 1) Env on the machine that talks to the LLM
```bash
cd gateway   # or wherever tutor.py + eval_brain.py live on Spark
export SENSEI_LLM_URL=http://localhost:<port>/v1   # model server, not Open WebUI
export SENSEI_LLM_MODEL=qwen3-vl-30b-a3b-thinking
export SENSEI_LLM_KEY=<if required>
# Funnel / phone path separately:
# SENSEI_KEY=<funnel key>
```

## 2) Quick smoke (`--limit 6`) — do this first
```bash
python eval_brain.py --limit 6
# optional race once smoke OK:
python eval_brain.py --limit 6 --models qwen3-vl-30b-a3b-thinking,cosmos-reason2-8b,cosmos-reason2-32b
```
Expect: warm-up may take minutes on model swap; then 6 rows + summary.

## 3) Target folders for Friday relevance
Priority order:
1. `physics/basic/medium` — **Fri plant** (vector `v_y=20`)
2. `physics/basic/easy` + `physics/basic/hard`
3. `math/basic/easy` — 1 good + 3 bad (sign / distribution)
2. `math/basic/medium` + `math/basic/hard` — thin coverage, still useful
3. Full suite only after winner freeze

```bash
python eval_brain.py math/basic/easy
python eval_brain.py math/basic/easy math/basic/medium math/basic/hard
```

## 4) Pass bar (freeze the model only if…)
Repo baseline for thinking VL: **8/8 mistakes, 0 false alarms, ~22 s** median (instruct was 2/8).

Friday *live* wants faster (~8–12 s median after warm-up). Decision rule:

| Result | Action |
|--------|--------|
| Catch ≥ ~75% of `bad_*` AND false alarms on `good_*` = 0 (or rare) AND median tolerable | **Freeze** that model; ship Fri |
| Catch OK but median >> 12–15 s live | Open **Lane C** (Jev typed gate) Thu |
| Catch soft OR false alarms on correct pages | Open **Lane D** (FT on samples) Thu; do **not** prompt-chase |
| Warm-up / URL / auth fails | Fix env with Andy; re-run smoke — don’t change prompts |

## 5) What to paste back to Log / Logan
From the printed summary line:
- model name
- `right/total`
- `mistakes caught` (e.g. `3/3`)
- `good left alone` (e.g. `1/1`)
- `median_s` / `max_s`
- path to `eval_results.jsonl`
- any XX rows (page + what it flagged)

## 6) Explicitly out of scope for Lane A
- Rewriting `SYSTEM_PROMPT`
- ASR / ears (Fri = Hint/Check taps)
- ESP32 / Body hardware
- Fine-tune or Jev until the table above says so
