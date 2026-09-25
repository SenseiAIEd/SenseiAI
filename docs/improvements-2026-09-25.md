# Sensei Desk: what we learned and changed on 25 Sep

Written 2026-09-25, after the first demo with the pan-tilt head connected to the Spark. It covers
the demo's session log, the experiments run on the recordings and the sample pages, the changes
made because of them, and what is still open. Numbers marked **measured** were run on the Spark
today. The earlier Jev work is in [jev-report.md](jev-report.md) and
[jev-in-sensei.md](jev-in-sensei.md); section 5 summarizes it and adds today's findings.

---

## 1. Summary

| Problem seen | Cause | Change | Evidence |
|---|---|---|---|
| Phone could not connect after the Spark moved | TURN relay still pointed at the old LAN IP; Funnel port 10000 had been taken by another service | Relay IP updated; port 10000 back to the relay, hermes-agent moved to :8450 | Calls went from 0 frames to connected in 0.9 s via the relay |
| Sensei never judged the page in a 5-minute demo | A failed head move (head unplugged) set the camera to "elsewhere", and only "notebook" frames are judged | A failed move keeps the camera's last target; a broken head is retried every 15 s, not every 3 s | Demo log: 0 page assessments in 300 s, 82 failed looks |
| The head would have spent the session on the student's face | Jev chose "student" at ~0.8 every 3 s while the student wrote | Jev decides; anything it can't decide means the notebook; it can only leave the page once the page is quiet | Demo log: 80 Jev "student" choices, confidence 0.73-0.86 |
| Sensei answered speech nobody said | Whisper writes "Thank you.", "Okay.", "Silence." for noise and silence | Silero voice check + Whisper's own no-speech probability for its stock phrases | Replay of 144 utterances: 35 phantoms dropped, 0 real ones |
| One sentence answered in two halves | Turn ended after 0.8 s of silence even mid-sentence | A transcript ending on "the", "and", "because"... waits 1.5 s and is joined | "Yeah, this is the." + "I want to do." in the recordings |
| "Okay, let me think." before "Hello back!" | The filler covered every reply slower than 1.8 s | No filler for small talk (Jev: about = social) | Demo log |
| Silence while the work was right | Correct, unfinished work got `say: null` | A short "that's right so far" after two new correct lines, at most every 45 s | Design; covered by tests |
| One teaching style for every subject | One prompt, free-form mistake kinds | Subject + topic from the page; per-subject playbook, mistake list, hint ladder and self-check | Answer keys in `datasets/samples/*/README.md` |
| The fast vision model flags mistakes in correct work | Model choice (speed first) | Not changed today; see section 4 | 6 of 7 correct pages flagged |

Tests: 105 before, 129 after, all passing. The changes are on branch
`sensei-listen-look-feedback` in `~/SenseiAI` (the listening, gaze and feedback work is committed;
the subject work is not yet).

---

## 2. What the demo log showed

Session `20260925-115426-035`, 5 minutes, voice mode on, head unplugged for most of it.

```
  1.2  look notebook  session start        ok=False     <- head unplugged
  4.3  look student   jev student (0.84)   ok=False     ... then every ~3 s until 244 s
 33.5  heard "Thank you."   (0.32 s of audio)           <- nobody said it
 38.8  heard "Hello."
 41.0  say "Okay, let me think."   41.3 say "Hello back! Ready to solve a problem?"
 58.6  heard "Thank you."  -> "You're welcome! Let's get back to solving."
162.9  say idle nudge
194.4  heard "Thank you."  -> "You're very welcome! Let's get back to solving."
242.0  say one-minute warning
302.1  wrap-up. Page assessments in the whole session: 0.
```

Across all 25 recorded sessions, "Thank you." was heard 11 times, 8 of them from under a second
of audio. Some real transcripts show the student frustrated at not being answered
("I asked a question.", "You may not acknowledging what I'm asking.").

---

## 3. Listening: phantom speech and cut-off sentences

### 3.1 Replay experiment (measured)

Every session's recorded audio was fed back through the real `Ears` segmenter, so each clip is
exactly what the gateway transcribed. For each of the **144 utterances** we recorded Whisper's
text, its `no_speech_prob` and `avg_logprob`, and how much speech Silero VAD finds in the clip.

What separated phantoms from speech:

| | Silero speech | no_speech_prob | Examples |
|---|---|---|---|
| Phantoms | 0 s (20 clips), or 0.6-1.1 s with a stock phrase | 0.4-0.75 | "Thank you." ×10, "Okay.", "Silence." ×2, "Bye.", "See you again.", empty |
| Real speech | 1.2 s or more | mostly under 0.1 | "What do you see?", "Can you hear me?", "No." (1.2 s, 0.02) |

The textbook Whisper rule (`no_speech_prob > 0.6 and avg_logprob < -1`) misses these: the
phantoms' log-probability is only -0.7 to -1.0.

### 3.2 The filter

1. Silero VAD (bundled with faster-whisper) must find at least **0.4 s** of speech, before
   transcription is even run.
2. A transcript that is one of Whisper's stock phrases ("thank you", "okay", "silence", "bye",
   "thanks for watching"...) **and** has `no_speech_prob >= 0.4` is noise. A clearly spoken
   "thank you" (probability 0.2 or less) still gets through.

Result on the replay: **35 of 144 clips dropped, all phantoms; none of the real questions or
answers dropped.** Dropped clips are logged as `heard_noise` and never answered.

### 3.3 Cut-off sentences

The turn ended after 0.8 s of silence, so a student pausing to think was answered mid-sentence.
Now a transcript that ends on a word a sentence can't end on (an article, conjunction,
preposition, "like", "equals", or "...") is held for 1.5 s and joined with what follows. Numbers
count as words, so "x equals 5" is finished and "x equals" is not. Finished sentences aren't
delayed.

---

## 4. Seeing: the vision model

### 4.1 Short comparison (measured, partial)

The 18 basic sample pages (6 math, 6 physics, 6 chemistry; 11 with one planted mistake, 7
correct), each model warmed up first so the router's model swap isn't timed:

| Model | Flagged a mistake (of 11) | False alarm on correct work (of 7) | Median per page |
|---|---|---|---|
| `qwen3-vl-30b-a3b-gguf` (current) | 11 | **6** | 3.3 s |
| `gemma-4-26b-a4b-nvidia-nvfp4` | 8 | 1 | 8.6 s |
| `qwen3.8-27b-unsloth-nvfp4` | 11 | **0** | 23 s |
| `qwen3-vl-30b-a3b-thinking` | not finished (stopped to free the router for testing) | | |

Caveats: the scorer only checks *whether* a mistake was flagged, not whether it was the right
line (to be checked against the answer keys); 18 pages is a small set; only 7 of them are correct
work.

Reading: the current model's 11/11 is not a real catch rate, because it flags almost every page.
The answer keys make the point that a false alarm on correct work is worse than a missed
mistake. qwen3.8-27b was the only clean score, at 7 times the latency.

### 4.2 Why we didn't switch today

The router keeps one large model in memory at a time, and a swap costs 15-160 s. Routing each
request to a different model is therefore too slow today. It isn't a hardware limit: the Spark
has 121 GB, qwen3.8-27b's weights are ~18 GB at NVFP4, and its server reserves 62% of memory
mostly for long contexts. qwen3.8 at ~30%, the fast qwen3-vl-30b (~20 GB) and JevK5 (11 GB)
would fit together in ~70 GB. With all three resident, routing costs nothing:

| Request | Model |
|---|---|
| Talking, small talk, "what do you see?" | fast qwen3-vl-30b (~3 s) |
| Background look at the page | qwen3.8-27b (runs while the student writes) |
| "Is this right?", Check, Hint | qwen3.8-27b |

The gateway already supports a separate chat model (`SENSEI_CHAT_MODEL`); Jev's existing
`about` / `needs_page` decisions route spoken questions. Decision for today: stay on
qwen3-vl-30b + Jev for the live test. Options for later: run a Sensei-only qwen3.8 server outside
the shared router (safest), or change the router's memory budget (helps every app, but changes
shared infrastructure).

---

## 5. Jev

### 5.1 Where we were (from jev-report.md)

Jev answers typed questions with calibrated probabilities instead of generating text. Sensei
uses it for the fast decisions around each utterance: should Sensei reply at all, what is it
about, does it need the camera image, has the student moved to another problem. Hosted Jev is the
wrong default for an offline tutor, so Sensei runs local reproductions. On 48 real utterances,
JevK5 was the best local backend: 43/47 correct "should reply" decisions, 3 unneeded replies,
375 ms median. It stays the default; SemIf and decider-4b are selectable. The benchmark leader
(decider-4b) did not carry its lead over to Sensei's own decisions.

### 5.2 Found today

- **Jev let phantom speech through.** It scored the phantom "Thank you."s at respond = 0.21,
  0.24, 0.31 and 0.47, and "Hello." at 0.53. Against floors of 0.40 (0.30 after Sensei asks a
  question), two of the phantoms and "Hello." were answered. Jev judged the words correctly; the
  words were never said. The fix belongs in the ears (section 3), not in Jev's thresholds.
- **Jev's gaze choice was the wrong shape.** Asked "notebook, student or stay?" once a second,
  it answered "student" at 0.73-0.86 from the first seconds, while the student was writing. Its
  confidence overruled the rules. A calibrated probability is only as good as the question: the
  state didn't make "the student is writing right now" decisive.

### 5.3 Changes

- **Gaze:** with Jev on, Jev decides and the notebook is the default. Unsure (below 0.55), down,
  or refused by the guard rails means the notebook. Jev can take the head off the page only once
  the page has been quiet for 12 s, or when the rules see a reason (waiting for the answer to
  Sensei's question, the student sounds stuck). "Stay" can't stretch a glance past 8 s; at most
  one glance every 20 s. With Jev off, the rules decide as before. The face's expression is read
  during each glance and sets the tone of the next minute.
- **Small talk:** Jev's `about = social` (confidence ≥ 0.6) now also means "no filler line first".
- **Subject in Jev's state:** Jev now sees the subject in focus.

### 5.4 Why subject detection is not a new Jev question

The subject is almost always visible on the page, and the vision model already reads the whole
problem on every look, so asking it for `subject` and `topic` costs nothing. Most utterances
("is this right?") carry no subject, and Jev's accuracy falls as its state fills with irrelevant
detail. The one case speech settles, a student naming the subject ("help me with my physics
homework"), is matched directly.

---

## 6. Subjects and teaching patterns

Each read of the work now returns `subject` (math, physics, chemistry, other) and `topic`. Once
the subject is known, every look at the work carries only that subject's playbook and its fixed
list of mistake kinds, taken from the ranked, sourced lists in `datasets/samples/<subject>/README.md`:

| | Where the mistake usually is | Hint level 2 | Hint level 3 | The student's own check |
|---|---|---|---|---|
| Math | A line: minus through brackets, (a+b)² = a²+b², chain rule, one term divided | Names the rule, as a question | A tiny parallel example with small numbers | Put the answer back into the equation |
| Physics | Before the algebra: units, components, normal force on a slope, sign convention | Asks about the physics (direction, units) | A simpler situation showing the same idea | Units, and whether size and direction make sense |
| Chemistry | What a formula means: subscript changed, limiting reagent, Celsius in gas laws, grams as moles | Asks what the formula or number means | A simpler parallel case | Count every atom and the charge on both sides |

Other teaching changes:

- **Hint level 3** is now a parallel, simpler problem that isolates the same idea, in place of a
  question that nearly reveals the answer.
- **Wait time:** after a hint, Sensei waits 20 s before asking about the same mistake again
  unprompted, so the student can find it themselves.
- **Finishing** gets a short congratulation and the subject's own check (a habit worth teaching)
  instead of only "explain why your key step works".
- **Mistake kinds** are fixed per subject, so a student's recurring mistakes can be counted
  (docs/tutor-plan.md §3).
- The subject and topic go to the phone and console, into the log (`tutor_subject`), and into the
  wrap-up.

`python eval_brain.py --playbook` scores a model with and without the playbooks. It has not been
run yet; the playbooks are the likeliest cheap fix for the fast model's false alarms, since the
live second look (which confirms a mistake before Sensei speaks) already carries them.

---

## 7. Operations notes

- The live gateway runs from `~/SenseiAI/gateway` (venv, `sensei.env`, TURN config, sessions),
  not the clone in `~/projects/SenseiAI`. tmux session `sensei`: windows `turn`, `jevk5`,
  `gateway`.
- The phone uses Funnel: HTTPS :8443 to the gateway, TLS :10000 to coturn. Funnel only allows
  443, 8443 and 10000, so 10000 must stay the relay.
- `turnserver.conf` must name the Spark's current LAN IP (192.168.2.56 on Wi-Fi as of today).
- `SENSEI_HEAD_PORT=/dev/ttyUSB0`; the gateway user needs the `dialout` group.
- Nothing starts on boot yet; after a reboot the three tmux windows must be started by hand.

---

## 8. Next

1. Test today's changes in a real session; check `heard_noise`, `progress`, `tutor_subject` and
   the gaze reasons in the log.
2. Run `eval_brain.py --playbook` on qwen3-vl-30b: do the playbooks cut the false alarms?
3. Check the lines qwen3.8-27b flagged against the answer keys, and finish the thinking model's
   run.
4. Decide on a Sensei-only accurate model next to the fast one (section 4.2).
5. Grow the utterance set (`evals/utterances.jsonl`) with today's sessions, and add gaze cases
   so Jev's look decision can be measured like its reply decision.
6. Later: a student model that persists across sessions, Bangla, letting the student interrupt,
   and start-on-boot services.
