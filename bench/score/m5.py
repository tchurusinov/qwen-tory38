"""bench.score.m5 — the M5 rubric: "5 QA questions, each binary-scored by
a golden file (auto)".

Input (the runner's QA payload for one M5 task):
  {"answers": [<str>, ...]}
  ``answers`` — the subagent's answers to the 5 S5.2 QA questions (in the
  order of ``bench/goldens/m5/qa.json`` ``questions``).

Scoring (auto, per-question binary — S5.3 "machine-scored"):
  * each question is scored 0/1 against the golden answer in
    ``bench/goldens/m5/qa.json``: PASS when the normalized golden answer
    appears in the normalized subagent answer (exact match is a special
    case of containment; the subagent may pad the answer with prose, so
    containment — not equality — is the match rule).
  * normalization: case-fold, keep only alphanumerics + underscores +
    commas + spaces collapsed (the golden answers are short tokens:
    "PTQ_1_0", "80", "M1, M2, M7" — the normalization is tolerant of
    case, surrounding punctuation, and extra whitespace, but NOT of a
    different value).
  * ``pass`` = all 5 questions correct (the S5.2 "5 QA questions, each
    binary-scored" rubric: the long-context read is right when all 5 of
    the scattered facts are retrieved).
  * ``score`` = correct/5 (the S5.3 rubric score, [0,1]).

The hermetic S5.8 golden in/out pair: the 5 golden answers -> pass (1.0);
4 of 5 -> fail (0.8).

Stdlib-only: the goldens loader + the shared verdict helper.
"""

from __future__ import annotations

import re

from bench.score import goldens
from bench.score._common import verdict

# The S5.2 M5 rubric: "5 QA questions, each binary-scored" — the count is
# part of the rubric contract, not an emergent property of the golden.
EXPECTED_QUESTIONS = 5

_NORM_RE = re.compile(r"[^a-z0-9_,\s]+")
_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    s = (s or "").casefold()
    s = _NORM_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _match(answer: str, golden: str) -> bool:
    a = _normalize(answer)
    g = _normalize(golden)
    if not g:
        return False
    # exact match or containment (the subagent may pad the answer).
    return a == g or g in a


def score(payload: dict, root=None) -> dict:
    """Score one M5 QA payload. See the module docstring for the payload
    shape. ``root`` is the goldens dir override (the hermetic S5.8 test
    points at a tmp copy)."""
    answers = payload.get("answers")
    if not isinstance(answers, list):
        return verdict(False, 0.0, "M5: answers is not a list")
    try:
        qa = goldens.m5_qa(root)
    except FileNotFoundError as exc:
        return verdict(False, 0.0, f"M5: golden QA missing: {exc}")
    golden_answers = qa.get("answers") or []
    n = len(golden_answers)
    if n == 0:
        return verdict(False, 0.0, "M5: no golden questions", {"correct": 0})
    if n != EXPECTED_QUESTIONS:
        # the S5.2 "5 QA questions" rubric contract: the golden carries a
        # different question count -> a named fail (the rubric does not
        # silently adapt to a mis-specified golden).
        return verdict(
            False,
            0.0,
            f"M5: golden carries {n} questions, rubric expects "
            f"{EXPECTED_QUESTIONS} (S5.2 '5 QA questions')",
            {"correct": 0, "n_questions": n},
        )
    correct = 0
    per_q = []
    questions = qa.get("questions") or []
    for i in range(n):
        ans = answers[i] if i < len(answers) else ""
        ok = _match(str(ans), str(golden_answers[i]))
        q_text = questions[i] if i < len(questions) else ""
        per_q.append({"question": q_text, "ok": ok})
        if ok:
            correct += 1
    ok_all = correct >= n
    return verdict(
        ok_all,
        correct / n,
        f"M5: {correct}/{n} QA questions correct "
        f"({'all 5' if ok_all else 'incomplete'})",
        {"correct": correct, "n_questions": n, "per_question": per_q},
    )
