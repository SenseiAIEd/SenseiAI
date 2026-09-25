# Where System One decisions fit in Sensei Desk

Companion to [tutor-plan.md](tutor-plan.md) (the decision layer) and
[jev-report.md](jev-report.md) (what Jev is, and how much of it to believe).
Revised 2026-09-24 after reading TypeSafe's API reference and its own list of Jev's weak spots.

---

## 1. The idea in one paragraph

Jev takes text and returns typed decisions with probabilities. **It cannot see the notebook, and
it cannot write a sentence.** So it doesn't replace the vision model or the language model. It
takes over the *decisions* that are currently buried inside the vision model's prompt: should
Sensei answer this, what is the student asking about, does answering need the page, has the
student moved to another problem. The vision model goes back to reading the page, and the
language model goes back to saying one sentence, and both are called only when a decision says
they're needed.

```
PERCEPTION  VLM reads the page -> steps, first_error, other problems    1.5-45 s
            Whisper hears the student -> text                           ~1 s
POLICY      Jev: one call per event, every question at once            ~0.1-0.5 s
EXPRESSION  LLM writes one spoken sentence, only when policy says so   0.5-45 s
```

## 2. Five rules for using it efficiently

1. **One call per event.** An event is something happening: the student finished speaking, the
   page settled, Sensei is about to speak. Everything we need to know about that moment goes
   into one request. Jev answers all questions in parallel, so six cost about what one costs.
2. **Code builds the state; the state stays small.** Jev's accuracy drops as the state fills
   with irrelevant detail (their jaggedness doc, item 5). The state is a short object Sensei
   builds from what it already knows (the problem in focus, the page as last read, the last few
   turns, what Sensei last said), never raw logs.
3. **Code does anything numeric.** Jev is "not a calculator" (item 2). Timers, counts and
   thresholds stay in code, and so does judging whether an algebra step is right: that stays
   with the vision model. Jev decides meaning, not maths.
4. **Say exactly what you mean.** Jev reads instructions literally (item 1). Every question
   names the field it's about and puts the edge cases in its criteria.
5. **Always have a floor.** If Jev is off, slow, failing or unsure, Sensei does exactly what it
   does today. Jev can only make Sensei better, never stop it working.

## 3. The events

| Event | Questions (one call) | What the code does |
|---|---|---|
| **Student finished speaking** | `respond` (noul), `about` (choice), `followup` (noul), `needs_page` (noul), `new_problem` (noul), `which_problem` (choice over the other problems the page shows) | Not for Sensei: stay quiet, no model call. Doesn't need the page: text-only call, no image. New problem named on the page: switch focus before answering. |
| **Before Sensei speaks** | `gives_away_answer` (noul) on the sentence it's about to say | Replace the sentence with a plain guiding question. |
| **Page settled** | `student_state` (choice: working, stuck, confused, finished), `speak_now` (noul) | Pace hints by how the student is doing, not by `hint_level + 1`. The *maths* judgement stays with the vision model (rule 3). |
| **Partial speech** (later) | `turn_done` (noul): have they finished their thought? | Reply sooner without cutting them off; today we wait for a fixed silence. |
| **Tick** (with the head) | `look_at` (choice), once expression or language cues are part of the state | Until then, gaze is a few lines of rules: its inputs are numbers (talking, pen moving, waiting for an answer). |

Removed from the previous plan: Jev double-checking whether a step is wrong (Jev's own docs say
keep maths in code), and routing between the thinking and instruct models (the router takes
15 s one way and 155 s the other on this Spark).

## 4. Where it runs

The client speaks TypeSafe's `/v1/systemone` format. Hosted Jev and a local JevK5 server both
use that format, so moving between them is a URL change.

| | For | Notes |
|---|---|---|
| Hosted Jev (`api.typesafe.ai`) | Now, and tuning | Key in `~/projects/jev/.env`. Needs internet; sends the student's words to a third party. |
| **Local JevK5 (default)** | The unplugged demo and the product | Qwen3.5-4B + distilled LoRA, T=1.22. Port 8095, ~14 GB GPU, ~370 ms. |
| Local SemIf | Comparison | The same prompt on the frozen base model, T=1.23. Port 8096. |
| Local decider-4b v2.1 / v2 | Comparison | JevBench v1.4.2 leader. Behind JevK5 on our set and slower (jev-report.md §8). Ports 8097/8098. |

Offline is a Friday success criterion and a mission requirement, so local JevK5 is where this
ends up. Hosted is the fast way to get the integration right first.

## 5. The switch

`USE_JEV=1` in `sensei.env` turns it on at startup; default off. It can also be flipped at
runtime from the console or with `POST /jev {"on": true|false}`, so it can be turned off
mid-demo without a restart. When off, nothing about Sensei changes.

## 6. Order of work

1. ~~**Now:** the `/v1/systemone` client, the "student finished speaking" event with its floor,
   the `USE_JEV` switch, and decision logging (`jev` events in `log.jsonl`).~~ **Done 24 Sep**
   — `gateway/jev.py`, wired into `Tutor.hear`. A failed call backs off for 30 s, so an unplugged
   run pays the timeout once, not on every utterance.
2. ~~**Now:** a labelled set of real utterances from our sessions and a scorer.~~ **Done 24 Sep**
   — `gateway/evals/utterances.jsonl` (48 real utterances, 23–24 Sep), `gateway/jev_eval.py`.
   Hosted `jev-1.13`, three runs, median ~270 ms:

   | decision | Jev | when Jev was confident | today's rule |
   |---|---|---|---|
   | should Sensei reply | 44/47 | 19/19 – 20/20 | 32/47 (the `FILLERS` word list) |
   | what it is about | 30–31/37 | 26–27/29 | — |
   | does it need the page (things worth answering) | 23–24/25 | 9/9 | always sent |
   | moved to another problem | 43/44 | 29/29 | — |

   An end-to-end run (real Jev, real VLM, the 24 Sep frame) found three things the eval didn't:
   a low reply score was overriding a clear "new problem" (now: being steered is never
   ignored); the writing model's `focus` field moved focus to "what is a coefficient?" (now:
   with Jev on, only Jev's `new_problem` moves focus); and a 0.10 floor after questions let
   "thank you" through (now 0.30 — Jev already sees whether Sensei asked something).
3. ~~**Next:** stand up JevK5 locally and score it on the same set.~~ **Done 25 Sep** — JevK5
   (default) and SemIf run on the Spark (`~/projects/jev-local`, ports 8095/8096); hosted stays
   selectable. `SENSEI_JEV_BACKEND` picks one at startup, the console or `POST /jev
   {"backend": ...}` switches at runtime, and each backend brings its own floors (`jev.BACKENDS`),
   because local models spread their probabilities differently. At its own floor JevK5 gets 42/47
   on "should Sensei reply" (hosted 44/47, today's word list 32/47), 43/44 on "moved to another
   problem", in ~370 ms, with no internet. Full numbers in jev-report.md §7.
4. **Then:** "before Sensei speaks" (answer guard), then "page settled" pacing.
5. **With the device:** partial-speech turn-taking and gaze.
