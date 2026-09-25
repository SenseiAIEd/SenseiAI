# Local Jev for Sensei: JevK5, SemIf and decider

This directory is the launcher and setup notes. It is deployed on the Spark at
`~/projects/jev-local`, where the model weights (`models/`, ~33 GB), the Python environments and
the upstream checkouts (`jevk5/`, `semif/`, `decider/`) live; none of those are in git. To set up
a fresh machine, follow "Setup, for the record" below, then copy `serve.py` next to `models/`.

Two open reproductions of TypeSafe's Jev, served over the same `/v1/systemone` format as hosted
Jev, so Sensei's gateway (`SenseiAI/gateway/jev.py`) can use any of the three. Both use the same
prompt and answer-letter readout (JevK5's runtime is built on SemIf's); they differ in weights
and calibration temperature.

| | weights | T | port | tmux window |
|---|---|---|---|---|
| JevK5 v0.2 (default) | Qwen3.5-4B + LoRA distilled from a 27B thinking teacher (`models/jevk5`, sha256-checked) | 1.22 (its config) | 8095 | `sensei:jevk5` |
| SemIf | frozen Qwen/Qwen3.5-4B @ 851bf6e (`models/semif`) | 1.23 (SemIf's authored-decision T) | 8096 | `sensei:semif` |

| decider-4b v2.1 (Mapika) | Qwen3.5-4B-Base, supervised on a decision mixture + LoRA; its own readout (`decider/`) | per type: noul 1.56, choice 1.11, score 1.29 | 8097 | `sensei:decider` |
| decider-4b v2 (JevBench v1.4.2 #1) | the same, before v2.1's replay fix (`models/decider-4b-v2`) | 1.935 | 8098 | not started |
| JevK8 (OpenJev) | Andy's serve on his Spark (not in this tree) | — | 8099 | external |

JevK8 floors in Sensei copy jevk5 provisionally until `jev_eval.py --backend jevk8 --sweep`.
JevK5 and SemIf take ~11 GB of GPU memory each; decider ~13 GB with the trimmed graphs below
(~24 GB and a ten-minute start with its defaults). How they compare on Sensei's own decisions:
`SenseiAI/docs/jev-report.md` §8. JevK5 is the default.

## Start (after a reboot)

```sh
cd ~/projects/jev-local
tmux new-window -d -t sensei -n jevk5 'cd ~/projects/jev-local && source .venv/bin/activate && python serve.py --model models/jevk5 --name jevk5-4b-v0.2 --port 8095'
tmux new-window -d -t sensei -n semif 'cd ~/projects/jev-local && source .venv/bin/activate && python serve.py --model models/semif --name semif-qwen3.5-4b --port 8096 --temperature 1.23'
# decider has its own server and venv. Graphs only for Sensei's sizes (rows 175-545 tokens, <=6 per request):
tmux new-window -d -t sensei -n decider 'cd ~/projects/jev-local/decider && source .venv/bin/activate && DECIDER_MODEL=../models/decider-4b-v2.1 DECIDER_FP8=0 DECIDER_COMPILE=0 DECIDER_T_BUCKETS=256,384,512,768,1024,1536 DECIDER_B_BUCKETS=1,2,4,8 uvicorn decider.serve:app --host 127.0.0.1 --port 8097'
# decider v2, only if you want to compare (backend "decider-v2"): the same with ../models/decider-4b-v2 and --port 8098
curl -s localhost:8095/health; curl -s localhost:8096/health; curl -s localhost:8097/health
```

If a server is down, the gateway decides the old way and stops trying for 30 s; nothing breaks.

## Setup, for the record

`.venv`: Python 3.12, torch 2.11.0+cu130 (pytorch.org cu130 index, aarch64), transformers 5.17,
flash-linear-attention 0.5.2 (took the six-question call from ~630 ms to ~430 ms), `jevk5`
installed editable from `jevk5/` (v0.2.0) with `--no-deps`. `semif/` is the SemIf repo, kept for
its docs and calibration; it has no server, so `serve.py` serves the SemIf base model through
JevK5's runtime, which reproduces SemIf's prompt exactly.

decider (`decider/`, v1.4.0 at 15ab28e) has its own `.venv`: it pins numpy<2, which would disturb
the JevK5/SemIf environment. FP8 and torch.compile are off: its card says those paths weren't
checked on v2.1, and the GB10 is a Blackwell GPU. `models/decider-4b-v2.1` is the Hub's main,
`models/decider-4b-v2` the `v2` tag. Note from its benchmark page: 8,000 of v2's training rows
were generated from the published names of JevBench's sealed test families.
