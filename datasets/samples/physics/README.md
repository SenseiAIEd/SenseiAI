# Physics: the mistakes students actually make

Demo data for `/tutor/diagnose`, laid out as:

```
samples/physics/
  basic/      easy/  medium/  hard/
  advanced/   projectile_cliff/  gravitation/     <- both carry figures
```

`basic/` is three samples, one per difficulty; `advanced/` is two grade 12 problems with diagrams. Each one carries **exactly one** deliberate error, and every line after that error is arithmetically consistent with it. That is on purpose: with two errors in an image, a diagnosis that names the second one first is not wrong, so the sample cannot be graded.

Text and numbers come from `scripts/render_samples.py`. Change a number there and change it here too.

The student work is rendered in a **handwriting font with per-character jitter**, so it is a closer proxy for the photos you will actually shoot. `ques.png` stays in a printed font: it is the worksheet, not the student.

**Figures never contain answers.** Every figure carries only what the question supplies (given lengths, masses, species, structures) plus symbols for the unknowns (`d`, `h`, `H`, `R`, `F_E`). Nothing a student has to work out appears on the diagram. This matters twice over: the same figure is drawn on `ques.png`, so a label like a functional-group name would make the worksheet answer its own question, and it would let a model catch a `bad_N` by spotting two lines that disagree rather than by knowing the subject.

**The images carry no line numbers**, because students do not number their steps and a photo of real handwriting will not either. Line numbers below are a reading aid for you, not something the model can see. Grade a diagnosis on **which line's content** it flags, not on the integer it prints.

## How these three were chosen

We did not invent plausible-looking errors. We searched what physics teachers and physics-education researchers report as the highest-frequency student mistakes, then picked the top one at each level that a camera can actually see. Sources are listed at the bottom.

Two filters were applied to every candidate:

1. **Frequency.** It must appear on multiple independent "most common mistakes" lists, or in physics-education research.
2. **Visible in written work.** A misconception a student only holds in their head is useless to us. The mistake must show up as a wrong line on paper.

## The most common physics mistakes, ranked

| # | Mistake | Why it is so common | Used here |
|---|---|---|---|
| 1 | Unit conversion skipped (km/h left as km/h, minutes left as minutes) | The formula "works" with any number, so nothing feels wrong | **Easy** |
| 2 | Vector used whole instead of resolved into components | Students see one number in the problem and use that one number | **Medium** |
| 3 | Normal force taken as `mg` on a slope, instead of `mg cos(theta)` | "Normal" is learned as "upward" rather than "perpendicular to the surface" | **Hard** |
| 4 | Sign and direction convention flipped mid-problem | Up and down are chosen once, then forgotten | not used |
| 5 | Confusing mass with weight, speed with velocity, distance with displacement | Everyday language uses these words as synonyms | not used |
| 6 | An invented "force of motion" added to a free-body diagram | Belief that motion needs a continuing force (impetus) | not used |
| 7 | Centripetal force added as an extra force, on top of the real forces | It is taught as a formula before it is taught as a net force | not used |

Mistakes 4 to 7 are real and well documented. They are left out only because three samples is the demo budget. Items 6 and 7 need a free-body diagram to be visible, which is a harder image for the model to read reliably, so they are poor insurance samples.

---

## Easy: unit conversion skipped

**Files:** `samples/physics/basic/easy/` &mdash; `ques.png` (problem), `good_1.png` (correct), `bad_1.png` (the work below)

**Problem:** A car moves at 72 km/h. How far does it travel in 5.0 s?

**What the student wrote:**

```
v = 72 km/h
d = v x t
d = 72 x 5.0
d = 360 m
```

**First error: line 3,** `d = 72 x 5.0`. The speed is in km/h but the time is in seconds. `72 km/h` is `20 m/s`, so line 3 should be `d = 20 x 5.0`.

**Correct answer:** 100 m. The student got 360 m, off by a factor of 3.6.

**Why students do this:** the formula accepts any number. Nothing on the page looks broken, and the answer even comes out in a believable range. Lines 1 and 2 are perfectly correct, which is what makes this a fair test: the model must not stop at the first line it sees.

**Concept:** `linear_motion`. **Root cause:** `derived_units`. The student does not have a broken idea of motion. They have a broken idea of what a unit is.

**Expected diagnosis:** the model must flag `d = 72 x 5.0`, and the WHY should mention mixed units, not arithmetic.

**Socratic opening (do not state the error):** "Look at line 3. What unit of time is hiding inside `72 km/h`, and what unit of time is `5.0 s`?"

---

## Medium: the vector was never resolved

**Files:** `samples/physics/basic/medium/` &mdash; `ques.png` (problem), `good_1.png` (correct), `bad_1.png` (the work below)

**Problem:** A ball is launched at 20 m/s, 60 degrees above the horizontal. How long until it returns to launch height? (g = 9.8 m/s^2)

**What the student wrote:**

```
v_y = 20 m/s
t_up = v_y / g = 20 / 9.8 = 2.04 s
t_total = 2 x 2.04 = 4.08 s
```

**First error: line 1,** `v_y = 20 m/s`. `20 m/s` is the speed along the launch direction, not the vertical component. It should be `v_y = 20 sin(60) = 17.32 m/s`.

**Correct answer:** t_up = 1.77 s, t_total = 3.54 s. The student got 4.08 s.

**Why students do this:** the problem gives one speed, so the student uses that speed. The angle is read as scene-setting, not as data. Note that the method in lines 2 and 3 is completely right. This student understands projectile motion. They do not understand vectors.

**Concept:** `projectile_motion`. **Root cause:** `vector_resolution`.

**This is the demo sample.** It is the one that produces the line in the video script: *"You did not fail projectile motion. You failed vector decomposition, two prerequisites back."* The graph walk from `projectile_motion` to `vector_resolution` is real, and it is visible in `backend/fixtures/hsc_physics_bn.json`.

**Expected diagnosis:** the model must flag `v_y = 20 m/s`, and the WHY should say the vertical component was not taken.

**Socratic opening:** "Your method in lines 2 and 3 is exactly right. Go back to line 1: the ball is moving at 20 m/s, but is all of that motion upward?"

---

## Hard: normal force on a slope

**Files:** `samples/physics/basic/hard/` &mdash; `ques.png` (problem), `good_1.png` (correct), `bad_1.png` (the work below)

**Problem:** A 5.0 kg block slides down a 30 degree incline. The coefficient of kinetic friction is 0.20. Find the friction force. (g = 9.8 m/s^2)

**What the student wrote:**

```
N = mg = 5.0 x 9.8 = 49 N
f = 0.20 x N = 0.20 x 49
f = 9.8 N
```

**First error: line 1,** `N = mg = 5.0 x 9.8 = 49 N`. On a slope the normal force is perpendicular to the surface, not vertical. It should be `N = mg cos(30) = 42.4 N`.

**Correct answer:** 8.5 N. The student got 9.8 N. Only about 15% off, which is exactly what makes it dangerous: the answer looks reasonable, so the student never rechecks it.

**Why students do this:** "normal force" gets learned on flat tables, where `N = mg` is true. The word "normal" means perpendicular to the surface, but almost every early example makes perpendicular and vertical the same thing. When the surface tilts, the habit survives and the physics does not. Research on free-body diagrams reports the same thing: students draw the normal force straight up even when the surface is angled.

**Concept:** `friction`. **Root cause:** `vector_resolution`.

**Note this on purpose:** the medium and hard samples come from different chapters and have the *same* root cause. That is the strongest thing the knowledge graph can show. Run both photos for one learner and the tutor stops treating them as two bad days and starts treating them as one broken prerequisite.

**Expected diagnosis:** the model must flag `N = mg = 5.0 x 9.8 = 49 N`, and the WHY should mention the tilt of the surface.

**Socratic opening:** "Line 1 says the surface pushes with 49 N. Which direction does a ramp push a block, straight up or straight out of the ramp?"

---

# Advanced set

Grade 12, both with **figures**. The school set above only ever fails in one shape: one wrong line, everything after it consistent. These two fail differently, and they are the only physics samples that require reading a diagram.

Both figures are plotted from the real physics, not sketched. The trajectory arc is the actual parabola for 25 m/s at 35 degrees, and the satellite really does sit at 3.0/3.84 of the way from Earth to Moon. A model that measures the picture instead of reading the numbers still lands on the right answer.

| Directory | Correct answer |
|---|---|
| `projectile_cliff/` | `v0x = 20.48`, `v0y = 14.34`, `H = 30.49 m`, `t = 3.96 s`, `R = 81.1 m` |
| `gravitation/` | `F_E = 3.54 N`, `F_M = 0.556 N`, net `2.98 N` toward Earth |

All values verified symbolically.

---

## Projectile from a cliff

**Problem:** launched from a 20 m cliff at 25 m/s, 35 degrees above the horizontal, `g = 9.8`. Find the velocity components, the maximum height above the ground, the time of flight and the range.

| File | Answer | First error | The mistake |
|---|---|---|---|
| `good_1.png` | **30.49 m, 3.96 s, 81.1 m** | none | all four parts correct |
| `bad_1.png` | 41.40 m, 5.00 s, 71.7 m | line 1 | `sin` and `cos` swapped |
| `bad_2.png` | t = 2.93 s, R = 60.0 m | line 5 | used the symmetric range formula |
| `bad_3.png` | H = 10.49 m | line 4 | height given above the launch point, not the ground |

**`bad_1`** swaps the components on the very first line. Every step after it is correct method applied to the wrong two numbers, so the working looks disciplined all the way down.

**`bad_2`** uses `t = 2 v0y / g`, which assumes the ball lands at the height it left. It lands 20 m lower, so the real flight is longer. The components and the maximum height above it are both correct, putting the error in the middle of good work.

📌📌📌📌 **`bad_3` is the subtle one.** Only a single word is wrong: the student computes `h = 10.49 m`, which is the rise **above the launch point**, and labels it the height **above the ground**. The 20 m cliff is never added back. Time and range below it are both correct. Nothing is miscalculated; the answer is simply to a different question than the one asked. 📌📌📌📌

---

## Gravitation

**Problem:** an 800 kg satellite on the Earth-Moon line, `3.0 x 10^8 m` from Earth's centre, centres `3.84 x 10^8 m` apart. Find both forces and the net force.

| File | Answer | First error | The mistake |
|---|---|---|---|
| `good_1.png` | **3.54 N, 0.556 N, net 2.98 N** | none | subtracts, because the pulls oppose |
| `bad_1.png` | net = 3.51 N | line 2 | used the full Earth-Moon distance for `r_M` |
| `bad_2.png` | net = 4.10 N | line 4 | added two antiparallel forces |
| `bad_3.png` | F_E = 1.06 x 10^9 N | line 2 | forgot to square the distance |

**`bad_1`** takes `r_M = 3.84 x 10^8` instead of the satellite-to-Moon gap of `0.84 x 10^8`. The Moon's pull comes out roughly 20 times too small, and the net force barely changes from `F_E` alone, which is the clue.

**`bad_2`** adds the two forces. Earth pulls one way and the Moon the other, so they subtract. Everything above the last two lines is correct, and the figure shows the arrows pointing in opposite directions, so the diagram contradicts the arithmetic.

**`bad_3`** drops the square in the denominator, giving an 800 kg satellite a pull of `10^9 N`. Like the optimization and circle samples, no arithmetic is wrong; the answer is just physically impossible.

**A note on rounding.** `F_E` is `3.5395 N`, so it rounds to **3.54 N** and the net to **2.98 N**. Rounding the numerator to `3.18 x 10^17` before dividing gives `3.53` and a net of `2.97` instead. That is the documented "round after every step" error, and it would make a good extra variant if you want one that is wrong only in the last digit. (`bad_4` and `bad_5` are already taken by the drawing errors.)

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


## Running them

Paths below are relative to the repo root, so run these from there.

```bash
curl -s -F learner_id=demo \
     -F "problem=A car moves at 72 km/h. How far in 5.0 s?" \
     -F image=@samples/physics/basic/easy/bad_1.png \
     http://localhost:8080/tutor/diagnose | jq
```

Passing `problem=` is optional but makes the transcription noticeably steadier, because the model can check what it reads against what was asked. Use it on camera.

## Sources

- [K12 Tutoring: The Most Common Physics Mistakes High School Students Make](https://tutoring.k12.com/resources/tutoring-help/science-tutoring-help/physics/the-most-common-physics-mistakes-high-school-students-make/)
- [Matrix Education: 6 Common Mistakes HSC Physics Students Make in Exams](https://www.matrix.edu.au/6-common-mistakes-hsc-physics-students-make-in-exams/)
- [IOP Spark: Many students are unable to identify correctly the forces acting on each object](https://spark.iop.org/many-students-are-unable-identify-correctly-forces-acting-each-object-situation-where-two-or-more)
- [IOP Spark: Many students think the downward motion of a projectile will be affected by its horizontal motion](https://spark.iop.org/many-students-think-downward-motion-projectile-will-be-affected-its-horizontal-motion)
- [Free body diagrams, friction and normal force (College Physics, eCampusOntario)](https://ecampusontario.pressbooks.pub/physicsfundamentals/chapter/7-1-concepts-force-of-friction-and-normal-force-free-body-diagrams/)
- [Centripetal Acceleration: Often Forgotten or Misinterpreted (arXiv)](https://arxiv.org/pdf/1602.06361)
- [Central distractors in Force Concept Inventory data (Phys. Rev. Phys. Educ. Res.)](https://link.aps.org/doi/10.1103/PhysRevPhysEducRes.14.010106)
- [Prescott: Student Misconceptions about Projectile Motion (UTS)](https://opus.lib.uts.edu.au/bitstream/10453/7474/1/2005002166.pdf)
