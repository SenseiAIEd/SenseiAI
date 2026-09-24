# Sensei Eyes+Brain — research briefs (Log / Scout)

Collected Wed Sep 23 while Spark key blocked. Primary Fri plant: `physics/basic/medium`.

## 1) Capture / OCR (phone overhead)
- Photo quality often fails before the model (glare, tilt, tiny marks; `5`↔`x` / invent strokes).
- Fix: square-on, even light, deskew, large dark ink, tight crop, Hint/Check over voice-in.
- Two-pass: read glyphs → then assess (never see-and-tutor in one shot).
- Primary: https://aclanthology.org/2026.alvr-main.23.pdf (FADE)
- OpenJev force-fit as math "mistake?" gate: rejected (wrong domain).

## 2) Socratic offline loop
- Detect first wrong step → stay quiet → on-demand Hint → Check.
- On-demand hints beat auto-hints (Razzaq & Heffernan ITS 2010).
- Primary: https://web.cs.wpi.edu/~nth/pubs_and_grants/ITS%202010/RazzaqHintsIsItBettertoGiveorWaittobeAsked.pdf

## 3) No spoon-feed + repos
- First-error verification then targeted tutor (Daheim EMNLP 2024): https://arxiv.org/abs/2407.09136
- Repos: eth-lre/verify-then-generate, breezedeus/Pix2Text, eth-nlped/mathdial
- Extra: lukas-blecher/LaTeX-OCR, opendatalab/UniMERNet, OpenBMB/MiniCPM-V, CAHLR/OATutor
- Gap: no solid open FBD/vectors grader — measure with physics/basic/medium
- Avoid: Photomath-style answer dumpers

## Tutoring locks (apply)
IDLE → MISTAKE_SEEN (quiet) → HINT (one Socratic Q, no answer leak) → CHECK.
Fri ASR voice-in OFF; short TTS voice-out OK if non-leaking.
