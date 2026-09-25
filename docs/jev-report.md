# Jev and System One models — a report

Written 2026-09-24, for the Sensei project. Sources: the TypeSafe AI manifesto (15 Sep 2026),
the Code Report video (21 Sep 2026), a model-router walkthrough video, and the READMEs of the
two open reproductions. Everything marked **measured** was run on our own Spark; everything
else is someone's claim, and labelled as such.

---

## 1. What it is

Jev is the first public "System One Model" from TypeSafe AI, founded by Diogo Almeida, who
worked on the instruction-following research behind ChatGPT at OpenAI. The name comes from
Kahneman: System 1 is fast and intuitive, System 2 is slow and deliberate. The pitch is that
almost all of the AI in a working application is System 1 — routing, classifying, scoring,
gating — and that we have been paying System 2 prices for it.

The radical move: **it does not generate text at all.**

You send unstructured state plus one or more *typed questions*. You get back typed answers with
probabilities. Three question types:

| type | shape | example |
|---|---|---|
| `bool` | yes / no + P(yes) | "does this contain personal data?" |
| `choice` | one of N (up to 255) + a probability per option | "is this chitchat, code, or an image request?" |
| `score` | one of up to 10 ordered levels | "how much model capability does this need?" |

All questions in a request are answered **in parallel, in one pass**, so asking six costs about
what asking one costs. Because the possible outputs are declared in advance, a type error is
structurally impossible — there is no JSON to parse and nothing to validate.

**Claimed** figures: 70–500 ms end-to-end against 3–329 s for frontier models; input at
$0.042/MTok with output tokens free; headline numbers of 193.6× faster and 444.6× cheaper.
Trained with something they call Reinforcement Learning for Calibrated Decisions (RLCD), whose
goal is that "60% confident" actually means right 60% of the time.

## 2. Why calibration is the actual product

It is tempting to read Jev as "a classifier with a nice API". The interface is the easy part —
two people reproduced it in a weekend. The claim that matters is **calibration**: that the
confidence number is honest enough to branch on.

This is the difference between a model that is right 95% of the time and a model you can
automate with. If a model is right 95% of the time but cannot tell you which 5%, you still need
a human on every call. If it says 0.99 and is right 99% of the time at that confidence, you can
write `if p > 0.9: act else: ask`. That is the whole thesis, and it is why "System One" is a
category claim rather than a product feature.

## 3. Measured: what this looks like on our hardware

**The plumbing is already free.** vLLM on the Spark supports `guided_choice` (guaranteed to
return one of the declared options) together with `logprobs`, on the model we already have
loaded. One forward pass, one token:

```
guided_choice        156 ms
structured_outputs    84 ms
plain + logprobs      81 ms
```

So Jev-shaped calls — typed, constrained, probabilistic, ~80 ms — need no new service, no new
memory, and no vendor. That is the good news.

**The judgement is not free.** I built a 12-case set from our own sessions: real things the
student said on 23–24 Sep, labelled with the answer we know is correct, asking the one question
that broke the 24 Sep session — *is the student pointing us at a different problem?*

| prompt style | correct | when it claimed ≥80% confidence |
|---|---|---|
| plain question | 7 / 12 | **4 / 9 right** |
| with per-option criteria (Jev-style) | 4 / 12 | 3 / 4 right |

A human gets 12/12. Two findings:

1. **It is worse than useless at high confidence.** In the first run, filtering to the cases it
   was ≥80% sure about made accuracy *fall* — 44% against its own 58% overall. Confidence was
   anti-correlated with being right. You cannot build `if p > 0.8` on that.
2. **It is wildly prompt-sensitive.** Rewriting the prompt moved *"What do you see now?"* from
   p=0.93 to p=0.00, and made overall accuracy worse. "I'm just giving a new problem" —
   unambiguous to any human — scored p(new)=0.05 and 0.13 in the two runs.

The lesson is not "this approach doesn't work". It is that **raw option probabilities off an
instruction-tuned chat model are not calibrated decisions**, and the gap between the two is
exactly what TypeSafe is selling. Our own 30B VLM, asked the same question inside a full
structured call where it can use more context, got all four steer utterances right — but took
1.5–2.5 s instead of 80 ms.

## 4. The open reproductions

Both of the projects you pointed at do more than read logits — importantly, **both calibrate**.

**[SemIf-OpenJev](https://github.com/TheoLeeCJ/SemIf-OpenJev)** — reads declared option logits
from a frozen model in one forward pass. Baseline Qwen3.5-4B, plus a 27B EXL3 bridge and 2B and
0.6B variants; runs on a 3090, and has a **CPU-only llama.cpp backend**. Applies per-workload
temperature scaling — reported ECE 0.068 → 0.038 at T=1.23. Claims balanced accuracy 0.813 on
144 authored decisions and 0.845 modal agreement on a TypeSafe subset, against Jev's 0.883.
Direct logits: 1.023 s for 21 binary decisions, versus 5.3 s for autoregressive JSON.

**[JevK5](https://github.com/allebee/jevk5)** — Qwen3.5-4B base with a distilled LoRA merged in,
trained from a Qwen3.6-27B thinking teacher. Supervised on 3,272 synthetic teacher questions
plus 3,272 human-labelled items, with a single fitted temperature on held-out data (the README
says 1.532; the shipped v0.2 `jevk5_config.json` says **1.22**, which is what actually runs).
Serves the TypeSafe-style `/v1/systemone` request shape, so it is drop-in against the hosted
API. ~9 GB in bf16, GGUF variants for CPU and Mac. Claims 2nd of 76 systems on JevBench v1.4 and
1st among open entrants; p50 13.5 ms on an H100.

Taken at face value, open reproductions land within a few points of Jev on Jev-shaped tasks,
run locally, and cost nothing per call. For a project whose mission is *offline*, that matters
more than the last few points of accuracy.

## 5. What to be sceptical about

- **The benchmarks measure agreement, not correctness.** TypeSafe's workflow evals use the
  average of GPT-6 Astra and Fable 5.1 as the reference answer. That measures how well Jev
  imitates two frontier models, which says little about whether it is *right* — and nothing at
  all about a domain like tutoring, where the correct answer is pedagogical rather than
  consensus-of-LLMs. To their credit they say this themselves, in a published nuance section.
- **No paper, undisclosed architecture.** "Possibly coming in the future, maybe."
- **Prior art.** Zero-shot classifiers built by reading option probabilities are over a decade
  old, and the launch credits none of them. At least one researcher claims a year-old paper
  describes the same method. The speed and the calibration training may well be novel; the
  shape is not.
- **Non-deterministic.** Same state, same question, possibly a different answer. Any test suite
  has to assert on thresholds and distributions, not on exact values.
- **Their own evals ran from their laptops**, and the latency figures are single-call.
- **The self-defeating privacy case.** The router demo asks Jev "does this contain private
  data?" — having already sent the data to Jev to ask. Any privacy or safety gate must run
  locally or it is theatre. This applies directly to us: children's voices and schoolwork.

## 6. What this means for Sensei

Short version: **the fast typed decision is real and useful, but it is not free judgement, and
it must be verified on our own data before we branch on it.**

- The interface costs us nothing to adopt — we already have 80 ms constrained choice.
- The quality has to be earned, either by a purpose-trained local model (JevK5 / SemIf) or by
  calibrating on our own labelled decisions. We have ~30 sessions of logs to build that set.
- Hosted Jev is the wrong default for the product: the Desk is meant to run offline, in
  Bangladesh, on children's work. Local is the requirement, not the fallback.
- Memory is the practical constraint, not money. The Spark sits at 109/121 GB with the VLM
  loaded, and the router stops backends to free memory. A 4B in bf16 (~9 GB) would be fighting
  for the last of it; the quantized or CPU backends are the safer shape.

The engineering plan is in [jev-in-sensei.md](jev-in-sensei.md).

## 7. Update, 25 Sep: both open models running on the Spark

JevK5 and SemIf now run locally (`~/projects/jev-local`), each ~14 GB of GPU memory, and are
scored on the same 48 real utterances as hosted Jev (`gateway/jev_eval.py`). Each at its own
reply floor:

| | should Sensei reply | moved to another problem | median latency |
|---|---|---|---|
| hosted Jev | 44/47 | 43/44 | ~270 ms |
| **JevK5 (default)** | **42/47** | **43/44** | **~370 ms, offline** |
| SemIf | 39/47 (never silent on a real question) | 43/44 | ~380 ms |
| today's word list | 32/47 | — | — |

Two things learned:

- **Floors belong to the backend.** JevK5 scores some real questions at 0.12–0.35; under the 0.40
  floor tuned on hosted Jev, Sensei would have gone silent on five of them. JevK5 runs at 0.25.
- **Local is deterministic.** Same input, same numbers, every time; hosted Jev varies slightly
  run to run. That makes local failures reproducible, which matters for fixing them.

The local models are weaker on "what is it about" (25–26/37 against 30–31) and on whether the
page is needed, so they only drop the image when very sure (under 0.15).

## 8. Update, 25 Sep: decider-4b, the JevBench v1.4.2 leader, on Sensei's decisions

[decider-4b](https://github.com/Mapika/decider) (Mapika, Apache-2.0) tops JevBench v1.4.2 at 64.1
against Jev 1.13's 63.3 — 0.8 points, while behind Jev on calibration (75.0 vs 76.3) and on the hard
tier (67% vs 74%). Two things to know about that lead: its author discloses that 8,000 of v2's
training rows were generated from the published names of the benchmark's ten sealed families, and
the current release is v2.1, not the v2 that was ranked. We ran both on the Spark alongside JevK5 and
SemIf, on the same 48 utterances, each at its own best floor and with Sensei's actual policy
(including "being redirected is never ignored"):

| model | should reply | silent on a real question | unneeded replies | moved to a new problem | what it's about | median / p95 |
|---|---|---|---|---|---|---|
| **JevK5 (default)** | **43/47** | 1 | **3** | 43/44 | 25/37 | **375 / 474 ms** |
| SemIf | 39/47 | **0** | 8 | 43/44 | 26/37 | 384 / 484 ms |
| decider-4b v2.1 | 41/47 | 1 | 5 | **44/44** | 26/37 | 585 / 686 ms |
| decider-4b v2 | 40/47 | 1 | 6 | **44/44** | 27/37 | 685 / 691 ms |
| hosted Jev (reference) | 45/47 | 1 | 1 | 43/44 | 31/37 | 264 / 299 ms |

Every local model returned identical numbers on repeated runs; hosted Jev didn't. None of them ever
answered without the image when the question needed it.

**The benchmark lead doesn't carry over to Sensei.** On the decision that matters most — should
Sensei reply at all — decider is two to three utterances behind JevK5 and 200–300 ms slower. Its
strength, catching every "new problem" (and confidently: 38 of 44 in v2), is one utterance better
than JevK5 on this set. With 47 cases a one- or two-case gap is within noise; the latency and the
extra replies are the consistent differences. JevK5 stays the default; `decider` and `decider-v2`
are selectable backends. General-purpose benchmarks measure a model; the utterance set measures it
at our job, and is where a choice like this should be made.
