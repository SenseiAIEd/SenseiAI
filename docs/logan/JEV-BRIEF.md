# Scout — Hosted Jev labels + Fri-safe plant gate

CLAIM: Public Jev/OpenJev is a caller-defined typed decision API (choice/score/noul), not a fixed tutoring taxonomy. Map SenseiAI `error_kind` + speak/quiet onto Jev questions; keep Check on `first_error` null/non-null; plant `v_y=20` first; Jev only as soft second opinion.

LIVE: brain = instruct GGUF (not thinking). Jev on Spark = hosted `jev-latest` (not yet in git).

## Labels (map to tutor.py)

| Jev question | Kind | Sensei map |
|--------------|------|------------|
| error_kind | choice: sign \| arithmetic \| rule \| concept \| copying \| none | Assessment.error_kind |
| has_first_error | noul | soft only — does NOT replace Check |
| dialog_act | choice: quiet \| hint_ask \| check_verdict \| look_describe | speak policy |
| ask_vs_quiet | noul | gate before speak/TTS |
| confidence_soft | score low\|med\|high | when to trust VL vs plant |

Check: `first_error` null = pass; non-null = fail. On-track → `say` null.

## Fri-safe use

1. Typed/plant gate first for physics `v_y=20`
2. VL Check binary stays authoritative
3. Jev second opinion only if VL soft — never override Check
4. Ground: verify-then-generate (Daheim EMNLP 2024)

## Instruct GGUF vs thinking (qualitative)

- Instruct LIVE: faster JSON / better demo latency; higher miss risk on subtle plant
- Thinking: better multi-step catch chance; latency + talky `say` (strip_reasoning)
- Prefer miss over false alarm on good_1 (Sensei prompt)

## Five follow-up ask rules

1. Quiet until Hint tap
2. One Socratic Q only — never corrected/next line
3. No answer dump in `say`; voice OFF → keep short
4. Check ≠ Hint
5. Prefer miss over false alarm on good_1

## Sources

- https://raw.githubusercontent.com/SenseiAIEd/SenseiAI/main/gateway/tutor.py
- https://typesafe.ai/blog/introducing-system-one-models-and-jev
- https://openjev.sh/docs
- https://arxiv.org/html/2407.09136v1

## Risks

Jev wrong-but-type-valid · hosted OpenJev may be text-only (no images) · instruct miss on plant · Jev replacing Check → false fail/pass
