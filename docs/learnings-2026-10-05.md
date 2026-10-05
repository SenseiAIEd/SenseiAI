# Sensei Desk: teaching state and sharper eyes, 5 Oct

Follows [learnings-2026-09-26-28.md](learnings-2026-09-26-28.md), whose "what's next" items 2-5 this
round works through.

## 1. Teaching state (answer key, lesson state, no repeats)

What changed in `gateway/tutor.py`:

- **Answer key.** Once a mistake is confirmed, a background call solves the problem privately
  (`Brain.solve` -> correct line, answer, check). Every reply after that gets it as a PRIVATE ANSWER
  KEY note: use it to judge, never read it out.
- **Fix said aloud.** The talk reply now returns `student_fixed`. When it is true, the mistake
  counts as fixed (`tutor_fixed_by_voice`) and the next look at the unchanged page doesn't
  re-hint it.
- **No repeats.** Replies see Sensei's last three lines. A reply that is a near-copy of one of them
  (word overlap of 0.6 or more) is sent back once for a different approach (`tutor_repeat_retry`).
- **"Why" gets a reason.** The prompt asks for the reason in plain words, not the rule again, and
  never to call a correct answer wrong.

Same 24 synthetic students, 10 problems; fresh blind judges scored the old and new tutor together
(`blind.py export current state round2`, `blind.py report round2`):

| | before | teaching state |
|---|---|---|
| overall (out of 10) | 4.04 | **4.83** |
| students who reached the fix | 18/24 | **21/24** |
| correctness (0-2) | 0.79 | 1.21 |
| teaching when stuck (0-2) | 0.79 | 1.08 |
| answered what they said (0-2) | 0.88 | 0.96 |
| wrong facts | 14 | 8 |
| answer leaks | 7/24 | 9/24 (worse) |

Still failing: telling a student who fixed it aloud they're wrong when the old line is still on the
page; blaming line 1 for late mistakes (all m4 conversations still score 1); false praise on the
incline problem; slightly more answer leaks (the key makes the right answer easy to say).

## 2. Sharper eyes: a careful model confirms mistakes

The fast model (`qwen3-vl-30b`) still looks at every page. When it suspects a new mistake, the
second, confirming look goes to the careful model (`qwen3.8-27b`, NVFP4, own vLLM on :8120), and
its verdict stands: no mistake clears the suspicion, and a different line replaces it. If the careful
model fails or times out, the fast one confirms as before. The careful model also writes the
answer key. Configure with `SENSEI_VERIFY_URL` / `SENSEI_VERIFY_MODEL`; unset = fast model only.

Measured on all 50 sample pages with handwritten work (15 correct, 35 with a mistake); the right
line was checked by hand against each page's written work:

| | fast alone | careful alone | cascade (live) |
|---|---|---|---|
| false alarms on correct pages | 10/15 | 1/15 | **1/15** |
| mistakes flagged | 32/35 | 31/35 | 30/35 |
| right line | 20/35 | 29/35 | **28/35** |
| time per page (median) | 5.5 s | 38 s | fast look + careful look |

- The careful model fixes most "blames the wrong line" errors: a dropped sign on line 4, the
  Earth-Moon distance, time of flight off a cliff, forces added instead of subtracted, an aldehyde
  called a ketone.
- It is not perfect: on two trig pages the fast model was right and it wasn't (it missed a bad
  factorisation and flagged the line after a wrong discriminant). Both models missed two
  unbalanced organic equations and blamed a later line for a wrong box set-up (`30 - x` for
  `30 - 2x`).
- Cost: the first hint now comes about 25-45 s after the page settles instead of about 11 s. A
  hint is only spoken after the student has had time to look anyway (`HINT_WAIT_S` is 20 s), so a
  correct, later hint is the better trade than a fast wrong one.
- **Shared GPU.** With both models busy, the fast model slows from 5.5 s to about 10 s a page, and
  four queued requests at once made it crawl (minutes). The careful model only runs when a
  mistake is suspected, one page at a time; the vLLM share is 0.22 of memory (about 25 GB).
  JevK5 was stopped to make room; Imajev-4B stays.

## 3. Push-to-talk (phone app)

Hold the mic button to talk (Settings, on by default). Releasing ends the utterance at once (the
gateway's ears now finish the utterance instead of dropping it). Needs a new APK; the Spark has no
Android SDK, so build it on a laptop (`npx expo run:android` or EAS).

## 4. Still to do

- Calibrate the head's student preset at the desk (console: nudge until the face is framed, Save as
  student), then `SENSEI_AUTO_LOOK=on`.
- Re-run the 24 students with the careful model confirming pages.
- Answer leaks: check the reply against the key's answer before speaking.
