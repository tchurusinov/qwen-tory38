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
    containment — not equality — is the match rule). The match is
    TOLERANT of conjunction / word-boundary phrasing (the 2026-09-27
    post-live-run amendment, H8: the live M5 miss was golden "M1, M2,
    M7" vs answer "M1, M2, and M7" — a conjunction the strict substring
    containment did not tolerate, while the long-context read itself
    worked): common particles (and/or/the/a/an) are dropped from both
    sides, and the rule is ordered containment OR every golden token
    present in the answer's token set. A missing fact still fails (the
    token-set half is strict on missing tokens).
  * normalization: case-fold, drop the conjunction/word-boundary
    particles, keep only alphanumerics + underscores + commas + spaces
    collapsed (the golden answers are short tokens: "PTQ_1_0", "80",
    "M1, M2, M7" — the normalization is tolerant of case, surrounding
    punctuation, extra whitespace, and "and"-phrasing, but NOT of a
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
# H8 (post-live-run): the conjunction / word-boundary particles dropped
# from both sides before matching (the live miss: "M1, M2, M7" vs
# "M1, M2, and M7" — the "and" the strict containment did not tolerate).
# Matched as whole words only (a value like "an80" keeps its "an" — the
# _NORM_RE already bounds tokens by punctuation/word characters).
_STOP = frozenset({"and", "or", "the", "a", "an"})
# G3c round-2 P2 (H8 hardening): token extraction for the H8 tolerant
# half. The normalized string keeps commas ("m1, m2, m7"), and a naive
# split() would leave the comma attached to the token ("m1," != "m1"),
# so the token-set half only matched when the answer used the EXACT
# same comma placement — the live regression was saved only because the
# conjunction was "and" and the tokens happened to align. Extracting the
# alnum/underscore runs treats commas as word boundaries, so
# "M1, M2, M7" and "M1 M2 M7" tokenize identically.
_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _normalize(s: str) -> str:
    s = (s or "").casefold()
    s = _NORM_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def _tokens(s: str) -> set[str]:
    # G3c round-2 P2: extract the alnum/underscore runs (commas and the
    # remaining punctuation are word boundaries), then drop the stop
    # particles. A naive s.split() would keep the comma attached to the
    # token ("m1," != "m1"), so the token-set half only matched when the
    # answer used the exact same comma placement.
    return {t for t in _TOKEN_RE.findall(s) if t not in _STOP}


def _match(answer: str, golden: str) -> bool:
    a = _normalize(answer)
    g = _normalize(golden)
    if not g:
        return False
    # exact match or containment (the subagent may pad the answer).
    if a == g or g in a:
        return True
    # H8 tolerant half: conjunctions / word-boundary phrasing. Drop the
    # stop particles and require every golden token in the answer's token
    # set. Ordered containment above remains the strict special case.
    g_tokens = _tokens(g)
    if not g_tokens:
        # a golden that is ALL stop words (e.g. "an") — the token-set half
        # is vacuous, so fall back to a WHOLE-WORD containment (a bare
        # stop-word golden must match "an" as a word, not as a substring
        # of "banana"; G3c round-1 P2). No golden answer is a bare stop
        # word today, so this only hardens the degenerate path.
        return re.search(r"(?<!\w)" + re.escape(g) + r"(?!\w)", a) is not None
    return g_tokens <= _tokens(a)


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
