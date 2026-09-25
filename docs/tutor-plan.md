# Sensei: from a loop that reacts to a tutor that decides

A plan for the next stage of the tutor, written against the code as it stands on 2026-09-24
(`gateway/tutor.py`, `gateway/server.py`, `gateway/ears.py`) and the goals in
*Sensei Desk: Pan-Tilt AI Tutor*.

The short version: Sensei can now see and hear, but it has no **policy**. What it says is
decided by an if-chain that runs after whatever it happened to look at last. This plan gives it
a decision layer, a model of the student, and a way to tell whether any learning happened.

---

## 1. What decides what Sensei says today

Four things can trigger speech, and they are independent:

| trigger | where | what it does |
|---|---|---|
| a settled page | `on_frame` (tutor.py:503) | judges the page, then `_react` picks a line |
| the student speaks | `hear` (tutor.py:556) | answers, always with the current frame attached |
| a button | `request` (tutor.py:514) | hint / check / look / repeat / pause / resume / end |
| the clock | `tick` (tutor.py:486) | one-minute warning, idle nudge |

The only arbitration between them is `self.thinking` (tutor.py:642), a mutex around the model
call, plus `pending_question` (tutor.py:404), which holds one spoken question until the current
look finishes. Everything else is ordering inside `_react` (tutor.py:685).

That gives three problems.

### 1.1 It cannot listen while it talks

The phone mutes the microphone for the whole time Sensei is speaking (`app/App.tsx`, `speak()`).
This is deliberate — otherwise Sensei hears its own voice — but it means the student physically
cannot interrupt. A tutor who cannot be interrupted is not in a conversation; they are
broadcasting.

### 1.2 Everything the student says is fused with whatever is on the page

Every spoken question goes to the model together with the newest frame and `last_seen`. The
instructions tell it to "use the image and the conversation". So when a student asks *"why does
subtracting work?"*, Sensei answers about the envelope on the desk. Real transcript, session
`20260923-080548-465`:

> Student: "What do you see, Nana?"
> Sensei: "I see a white envelope on a wooden table, with some stains on it and a black tripod
> in front…"

Eight replies in that session opened with *"I see a white envelope…"*. The student's words were
treated as a caption request for the current frame. There is no representation of **what the
utterance is about**, so the page always wins.

### 1.3 The Socratic teaching is one paragraph

The whole pedagogy is the "Rules for say" block in `SYSTEM_PROMPT`: never give the answer, one
short sentence, and three hint levels (point at the line → name the rule → nearly reveal it).
That is a good start and it is genuinely Socratic, but it is missing everything a teacher does
around the hint: waiting, diagnosing before correcting, checking that an explanation landed,
and knowing this particular student.

---

## 2. The decision layer

Split **perception** (what is true right now) from **policy** (what to do about it). Today one
model call does both and the policy is scattered.

### 2.1 Situation

One object, rebuilt whenever anything changes:

```
Situation
  heard        last utterance: text, seconds of audio, when, and whether the mic is live now
  speaking     is the student talking at this instant (VAD), is Sensei talking
  page         last Assessment: kind, steps, first_error, finished, hand_over_page, rotated
  page_age     seconds since the page last changed
  student      the model in §3: known misconceptions, pace, how often they answer
  session      phase, time left, hint level, last line spoken and when
```

A System One model (Jev, or a local reproduction) is a natural fit for this tier: typed
questions, calibrated probabilities, ~80 ms. See [jev-in-sensei.md](jev-in-sensei.md) for which
decisions are worth moving and what has to be proven first.

### 2.2 Actions

Exactly one per decision, each with a recorded reason:

```
stay_quiet · answer(utterance) · hint(level) · diagnose · acknowledge_fix
celebrate(step) · nudge · ask_to_show(reason) · wait_for_answer · wrap_up
look_at(notebook | student)        <- the pan-tilt head
```

`look_at` belongs here rather than in the hardware client. The Desk guide already has the tutor
choosing gaze (`look_at_notebook()`, `look_at_student()`); attention is a tutoring decision, and
it is the same decision as "should I be reading the page or watching the person right now".

### 2.3 Priority

This is the part that answers "it always prioritizes what it's seeing":

1. **The student is speaking** → stop talking. Nothing outranks this.
2. **The student just spoke** → answer them, routed by §2.4.
3. **The student pressed a button** → serve it.
4. **A confirmed mistake** → hint, rate-limited, escalating.
5. **A fix or a finish** → acknowledge once.
6. **Clock** → one-minute warning, idle nudge.
7. Otherwise → silence. Silence is a legitimate and common answer.

Rules 2–7 mostly exist inside `_react` already; the work is making them explicit, testable, and
logged (`decision` events with the situation that produced them), rather than implied by the
order of `if` statements.

### 2.4 Routing what the student said

Before answering, decide what the utterance is **about**. Cheapest version: one extra field in
the JSON the model already returns, so it costs no extra call.

```
about = page        "is my second line right?", "check this", "what do you see?"
        subject     "why does subtracting work?", "what's a coefficient?"
        sensei      "what did you say?", "repeat that", "stop"
        social      "I'm tired", "my name is…"
        unclear     misheard, or could mean several things
```

Only `about: page` attaches the frame and may talk about what is in view. `about: subject`
answers from the conversation and the problem, with the image present but explicitly not to be
described. `about: sensei` is answered from `conversation` without a model call at all where
possible. `unclear` asks one short clarifying question instead of guessing — the current
behaviour of confidently answering a misheard fragment ("You said 'Love' — what part of the
page made you say that?") is worse than admitting it.

### 2.5 Turn-taking (barge-in)

The blocker is physical, not logical. Sensei's voice is Android TTS played on the phone's
speaker; WebRTC's echo canceller cannot remove it because it never rendered it. Keeping the mic
open today means Sensei hears itself and interrupts itself on its own first syllable.

The fix that actually works: **synthesise on the Spark and send the audio back over the WebRTC
track**. Then the voice is the render stream, AEC references it, the mic can stay open, and
barge-in is a VAD threshold. It also gives a better voice than Android TTS, makes a "thinking"
sound trivial (play it into the same track), and keeps everything offline, which the mission
requires. Piper or Kokoro on the Spark; aiortc can push an audio track.

Until then, the honest interim is what we already did — shorter utterances, so there is less to
interrupt — plus a visible stop control.

---

## 3. A model of the student

Everything Sensei knows is currently thrown away when the call drops. `conversation` holds 20
turns; `mistakes_found` and `mistakes_fixed` hold strings for the wrap-up.

Per student, persisted:

```
misconceptions   kind -> {seen, fixed, last_seen, example}     e.g. sign-when-distributing
pace             median seconds per step, median hints per mistake
independence     fraction of steps taken with no hint
explains         how well they explain a step back, scored 0-2
topics           what they have worked on, and when
language         what they answer in
```

Two uses. It goes into the prompt ("last week they mixed up signs when distributing; watch for
it"), and it is the input to §5. `error_kind` already exists and is the seed of the
misconception taxonomy — it needs to become a fixed, per-subject list so counts mean something.

---

## 4. Watching the student, not just the page

The Desk has `look_at_student()`. What it is for:

- **Are they still there, and still working?** The cheapest, most useful signal. Currently an
  idle timer guesses this from the page not changing.
- **Are they stuck?** Long pause + no writing + looking away is different from long pause +
  writing. Right now both look identical.
- **Are they frustrated or lost?** Affect from face and posture.

Affect should be a **policy input, never a topic**. Sensei should shorten the gap before
offering help, drop a hint level, or change activity — not say "you look frustrated", which is
intrusive and often wrong.

Caveats to design in from the start, not bolt on:

- Automated emotion recognition from faces is contested and less accurate across ages, skin
  tones and cultures than vendors claim. Treat it as a weak prior, never as fact, and never as
  a record.
- These are children. Faces must be processed **on the Spark, in memory, never written to
  disk** — the session recording already keeps the notebook view; a face track is a different
  category of data. Consent belongs to the parent or school, and the default should be off.
- The mission is Bangladesh, where consent and data norms are not the ones a US pilot assumes.

A useful first step needs no affect model at all: use `look_at_student()` to answer "present /
absent / writing / looking away", which is most of the value at a fraction of the risk.

---

## 5. Did any learning happen?

Sensei counts `hints_given`, `mistakes_found`, `mistakes_fixed`, `problems_finished`. All four
go up when Sensei talks more, which makes them useless as a measure of teaching and actively
dangerous as a target: the easiest way to maximise `problems_finished` is to give the answer.

Measure the student, not the tutor:

**Within a session**
- *first-attempt correctness* — steps right before any hint
- *hints to fix* — how far down 1→3 the student needed to go (lower is better learning, but
  only when paired with the next one)
- *explain-back* — after a fix, "why does that work?", scored 0–2 by the model. A fix the
  student cannot explain is a copied correction.
- *unassisted recovery* — they spotted and fixed it before Sensei spoke

**Across sessions**
- *misconception recurrence* — does `sign-when-distributing` come back next week? This is the
  single most honest number available: a fixed mistake that returns was never learned.
- *independence trend* — hints per problem over time, on problems of the same difficulty
- *transfer check* — the gold standard. Same misconception, different surface problem, no
  hints. Sensei can generate one at the end of a session and simply watch. Solved unaided =
  learning; not solved = the student got through a problem, which is not the same thing.

**Guards**
- Never let Sensei optimise a number it can move by talking.
- Log the *decision* and the *situation*, not just the outcome, so a session can be re-scored
  later when the definition of "learned" improves.
- A human check: sample sessions and have a teacher rate the hints. Everything above is a
  proxy, and proxies drift.

---

## 6. Order of work

Cheap, unblocks the complaints you actually have:

1. ~~**Utterance routing (§2.4)**~~ — **done 2026-09-24.** `about` is in `CHAT_SYSTEM_PROMPT`
   and logged as `tutor_about`; "say that again" is answered from memory with no model call.
   Measured on the real model with the envelope frame attached: 5 of 6 utterances routed to the
   expected class, all 6 answered correctly, ~1.5 s each.
2. **Explicit `Situation` + `Decision` with logged reasons (§2.1–2.3)** — mostly a refactor of
   `_react`, but it makes everything after this testable. A day.
3. **Wait-for-answer and diagnose-before-hint (§1.3)** — pedagogy that needs no new inputs.

Bigger, in order of value:

4. **Voice on the Spark (§2.5)** — unblocks barge-in *and* the thinking sound *and* improves
   the voice *and* keeps it offline. One change, four wins. A day or two.
5. **Student model (§3)** — needed before any cross-session measure means anything.
6. **Learning outcomes (§5)** — start by logging what §5 needs; the scoring can come later.

Blocked on hardware:

7. **`look_at_student()` presence and attention (§4)** — needs the pan-tilt head.
8. **Affect as a policy input (§4)** — last, and only with the consent design decided first.

---

## 7. Open questions

- **Which subjects?** The misconception taxonomy has to be written per subject. Algebra first?
- **One student per device, or many?** Decides whether the student model needs identity, and
  whether faces are used to recognise *who* — a much heavier consent question than affect.
- **Multilingual**: the goal says multilingual and the mission says Bangladesh. Whisper
  `small.en` is English-only today, and the Socratic prompt is English. Bangla changes the model
  choice for both ears and brain, and should be decided before the student model hardens.
- **What is a session for?** A fixed 5–15 minutes, or until the student finishes a problem? The
  wrap-up, the transfer check and the pacing all depend on this.
