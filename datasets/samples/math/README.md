# Math: the mistakes students actually make

Demo data for `/tutor/diagnose`. Two sets of questions, each question in its own directory:

```
samples/math/
  basic/          grade 9 to 12
    easy/  medium/  hard/
  advanced/       grade 12 and first year
    area_between_curves/  optimization/  trig_equation/
    circle_geometry/      cone/            <- these two carry figures

inside each:  ques.png     the problem alone, no working, printed
              good_1.png   a student who solved it correctly
              bad_1.png    a student who made one realistic mistake
              bad_2.png    a different student, a different mistake
              bad_3.png    ...
```

`basic/` fails in one shape: one wrong line, everything after it consistent. `advanced/` is where each question fails differently. Every `advanced/` topic and `basic/easy/` have full `good_1` + `bad_1..3` coverage; `basic/medium/` and `basic/hard/` have `bad_1` only.

`scripts/render_samples.py` derives the band from the topic name, so `easy`, `medium` and `hard` land in `basic/` and everything else in `advanced/`. Adding a topic cannot put it in the wrong place.

Every `bad_N` carries **exactly one** deliberate error, and every line after that error is arithmetically consistent with it. That is on purpose: with two errors in an image, a diagnosis that names the second one first is not wrong, so the sample cannot be graded.

The `good_N` images are not filler. **A tutor that finds an error in correct work is worse than one that misses an error**, because it teaches the student out of something they already had right. There is no way to catch that without a correct sample.

Text and numbers come from `scripts/render_samples.py`. Change a number there and change it here too.

The student work is rendered in a **handwriting font with per-character jitter**, and each student gets a different hand, so the tutor cannot be tuned to one. `ques.png` stays in a printed font: it is the worksheet, not the student. Fonts are vendored in `assets/fonts/`.

**Figures never contain answers.** Every figure carries only what the question supplies (given lengths, masses, species, structures) plus symbols for the unknowns (`d`, `h`, `H`, `R`, `F_E`). Nothing a student has to work out appears on the diagram. This matters twice over: the same figure is drawn on `ques.png`, so a label like a functional-group name would make the worksheet answer its own question, and it would let a model catch a `bad_N` by spotting two lines that disagree rather than by knowing the subject.

**The images carry no line numbers**, because students do not number their steps and a photo of real handwriting will not either. Line numbers below are a reading aid for you, not something the model can see. Grade a diagnosis on **which line's content** it flags, not on the integer it prints.

---

# School set

## How these three were chosen

We did not invent plausible-looking errors. We searched what math teachers and university math departments report as the highest-frequency student mistakes, then picked the top one at each level. The strongest single source is Eric Schechter's error list from Vanderbilt, which ranks errors by how often they appear in real undergraduate work. Sources are listed at the bottom.

Two filters were applied to every candidate:

1. **Frequency.** It must appear on multiple independent "most common mistakes" lists, or in math-education research.
2. **Visible in written work.** The mistake must show up as a wrong line on paper, not just a wrong final answer.

## The most common math mistakes, ranked

| # | Mistake | Why it is so common | Used here |
|---|---|---|---|
| 1 | Sign errors, above all a negative not distributed across parentheses | The minus is read as "this number is negative" instead of "subtract everything that follows" | **Easy** |
| 2 | Linearity assumed where it does not hold: `(a+b)^2 = a^2 + b^2` | Distribution over multiplication is drilled first, then over-applied to every operation | **Medium** |
| 3 | Chain rule: outer function differentiated, inner function forgotten | The inner function is not seen as a separate thing that also changes | **Hard** |
| 4 | Only one term divided or multiplied when the whole side should be | The operation is applied to the term the student is looking at, not the equation | not used |
| 5 | Order of operations, especially `-3^2` read as `(-3)^2` | Handwriting hides which part the exponent belongs to | not used |
| 6 | Invisible parentheses lost partway through | They were never written, so there is nothing on the page to remind the student | not used |
| 7 | Negative root dropped when taking a square root | `sqrt` returns one number on a calculator, so `x^2 = 16` looks like it has one answer | not used |

Mistakes 4 to 7 are real and well documented. They are left out only because three samples is the demo budget. Note that 2, 3 and 6 are the same underlying error in different clothes: *the student applied a rule in a place where that rule is not true*.

---

## Easy: the negative was not distributed

**Directory:** `samples/math/basic/easy/` &nbsp; **Problem:** Solve `5 - (2x - 4) = 11` &nbsp; **Correct answer:** `x = -1`

| File | Answer | First error | The mistake |
|---|---|---|---|
| `good_1.png` | **-1** | none | correct, every line clean |
| `bad_1.png` | -5 | line 1 | minus distributed to `2x` only, not to `-4` |
| `bad_2.png` | 1 | line 4 | distribution correct, then the minus on `-2x` is dropped |
| `bad_3.png` | -2 | line 5 | method fully correct, final division wrong |

The three failures sit on **three different lines** on purpose, so the set tests whether the tutor localizes the error rather than just detecting one. They also differ in kind: `bad_1` is conceptual, `bad_2` is a sign slip, `bad_3` is pure arithmetic. If the tutor answers `bad_3` by re-teaching distribution, it is pattern-matching, not diagnosing.

`bad_2` and `bad_3` share their first three lines with `good_1`. That is deliberate: identical correct openings, three different verdicts.

### `good_1.png` (correct)

```
5 - 2x + 4 = 11
9 - 2x = 11
-2x = 11 - 9
-2x = 2
x = -1
```

**Expected diagnosis:** `FIRST_ERROR: NONE`. Anything else is a false positive, and this is the single most important image in the directory.

**Expected tutor opening:** confirm the work and move them forward. It must not invent a correction, and it must not ask them to re-check a line that is right.

### `bad_1.png` (answer -5)

```
5 - 2x - 4 = 11
1 - 2x = 11
-2x = 10
x = -5
```

**First error: line 1,** `5 - 2x - 4 = 11`. The minus applies to both terms in the parentheses. `- (2x - 4)` is `- 2x + 4`, not `- 2x - 4`. Line 1 should read `5 - 2x + 4 = 11`.

**Why students do this:** the minus is distributed to the first term and then quietly forgotten for the second. Lines 2, 3 and 4 are all correct algebra performed on a wrong line 1, which is the normal shape of a sign error: one bad character, then three flawless steps. This is the single most reported mistake in school algebra.

**Root cause:** distributing over subtraction.

**Expected diagnosis:** the model must flag `5 - 2x - 4 = 11`, and the WHY should mention the sign of the `4`.

**Socratic opening (do not state the error):** "Your last three lines are clean. Go back to the first: the minus in front of the bracket has to reach both things inside. What does it do to the `-4`?"

### `bad_2.png` (answer 1)

```
5 - 2x + 4 = 11
9 - 2x = 11
-2x = 11 - 9
2x = 2
x = 1
```

**First error: line 4,** `2x = 2`. The previous line says `-2x = 2`. The minus was dropped rather than divided out, so the sign of the answer flips.

**Why students do this:** the student treats "get rid of the minus" as a tidying step instead of a division by `-2`. The distribution on line 1 is correct, which is what makes this different from `bad_1`: same subject, later failure, and the tutor must not send them back to re-learn distribution.

**Root cause:** dividing both sides by a negative coefficient.

**Expected diagnosis:** the model must flag `2x = 2`, and the WHY should say the negative sign was dropped instead of divided.

**Socratic opening:** "You got all the way to `-2x = 2` correctly. To get `x` alone from `-2x`, what exactly do you divide both sides by?"

### `bad_3.png` (answer -2)

```
5 - 2x + 4 = 11
9 - 2x = 11
-2x = 11 - 9
-2x = 2
x = -2
```

**First error: line 5,** `x = -2`. Every line above is right. `2` divided by `-2` is `-1`, not `-2`.

**Why students do this:** the `2` gets carried down with a sign attached instead of being divided. This is a computation slip, not a broken concept, and that is exactly what it is here to test. The tutor should correct the arithmetic and leave the method alone. If it starts re-teaching brackets or signs-across-the-equals, it is responding to the topic rather than to this student.

**Root cause:** none upstream. Do not walk the graph on this one.

**Expected diagnosis:** the model must flag `x = -2`, and the WHY should point at the division, not at the method.

**Socratic opening:** "Your whole method is right, so let us just check the last step. What is `2` divided by `-2`?"

---

## Medium: squaring a sum term by term

**Files:** `samples/math/basic/medium/ques.png` (problem) and `samples/math/basic/medium/bad_1.png` (the work below)

**Problem:** Solve `(x + 3)^2 = 25`

**What the student wrote:**

```
x^2 + 9 = 25
x^2 = 16
x = 4   or   x = -4
```

**First error: line 1,** `x^2 + 9 = 25`. `(x + 3)^2` is `x^2 + 6x + 9`, not `x^2 + 9`. The middle term is missing.

**Correct answer:** x = 2 or x = -8. The student got x = 4 or x = -4.

**Why students do this:** squaring is treated as something that happens to each term separately, the way multiplying by a constant does. `3(x + 3)` really is `3x + 9`, so the habit works once and then keeps getting applied. Schechter calls this class of error "everything is additive", and lists it near the top: the same instinct produces `sqrt(x+y) = sqrt(x) + sqrt(y)` and `1/(x+y) = 1/x + 1/y`.

Give the student credit for line 3: they kept both roots, which is itself a commonly dropped step.

**Concept:** quadratic equations. **Root cause:** expanding a binomial square.

**Expected diagnosis:** the model must flag `x^2 + 9 = 25`, and the WHY should say the middle term `6x` is missing.

**Socratic opening:** "You remembered both roots on line 3, good. Now check line 1 by writing `(x + 3)^2` as `(x + 3)(x + 3)` and multiplying it out. How many terms do you get?"

---

## Hard: chain rule, inner derivative forgotten

**Files:** `samples/math/basic/hard/ques.png` (problem) and `samples/math/basic/hard/bad_1.png` (the work below)

**Problem:** Differentiate `y = sin(3x^2)` and find `dy/dx` at `x = 1`.

**What the student wrote:**

```
dy/dx = cos(3x^2)
At x = 1:   dy/dx = cos(3)
= -0.99
```

**First error: line 1,** `dy/dx = cos(3x^2)`. The derivative of the inside is missing. It should be `dy/dx = cos(3x^2) * 6x`.

**Correct answer:** `dy/dx = 6x cos(3x^2)`, which at x = 1 is `6 cos(3) = -5.94`. The student got -0.99, exactly 6 times too small.

**Why students do this:** `sin(3x^2)` is read as "sine, of some stuff". The student differentiates the sine and treats the inside as a label rather than as a function that is also changing. Lines 2 and 3 are then executed perfectly, including the evaluation of `cos(3)` in radians, which many students get wrong. This is a strong student with one missing rule, and the diagnosis should say so.

**Concept:** differentiation of composite functions. **Root cause:** chain rule.

**Expected diagnosis:** the model must flag `dy/dx = cos(3x^2)`, and the WHY should mention the derivative of the inner function `3x^2`.

**Socratic opening:** "Your evaluation in lines 2 and 3 is right, including the radians. Line 1: when `x` moves a little, `sin` is not the only thing that changes. What else does?"

---

# Advanced set

Grade 12 and first-year. These exist because the school set only ever fails in one shape, which is easy for a diagnosis prompt. Each of these fails differently.

| Directory | Correct answer | The failure shape it tests |
|---|---|---|
| `area_between_curves/` | `9/2` | wrong **setup**, right execution |
| `optimization/` | `x = (25 - 5*sqrt7)/3 = 3.92 cm` | right execution, **impossible answer** left unchecked |
| `trig_equation/` | `60.68, 164.32, 240.68, 344.32` degrees | **every line correct, answer incomplete** |

All values were verified symbolically with sympy, not by hand.

---

## Area between curves

**Problem:** The curves `y = x^2 - 4x + 3` and `y = x - 1` intersect at two points. Find the exact area enclosed between them. **Correct answer:** `9/2`.

| File | Answer | First error | The mistake |
|---|---|---|---|
| `good_1.png` | **9/2** | none | checks which curve is on top before subtracting |
| `bad_1.png` | -9/2 | line 4 | subtracted the wrong way round |
| `bad_2.png` | 10/3 | line 1 | used the parabola's own roots as the limits |
| `bad_3.png` | 5/6 | line 7 | subtracted a negative as if it were positive |

**`bad_1`** integrates `(parabola - line)` and reports a **negative area**. Four correct lines follow the bad one. The tutor should not need calculus to catch this: an area cannot be negative, and saying so is the whole lesson.

**`bad_2`** solves `x^2 - 4x + 3 = 0` instead of setting the two curves equal, so it finds where the **parabola** meets the x-axis (`x = 1, 3`) rather than where the curves meet (`x = 1, 4`). Everything after is correct integration over the wrong interval. Very common confusion.

**`bad_3`** is correct through six lines. `F(1) = -11/6`, and the student writes `A = (8/3) - (11/6)`, dropping the sign. Tests localization deep in a long solution.

**Expected diagnosis for `good_1`:** `FIRST_ERROR: NONE`.

---

## Optimization

**Problem:** A 30 cm x 20 cm sheet, squares of side `x` cut from each corner, sides folded up. Find `x` that maximizes volume. **Correct answer:** `x = (25 - 5*sqrt7)/3 = 3.92 cm`, giving `V = 1056.3 cm^3`.

| File | Answer | First error | The mistake |
|---|---|---|---|
| `good_1.png` | **3.92 cm** | none | rejects the out-of-range root and runs the second-derivative test |
| `bad_1.png` | 7.85 cm | line 1 | `V = x(30 - x)(20 - x)`, forgetting both ends are cut |
| `bad_2.png` | 12.74 cm | line 7 | keeps the root outside the domain |
| `bad_3.png` | 3 cm | line 2 | lost the `(-2x)(-2x) = 4x^2` term when expanding |

**`bad_1`** is the classic setup error: a square is cut from **each** corner, so each side loses `2x`, not `x`. The calculus that follows is flawless. The root cause is modelling, not differentiation, and a tutor that responds by reviewing the product rule has missed the point entirely.

📌📌📌📌 **`bad_2` is the one to watch.** Every number on the page is correct. The quadratic is solved right, both roots are right. The student then picks `x = 12.74` because a bigger cut sounds like a bigger box. But `20 - 2(12.74) = -5.48`, so the box has a **negative side**. Nothing is arithmetically wrong; the answer is physically impossible. This tests whether the tutor sanity-checks a result or only re-derives it. 📌📌📌📌

**`bad_3`** expands `(30 - 2x)(20 - 2x)` as `600 - 60x - 40x = 600 - 100x`, dropping the `4x^2` term. It produces a clean, believable `x = 3`, which is exactly why the student never doubts it.

---

## Trigonometric equation

**Problem:** Solve `2 sin^2 x - 3 sin x cos x - cos^2 x = 0` for `0 <= x <= 360` degrees. **Correct answer:** `x = 60.68, 164.32, 240.68, 344.32` degrees.

The method: `cos x = 0` gives `2 = 0`, so it can be ruled out, and dividing through by `cos^2 x` turns the equation into `2 tan^2 x - 3 tan x - 1 = 0`, whose roots are `tan x = (3 ± sqrt17)/4`.

| File | Answer | First error | The mistake |
|---|---|---|---|
| `good_1.png` | **all four** | none | rules out `cos x = 0`, then takes both branches of each tangent |
| `bad_1.png` | two of four | **none** | stopped at the principal values |
| `bad_2.png` | 26.57, 45, 206.57, 225 | line 3 | discriminant `9 - 8` instead of `9 + 8` |
| `bad_3.png` | 45, 153.43, 225, 333.43 | line 2 | invented a factorization that does not expand back |

📌📌📌📌 **`bad_1` is the most valuable image in the entire kit, and it will probably break the current prompt.** The student writes `tan x = 1.7808`, `arctan(1.7808) = 60.68`, and stops. Then `arctan(-0.2808) = -15.68`, so `x = 164.32`. **Every one of those statements is true.** There is no wrong line to find. The mistake is the two solutions they never wrote: `240.68` and `344.32`.

`DIAGNOSIS_SYSTEM` in `backend/sensei/tutor.py` asks for "the line number where the first genuine mistake appears, or NONE". Against this image it should return `NONE`, and pass incomplete work as correct. If it does, the prompt needs a completeness check, not just a line-by-line check. Run this one first. 📌📌📌📌

**`bad_2`** computes the discriminant as `9 - 8 = 1`, forgetting that `-4ac` with `c = -1` **adds**. It gives suspiciously clean angles (`45`, `26.57`), and it takes both branches correctly, so the only fault is the arithmetic on line 3.

**`bad_3`** guess-factors as `(2 sin x + cos x)(sin x - cos x)`. That expands to a middle term of `-1 sin x cos x`, not `-3`. Everything after the bad factorization is handled correctly, including all four branches. Tests whether the tutor multiplies the factorization back out to check it.

---

# Geometry set

Grade 12. These are the only samples with a **figure**, so they test something nothing else in the kit does: whether the model can read a diagram and connect it to the working. The figures are drawn to scale from the real geometry, so the chord genuinely sits at `0.6r` and the cone is genuinely a 6-8-10 triangle. A model that measures the picture instead of reading the numbers still gets the right answer.

| Directory | Correct answer |
|---|---|
| `circle_geometry/` | `d = 6 cm`, `angle AOB = 106.26 deg` |
| `cone/` | `h = 8 cm`, area `96 pi cm^2`, volume `96 pi cm^3` |

Both verified symbolically. The two `96 pi` in the cone problem are a genuine coincidence of these numbers, not a typo.

---

## Circle geometry

**Problem:** circle of radius 10 cm, chord `AB = 16 cm`. Find the perpendicular distance from the centre to the chord, and the angle the chord subtends at the centre.

| File | Answer | First error | The mistake |
|---|---|---|---|
| `good_1.png` | **d = 6, AOB = 106.26** | none | bisects the chord, then `sin(AOM) = 8/10`, then doubles |
| `bad_1.png` | d = 12.8 | line 3 | Pythagoras added instead of subtracted |
| `bad_2.png` | AOB = 53.13 | last line | answered the half-angle |
| `bad_3.png` | d = 5.83 | line 5 | `100 - 64` read as `34` |

**`bad_1`** writes `OM^2 = OA^2 + AM^2`, giving `d = 12.8 cm`. The chord is inside a circle of radius 10, so a distance of 12.8 cm from the centre is **impossible**. Like the optimization sample, this tests whether the tutor sanity-checks a result rather than only re-deriving it. The angle work below the error is untouched and still correct.

**`bad_2`** is correct in every line until the last. The student computes `AOM = 53.13` and reports that as `AOB`, forgetting the perpendicular splits the angle in two. Extremely common, and the answer looks entirely plausible.

**`bad_3`** is a pure arithmetic slip in the middle of correct method. The tutor should fix the subtraction and leave the geometry alone.

📌📌📌📌 **There is a trap in this problem worth knowing about.** In right triangle `OMA` the right angle is at `M`, so for the angle **at O** the side `AM` is opposite, not adjacent: `sin(AOM) = 8/10`, giving `53.13 deg`. Writing `cos(AOM) = 8/10` returns `36.87 deg`, which is the angle at **A**. Both are angles in the same triangle and both look reasonable, so the wrong one does not announce itself. No sample uses this error yet; it would make a good extra variant if you want a harder one. (`bad_4` and `bad_5` are already taken by the drawing errors.) 📌📌📌📌

---

## Cone

**Problem:** right circular cone, radius 6 cm, slant height 10 cm. Find the vertical height, the total surface area, and the volume.

| File | Answer | First error | The mistake |
|---|---|---|---|
| `good_1.png` | **h = 8, 96 pi, 96 pi** | none | all three parts correct |
| `bad_1.png` | area = 60 pi | line 3 | "total" area given as the curved area only |
| `bad_2.png` | V = 120 pi | line 6 | slant height used in the volume |
| `bad_3.png` | h = 11.66 | line 1 | slant height treated as a leg, not the hypotenuse |

**`bad_1`** omits the base. `pi r l` is the curved surface; the question asks for the *total*, which needs `+ pi r^2`. The height above it and the volume below it are both correct, so the error sits in the middle of good work.

**`bad_2`** uses `l = 10` where the volume formula needs `h = 8`. This is the most common cone error there is, because both numbers are on the diagram and the student has just finished writing `10` several times.

**`bad_3`** fails on the very first line: it writes `h^2 = 10^2 + 6^2`. The slant height is the hypotenuse, so it cannot be a leg. Everything downstream is consistent with that wrong height.

---

---

## Figure errors: `bad_4`

Every question that carries a diagram has one extra variant, `bad_4`, where **the drawing is wrong and the working underneath is completely correct**. That is the realistic case: the student draws their own figure, so the figure is part of their answer and can be the only thing they got wrong.

Its working is copied verbatim from `good_1` by the render script, so the text is provably correct and the two cannot drift apart.

| Directory | What is wrong in the drawing | Working below |
|---|---|---|
| `math/advanced/circle_geometry/` | the foot of the perpendicular is drawn well off the midpoint, so `AM != MB` | correct, and says `AM = MB = 8` |
| `math/advanced/cone/` | `6 cm` marked right across the base, as a diameter rather than a radius | correct |
| `physics/advanced/projectile_cliff/` | the `v_x` and `v_y` labels swapped, so `v_x` names the vertical arrow | correct, and says `vOx = 25 cos35` |
| `physics/advanced/gravitation/` | both force arrows drawn toward the Moon | correct, and says they oppose |
| `chemistry/advanced/redox_magnesium/` | the ion drawn `Mg+` with one chloride, so an atom is lost and the charge is wrong | correct, and says `Mg -> Mg2+ + 2e-` |
| `chemistry/advanced/oxidation_ethanol/` | ethanal drawn keeping the `-OH`, giving that carbon five bonds | correct |

A second figure-error variant, `bad_5`, does the same again with a different drawing mistake:

| Directory | What is wrong in the `bad_5` drawing |
|---|---|
| `math/advanced/circle_geometry/` | the chord drawn tilted, so the line from O plainly is not perpendicular to it, yet the right-angle tick is still marked |
| `math/advanced/cone/` | the right angle marked at the **apex**, where there is none |
| `physics/advanced/projectile_cliff/` | the arc drawn landing back at cliff height, as if the 20 m drop were not there |
| `physics/advanced/gravitation/` | the satellite drawn a third of the way out, contradicting its own `3.0` of `3.84` labels |
| `chemistry/advanced/redox_magnesium/` | magnesium drawn already as `Mg2+` in the **Before** row, i.e. the product state before the reaction |
| `chemistry/advanced/oxidation_ethanol/` | ethanol's methyl carbon drawn with one H missing, leaving it with three bonds |

Both variants take their working verbatim from `good_1`.

**Expected diagnosis:** every written line is right, so a model that only checks the working will return `FIRST_ERROR: NONE` and pass the page. Catching these requires reading the picture and comparing it with the text. No other sample in the kit tests that.

The `ques.png` for these questions carries **no diagram**, because the question asks the student to draw one.


---

## Grading conventions

Some samples have more than one defensible right answer. These rules exist so a good diagnosis is not scored wrong.

- **Figure errors (`bad_4`, `bad_5`): grade on "did the model flag the drawing", not on whether it named the exact fault.** A wrong drawing often has several true descriptions at once. Tilting a chord also moves its endpoints; shifting the foot of a perpendicular also makes it non-perpendicular. Any of those is a fair thing for a tutor to point at.
- **When an error spans a formula line and its substitution line, accept a box on either.** The student wrote one mistake across two lines.
- **Grade on the flagged line's content, not the integer the model prints.** The images carry no line numbers.
- **`advanced/trig_equation/bad_1` has two correct answers. Accept either.** Every written line is true and the answer is incomplete, so `FIRST_ERROR: NONE` with `answer_complete: false` is right. But the final line `x = 60.68 and 164.32 degrees` is also false *as an answer* to "solve for all x", so flagging it is right too, and a model that boxes it and asks "are those the only ones?" is giving the best tutoring response available. For the bounding box: either no box, or a box on that final line.
- **`advanced/area_between_curves/bad_1`: accept line 4 or line 8.** Line 4 is the first false statement (the reversed subtraction) and is the better answer because it names the cause. Line 8, `A = -9/2`, is the louder symptom and the one the lesson is written around.


## Running them

Paths below are relative to the repo root, so run these from there. Send a `good_N` or `bad_N` image, never `ques.png`, which has no working to diagnose.

```bash
curl -s -F learner_id=demo \
     -F "problem=Solve 5 - (2x - 4) = 11" \
     -F image=@samples/math/basic/easy/bad_1.png \
     http://localhost:8080/tutor/diagnose | jq
```

Run `good_1.png` through the same command before every demo. It is the fastest check that the tutor is not inventing errors.

Passing `problem=` is optional but makes the transcription noticeably steadier, because the model can check what it reads against what was asked. Use it on camera.

## A note on the knowledge graph

`backend/fixtures/hsc_physics_bn.json` gives physics real concept ids, so the root-cause walk in `graph.py` runs for the physics samples today. There is no math graph in the repo yet. To make the root-cause beat work for these three, feed a math syllabus to `POST /curriculum/build` and use the ids it produces. The concepts named above are what that graph needs to contain.

## Sources

- [Schechter (Vanderbilt): The most common errors in undergraduate mathematics](https://math.vanderbilt.edu/schectex/commerrs/)
- [LibreTexts: Common Mistakes in Algebra](https://math.libretexts.org/Courses/Northern_Illinois_University/Conceptual_Mathematics_in_Society/04:_Algebra/4.08:_Common_Mistakes_in_Algebra)
- [Mathnasium: 5 Most Common Algebra Test Mistakes](https://www.mathnasium.com/math-centers/lakewoodco/news/common-algebra-mistakes)
- [eTutorWorld: Top 10 Algebra Mistakes Students Make](https://www.etutorworld.com/blog/top-10-algebra-mistakes-students-make-and-how-to-avoid-them/)
- [Students' common errors in quadratic equations (Infinity, journal PDF)](https://www.e-journal.stkipsiliwangi.ac.id/index.php/infinity/article/download/3843/1909)
- [Khan Academy: Examining common chain rule misunderstandings](https://www.khanacademy.org/math/ap-calculus-ab/ab-differentiation-2-new/ab-3-1a/v/common-chain-rule-misunderstandings)
- [Paul's Online Notes: Chain Rule](https://tutorial.math.lamar.edu/classes/calci/chainrule.aspx)
