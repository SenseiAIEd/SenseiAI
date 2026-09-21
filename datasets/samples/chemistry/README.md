# Chemistry: the mistakes students actually make

Demo data for `/tutor/diagnose`, laid out as:

```
samples/chemistry/
  basic/      easy/  medium/  hard/
  advanced/   redox_magnesium/  oxidation_ethanol/    <- both carry figures
```

Every `bad_N` carries **exactly one** deliberate error, and every line after that error is consistent with it. That is on purpose: with two errors in an image, a diagnosis that names the second one first is not wrong, so the sample cannot be graded.

The `good_N` images are not filler. **A tutor that finds an error in correct work is worse than one that misses an error**, because it teaches the student out of something they already had right.

Text and numbers come from `scripts/render_samples.py`. Change a number there and change it here too.

The student work is rendered in a **handwriting font with per-character jitter**, so it is a closer proxy for the photos you will actually shoot. `ques.png` stays in a printed font: it is the worksheet, not the student.

**Figures never contain answers.** Every figure carries only what the question supplies (given lengths, masses, species, structures) plus symbols for the unknowns (`d`, `h`, `H`, `R`, `F_E`). Nothing a student has to work out appears on the diagram. This matters twice over: a label like a functional-group name would answer the question for the student, and it would let a model catch a `bad_N` by spotting two lines that disagree rather than by knowing the subject.

**The images carry no line numbers**, because students do not number their steps and a photo of real handwriting will not either. Line numbers below are a reading aid for you, not something the model can see. Grade a diagnosis on **which line's content** it flags, not on the integer it prints.

## How these three were chosen

We did not invent plausible-looking errors. We searched what chemistry teachers and chemistry-education researchers report as the highest-frequency student mistakes, then picked the top one at each level. Sources are listed at the bottom.

Two filters were applied to every candidate:

1. **Frequency.** It must appear on multiple independent "most common mistakes" lists, or in chemistry-education research.
2. **Visible in written work.** The mistake must show up as a wrong line on paper, not just a wrong final answer.

## The most common chemistry mistakes, ranked

| # | Mistake | Why it is so common | Used here |
|---|---|---|---|
| 1 | Balancing by changing a subscript instead of a coefficient | The atom count really does come out equal, so the check passes | **Easy** |
| 2 | Limiting reagent picked by raw amount, without dividing by the coefficient | "Which do I have less of" is an obvious question and an almost-right one | **Medium** |
| 3 | Celsius used in a gas law instead of Kelvin | The number is right there in the problem and looks ready to use | **Hard** |
| 4 | Grams used directly in a mole ratio, with no conversion to moles | Coefficients look like they could apply to any quantity | not used |
| 5 | Atomic mass used where the molecule is diatomic (O = 16 instead of O2 = 32) | The element symbol and the molecule share a name in speech | not used |
| 6 | Rounding after every step instead of once at the end | Each step looks tidy, and the error only shows up in the final digit | not used |
| 7 | The wrong value of R for the pressure units in use | R has several values and they differ by orders of magnitude | not used |

Mistakes 4 to 7 are real and well documented. They are left out only because three samples is the demo budget. Research on stoichiometry consistently names the same cluster: the mole concept, balancing, picking the limiting reagent, and theoretical yield.

---

## Easy: balanced by changing a subscript

**Files:** `samples/chemistry/basic/easy/ques.png` (problem) and `samples/chemistry/basic/easy/bad_1.png` (the work below)

**Problem:** Balance `H2 + O2 -> H2O`

**What the student wrote:**

```
H2 + O2  ->  H2O2
Check:   H: 2 = 2,   O: 2 = 2
Balanced.
```

**First error: line 1,** `H2 + O2  ->  H2O2`. The subscript was changed, not the coefficient. That does not balance water. It replaces water with hydrogen peroxide, a different substance.

**Correct answer:** `2 H2 + O2 -> 2 H2O`.

**Why students do this:** **line 2 is true.** H2O2 really does have 2 hydrogen and 2 oxygen, so the student's own check confirms the answer. This is the best example in the whole kit, because the mistake survives the test the student was taught to run. Education research reports it directly: students do not distinguish a coefficient (how many molecules) from a subscript (what the molecule is).

This sample also tests something specific about the model. The error is chemical, not arithmetic. A diagnosis that only checks the numbers will pass this image as correct.

**Concept:** balancing chemical equations. **Root cause:** what a chemical formula means.

**Expected diagnosis:** the model must flag `H2 + O2  ->  H2O2`, and the WHY must say the substance changed, not that the count is wrong.

**Socratic opening (do not state the error):** "Your count on line 2 is correct. So let me ask a different question: on line 1 you wrote H2O2. Is that still water?"

---

## Medium: limiting reagent chosen by raw moles

**Files:** `samples/chemistry/basic/medium/ques.png` (problem) and `samples/chemistry/basic/medium/bad_1.png` (the work below)

**Problem:** `2 H2 + O2 -> 2 H2O`. 6.0 g of H2 reacts with 64 g of O2. What mass of water forms?

**What the student wrote:**

```
n(H2) = 6.0 / 2.0 = 3.0 mol
n(O2) = 64 / 32 = 2.0 mol
O2 is limiting (fewer moles)
n(H2O) = 2 x 2.0 = 4.0 mol
m = 4.0 x 18 = 72 g
```

**First error: line 3,** `O2 is limiting (fewer moles)`. Moles cannot be compared directly, because the equation does not use them 1 to 1. Divide each by its coefficient first: H2 gives `3.0 / 2 = 1.5`, O2 gives `2.0 / 1 = 2.0`. The smaller one is H2, so **H2 is limiting**.

**Correct answer:** 54 g of water. The student got 72 g.

**Why students do this:** lines 1 and 2 are correct, and after that "fewer moles" feels like a complete argument. It is right whenever the coefficients happen to be equal, which covers a lot of textbook practice, so the habit gets reinforced before it gets tested. Research lists identifying the limiting reagent among the core stoichiometry difficulties, alongside the mole concept itself.

Note the trap in the numbers: H2 has *more* moles but is still the limiting reagent. That is only visible after dividing by the coefficient.

**Concept:** limiting reagent. **Root cause:** what a coefficient means as a mole ratio.

**Expected diagnosis:** the model must flag `O2 is limiting (fewer moles)`, and the WHY should mention the 2 to 1 ratio.

**Socratic opening:** "Lines 1 and 2 are exactly right. Line 3: the equation needs 2 H2 for every 1 O2. With 2.0 mol of O2, how much H2 does the reaction actually want?"

---

## Hard: Celsius used in the gas law

**Files:** `samples/chemistry/basic/hard/ques.png` (problem) and `samples/chemistry/basic/hard/bad_1.png` (the work below)

**Problem:** `CaCO3 -> CaO + CO2`. 25.0 g of CaCO3 decomposes completely. Find the volume of CO2 at 25 C and 1.00 atm. R = 0.0821 L atm / mol K.

**What the student wrote:**

```
M(CaCO3) = 40.1 + 12.0 + 3(16.0) = 100.1 g/mol
n(CaCO3) = 25.0 / 100.1 = 0.250 mol
n(CO2) = 0.250 mol      (1 : 1)
V = nRT / P = (0.250)(0.0821)(25) / 1.00
V = 0.513 L
```

**First error: line 4,** `V = nRT / P = (0.250)(0.0821)(25) / 1.00`. T must be in Kelvin. `25 C` is `298 K`, so line 4 should read `(0.250)(0.0821)(298) / 1.00`.

**Correct answer:** 6.12 L. The student got 0.513 L, about 12 times too small.

**Why students do this:** the temperature is printed in the problem as `25`, and `25` is what gets copied. The units of R say `mol K` right there on the page and still get skipped. Multiple sources call this the single most common gas-law error.

**This is the hard sample for a specific reason.** Lines 1, 2 and 3 are all correct, including a molar mass built from four atoms and a mole ratio. The model must verify three good lines before it reaches the bad one. A diagnosis that reports an error in lines 1 to 3 is a false positive, and that is the failure mode worth catching before the camera is on: a tutor that invents an error teaches the student out of something they already had right.

**Concept:** ideal gas law. **Root cause:** absolute temperature scale.

**Expected diagnosis:** the model must flag `V = nRT / P = (0.250)(0.0821)(25) / 1.00`, and the WHY should say Celsius was used where Kelvin is required.

**Socratic opening:** "Everything through line 3 is right. On line 4, look at the units of R that you wrote at the top. What unit does the K at the end demand for your temperature?"

---

# Advanced set

Grade 12, both with **figures**. `redox_magnesium/` carries a particle diagram of the beaker before and after; `oxidation_ethanol/` carries the three displayed structural formulas with the oxidation arrows between them.

The particle diagram is atom-balanced across the two rows (`Mg + 2H+ + 2Cl- -> Mg2+ + 2Cl- + H2`), so nothing appears or vanishes between "Before" and "After". A chemistry-literate model checking the picture against the equation will find them consistent.

| Directory | Correct answer |
|---|---|
| `redox_magnesium/` | `Mg + 2HCl -> MgCl2 + H2`, `n(H2) = 0.20 mol` |
| `oxidation_ethanol/` | hydroxyl, aldehyde, carboxylic acid; water in step 1 only |

---

## Magnesium and hydrochloric acid

**Problem:** Mg reacts with excess HCl. Balance the equation, give both half-equations, and find the moles of H2 from 4.8 g of Mg. `M(Mg) = 24.0`.

| File | Answer | First error | The mistake |
|---|---|---|---|
| `good_1.png` | **0.20 mol** | none | balanced, both half-equations charge-balanced, 1:1 ratio |
| `bad_1.png` | 0.20 mol | line 2 | `Mg -> Mg2+ + e-`, one electron instead of two |
| `bad_2.png` | 0.40 mol | line 5 | read the 2 in `2HCl` as the H2 ratio |
| `bad_3.png` | 115.2 mol | line 4 | multiplied by the molar mass instead of dividing |

📌📌📌📌 **`bad_1` is the one that will separate models.** The arithmetic is untouched and the final answer, 0.20 mol, is **correct**. The only fault is `Mg -> Mg2+ + e-`: charge is 0 on the left and +1 on the right, so it does not balance. A model that checks numbers will pass this page. Catching it requires reading the chemistry, and the figure deliberately does not state the electron count. 📌📌📌📌

**`bad_2`** is the trap the problem is built around. The `2` in `2HCl` is the acid's coefficient, not hydrogen's; Mg to H2 stays 1:1. Doubling to 0.40 mol is one of the most common stoichiometry errors there is, and the number looks entirely reasonable.

**`bad_3`** gives 115 mol of gas from 4.8 g of metal, which is not physically possible. Same shape as the optimization and gravitation samples: no step is miscalculated, the answer is just absurd.

---

## Oxidising ethanol

**Problem:** ethanol is oxidised to ethanal, then to ethanoic acid. Name each functional group and write both equations.

| File | Answer | First error | The mistake |
|---|---|---|---|
| `good_1.png` | **correct** | none | water in step 1 only |
| `bad_1.png` | names a ketone | line 2 | `-CHO` on a chain end is an aldehyde |
| `bad_2.png` | step 1 unbalanced | line 4 | water dropped from the first equation |
| `bad_3.png` | step 2 unbalanced | line 5 | water added to the second equation |

**`bad_1`** calls `-CHO` a ketone. A ketone has its `C=O` between two carbons, which needs at least three; ethanal has two. Purely a naming error, with every equation on the page correct, so it tests whether the model reads chemistry rather than checking arithmetic.

**`bad_2` and `bad_3` are a matched pair.** One drops the water that belongs in step 1; the other adds water to step 2 where none belongs, by symmetry with step 1. Both are visible by counting atoms: `bad_2` has 6 H on the left and 4 on the right, `bad_3` has 4 on the left and 6 on the right. Together they check whether a model balances equations or just recognises their shape.

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
     -F "problem=Balance H2 + O2 -> H2O" \
     -F image=@samples/chemistry/basic/easy/bad_1.png \
     http://localhost:8080/tutor/diagnose | jq
```

Passing `problem=` is optional but makes the transcription noticeably steadier, because the model can check what it reads against what was asked. Use it on camera.

## A note on the knowledge graph

`backend/fixtures/hsc_physics_bn.json` gives physics real concept ids, so the root-cause walk in `graph.py` runs for the physics samples today. There is no chemistry graph in the repo yet. To make the root-cause beat work for these three, feed a chemistry syllabus to `POST /curriculum/build` and use the ids it produces. The concepts named above are what that graph needs to contain.

This is also the cheapest place to stage the unrehearsed-syllabus stunt in `PLAN_WIN.md` section 9: chemistry is the subject we have samples for and no graph for, so building the graph live is a real build, not a replay.

## Sources

- [RSC Education: Students' difficulties with stoichiometry](https://edu.rsc.org/resources/students-difficulties-with-stoichiometry-beyond-appearances/4017805.article)
- [Students' misconceptions in stoichiometry (Academia.edu)](https://www.academia.edu/693170/STUDENTS_MISCONCEPTIONS_IN_STOICHIOMETRY)
- [Mind Matters Pedagogy: 4 mistakes students make when learning to balance equations](https://www.mindmatterspedagogy.com/post/4-mistakes-students-make-when-learning-to-balance-equations-and-how-you-can-address-them)
- [Challenges and Misconceptions in Applying Stoichiometry](https://www.solubilityofthings.com/challenges-and-misconceptions-applying-stoichiometry/)
- [RevisionDojo: IB Chemistry stoichiometry common mistakes](https://www.revisiondojo.com/blog/ib-chemistry-stoichiometry-common-mistakes)
- [Albert.io: Ideal Gas Law AP Chemistry review](https://www.albert.io/blog/ideal-gas-law-ap-chemistry-review/)
- [Chemistry LibreTexts: The Ideal Gas Law](https://chem.libretexts.org/Bookshelves/Physical_and_Theoretical_Chemistry_Textbook_Maps/Supplemental_Modules_(Physical_and_Theoretical_Chemistry)/Physical_Properties_of_Matter/States_of_Matter/Properties_of_Gases/Gas_Laws/The_Ideal_Gas_Law)
