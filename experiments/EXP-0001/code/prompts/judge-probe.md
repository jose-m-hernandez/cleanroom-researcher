You label answers to memory questions. Reply only with JSON that matches the schema.

Case id: {case_id}

Below is one block per answer, each with its question, gold answer, stale answers and the model's answer. Label each answer on its own, using only its own block.

{probes}

Labels:
- correct: the answer states the gold answer (the wording may differ) and does not present a stale answer as current.
- incorrect: the answer states a different answer, or presents a stale answer as current.
- abstained: the answer says it does not know or cannot say, and gives no answer.

Return one object in "labels" for every answer: the answer's id exactly as given, its label, and at most one short span copied verbatim from that answer as "quote" (use "" if none). Do not explain.
