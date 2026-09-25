# Sensei Desk demo script: math and physics

For the 25 Sep showcase. Tested this afternoon against the live model (`qwen3-vl-30b` + JevK5) on
the printed versions of these exact problems. Every line under "Sensei says" is what it actually
said in testing; wording will vary a little.

## The one rule

**Sensei finds the mistake on paper; the student fixes it out loud.** Then move to the next sheet.

Today's model catches the planted mistakes reliably (11 of 11 in testing) and confirms a correct
spoken answer in 1-2 seconds. But when the corrected work is shown on paper, it keeps flagging the
line that was just fixed (0 of 10 in testing). So don't write the fix and wait for a verdict: say
the fix, hear Sensei agree, then swap sheets.

---

## Before you start (10 minutes before)

- White paper, a **thick dark marker**, one problem per sheet. Write large, about 1.5 cm letters,
  one step per line, the problem on the top line. No line numbers.
- Pre-write all sheets (below) so each demo takes under a minute. Keep them in order, face down.
- Desk lamp on, no window glare, tape marks for where the sheet goes.
- Head presets: nudge in the console until the phone frames the sheet, **Save as notebook**;
  frame the presenter's face, **Save as student**.
- Console: `https://spark-e257.tail803c7f.ts.net:8443/?key=...` shows the camera, what Sensei
  read, and each decision. Keep it open on a laptop.
- Phone: open Sensei Cam, **Start** a **15-minute** session, turn **voice mode** on.

**Timing:** Sensei judges a page when the hand has been off it for about 3 seconds, then reads it
twice to be sure before speaking. Expect the question **8-12 seconds** after you lift your hand.
Nothing after 15 seconds? Tap **Hint**.

---

## Opening (30 seconds)

> "This is Sensei. It's a phone on a head we built, and everything runs on this box, offline. It
> watches you work on paper, and when you go wrong it doesn't give you the answer. It asks you a
> question."

Each new sheet can just be put down: Sensei reads it as a new problem. Saying the problem out loud
as you put it down ("Next one: solve x plus 3 squared equals 25") also works and sounds natural.

---

## Warm-up: "What do you see?" (optional, 20 seconds)

Sheet 0, write only:

```
Solve 3x + 5 = 20
```

Tap **What do you see?** Sensei describes the sheet and reads the equation out. This shows the
eyes without any marking.

---

## Math

### M1. The minus in front of brackets (the flagship)

Write:

```
Solve 5 - (2x - 4) = 11
5 - 2x - 4 = 11
1 - 2x = 11
-2x = 10
x = -5
```

The mistake is line 2: the minus must reach the -4, so it should be `5 - 2x + 4 = 11`.

- **Sensei says:** "What happens to each term inside the brackets when you subtract them?"
- **You say:** "Oh, the minus changes both signs, so it should be plus four, not minus four."
- **Sensei says:** "Yes, the minus sign changes both signs inside the parentheses. Good thinking!"
- **Point to make:** it found the first wrong line, not just a wrong answer, and it asked instead
  of telling.

### M2. Squaring a sum

Write:

```
Solve (x + 3)^2 = 25
x^2 + 9 = 25
x^2 = 16
x = 4 or x = -4
```

The mistake is line 2: `(x + 3)^2` is `x^2 + 6x + 9`.

- **Sensei says:** "What happens to each term inside the brackets when you square them?" (tested:
  "What did you do to expand (x + 3)^2?")
- **You say:** "Oh, x plus 3 squared is x squared plus 6x plus 9. I forgot the middle term."
- **Sensei says:** "You're right, the middle term is 6x. Now, what should you do with the equation?"
- **Point to make:** it keeps teaching after you fix it, with the next question.

### M3. Chain rule (for a technical audience)

Write:

```
Differentiate y = sin(3x^2), find dy/dx at x = 1
dy/dx = cos(3x^2)
at x = 1: dy/dx = cos(3)
= -0.99
```

The mistake is line 2: the inner derivative `6x` is missing.

- **Sensei says:** "What rule did you use to differentiate sin(3x^2)?"
- **You say:** "The chain rule. I forgot to multiply by the derivative of the inside, which is 6x."
- **Sensei says:** "Yes, you're right about the chain rule. You need to multiply by the derivative
  of the inside, which is 6x. What's the full derivative?"

---

## Switch subject (5 seconds)

Say: **"Can you help me with my physics homework?"** Sensei switches to physics: from now on it
checks units, directions and components first, the way a physics teacher would. (The console
shows the subject change.)

---

## Physics

### P1. Units (the easiest to follow for any audience)

Write:

```
A car moves at 72 km/h. How far in 5.0 s?
v = 72 km/h
d = v x t
d = 72 x 5.0
d = 360 m
```

The mistake is line 4 (`d = 72 x 5.0`): km/h times seconds. 72 km/h is 20 m/s.

- **Sensei says:** "You multiplied speed by time, but the units don't match..."
- **You say:** "No. I need to change 72 kilometers per hour into 20 meters per second first."
- **Sensei says:** "Yes, you're right. You need to convert km/h to m/s first. What's the conversion
  factor?"
- **Point to make:** the first two lines are right and the answer even looks believable. It
  checked every line.

### P2. The vector that was never split (the story)

Write:

```
Ball launched at 20 m/s, 60 degrees up. Time to land? g = 9.8
v_y = 20 m/s
t_up = v_y / g = 20 / 9.8 = 2.04 s
t_total = 2 x 2.04 = 4.08 s
```

The mistake is line 2: only part of 20 m/s is upward, `v_y = 20 sin 60 = 17.3 m/s`.

- **Sensei says:** "You wrote v_y = 20 m/s. What part of the launch speed actually goes upward?"
- **You say:** "Only part of it. The vertical part is 20 times sine 60, about 17.3 meters per second."
- **Sensei says:** "Yes, you're right. The vertical component is 20 times sine 60. That's the speed
  going up."
- **Point to make:** "The student didn't fail projectile motion. They failed splitting a vector
  into parts, which they learned two chapters ago. That's what Sensei will track over time."

### Closing (15 seconds)

Say: **"Look at me."** The head turns to the presenter. Then tap **End**: Sensei gives a short
spoken summary of the session.

> "Every word you heard was decided and spoken on this box, with the network unplugged. That's a
> tutor a school in Bangladesh can run without internet."

---

## Spares (use only if needed)

These are caught on paper, but the spoken fix only half-lands in testing: Sensei agrees with the
idea and then asks one more question.

- **P3. Normal force on a slope:** `A 5.0 kg block slides down a 30 degree slope, friction 0.20.
  Friction force?` / `N = mg = 5.0 x 9.8 = 49 N` / `f = 0.20 x 49` / `f = 9.8 N`. Mistake: line 2,
  on a slope `N = mg cos 30`. Sensei asks which way the ramp pushes. Same root cause as P2.
- **C1. Balancing:** `Balance H2 + O2 -> H2O` / `H2 + O2 -> H2O2` / `Check: H 2 = 2, O 2 = 2` /
  `Balanced.` Mistake: line 2, a subscript was changed, which makes hydrogen peroxide. Sensei asks
  "Is H2O2 still water?"

## Avoid in the demo

- **Writing the correction and waiting for Sensei to approve the page.** See "The one rule".
- **A mistake in a late line** (for example a wrong final division). Today's model tends to blame
  the first line instead. Every problem above has its mistake in the first few lines.
- **Crossed-out lines, small writing, or two problems on one sheet.**

## If something goes wrong

| What happens | Do this |
|---|---|
| Sensei says nothing after 15 s | Keep your hand off the page; tap **Hint** |
| It talks about the wrong line | Tap **Hint** again, or move on: "Let's try the next one" |
| It answers something you didn't mean | Tap **Repeat**, or just carry on |
| The head doesn't move | Say "look at my notebook"; the tutor keeps working without the head |
| Phone shows disconnected | Tap Connect; the gateway keeps running |
| Everything stops | Play the backup video |
