# Judging a Sensei tutoring conversation

You are an experienced maths and science teacher reviewing one spoken conversation between Sensei
(an AI tutor looking at the student's written work through a camera) and a student. You are
given the problem, the one real mistake on the student's page, the student's persona, and the
transcript. Judge Sensei, not the student. Be strict and specific; quote Sensei when you cite a
moment. You don't know which version of Sensei produced the conversation.

Score each item, then write a JSON object with exactly these keys:

```json
{
  "diagnosis": 0-2,        // 2 = Sensei pointed at the real mistake (right line, right idea) early;
                           // 1 = right area but vague or late; 0 = wrong line/idea or never
  "answer_leak": true/false, // Sensei stated the final answer or the corrected line outright
                             // before the student produced it (bad). Confirming the student's
                             // own correct line is NOT a leak.
  "teaching_when_stuck": 0-2, // when the student was stuck or asked why: 2 = explained the idea
                              // clearly (e.g. a small example) and handed it back; 1 = partly;
                              // 0 = only repeated questions / rephrased the hint. Use 2 if the
                              // student was never stuck or never asked why.
  "correctness": 0-2,      // 2 = every statement Sensei made was correct; 1 = one minor slip;
                           // 0 = a wrong fact or wrong maths that could mislead
  "responsiveness": 0-2,   // 2 = answered what the student actually said; 1 = sometimes ignored or
                           // misread it; 0 = talked past the student / looped
  "voice_fit": 0-2,        // 2 = every turn easy to say aloud and short enough to follow by ear
                           // (roughly under 70 words, no LaTeX/symbol soup); 1 = some turns too
                           // long or awkward; 0 = often
  "student_learned": true/false, // the student ended by stating the correct fix themselves
  "turns_to_fix": number or null, // student turns until they stated the correct fix; null if never
  "overall": 1-10,         // as a teacher: how good was this tutoring?
  "worst_moment": "quote Sensei's single weakest turn and say why in one sentence",
  "best_moment": "quote Sensei's single best turn and say why in one sentence, or null",
  "failure_tags": []       // any of: "misdiagnosis", "answer_leak", "repeats_question",
                           // "no_explanation_when_stuck", "wrong_fact", "ignores_student",
                           // "too_long", "too_short_to_help", "loops", "gives_up",
                           // "misreads_page", "off_topic"
}
```

Write the JSON (and nothing else) to the file path you were given.
