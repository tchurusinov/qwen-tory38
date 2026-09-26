"""bench.score.goldens — load the S5.3 golden fixtures from
``bench/goldens/``.

The S5.3 rubric rule: "golden files in ``bench/goldens/``" — the M4 seeded
10-bug diff, the M5 QA golden, and the M1 code-gen 3-case suite are
versioned in the repo as fixtures (not live diffs / live endpoints). This
module loads them so the scorer plugins (and the S5.8 hermetic rubric
tests) share ONE source of truth.

Layout (each rubric a subdir of ``bench/goldens/``):

  m1/
    module.py         # the golden code-gen module (the expected shape)
    test_module.py    # the golden 3-case test suite (what M1 scores)
  m2/
    expected_report.json   # the golden test-batch report (the 10-file shape)
  m4/
    diff.patch          # the seeded 10-bug diff (the fixture)
    bugs.json           # the 10 seeded bug ids + locations (the ground truth)
  m5/
    doc.json            # the golden 60K-token doc (a compact stand-in)
    qa.json             # the 5 QA questions + their golden answers

All files are read-only fixtures. ``load(rubric, name)`` returns the file
bytes; ``load_json`` / ``load_text`` are the typed conveniences. A missing
file raises FileNotFoundError (the runner's config path is upstream — the
goldens_dir resolvability is checked by the S5.8 config test).

Stdlib-only: ``pathlib`` + ``json``.
"""

from __future__ import annotations

import json
from pathlib import Path

# The default goldens dir (repo-relative; the S5.7 ``goldens_dir`` key
# points here at test time, /opt/bonsai2/share/bench/goldens at install).
DEFAULT_GOLDENS_DIR = Path(__file__).resolve().parents[1] / "goldens"


def goldens_dir(root: str | Path | None = None) -> Path:
    """The goldens root (override for hermetic tests pointing at a tmp
    copy). Defaults to the repo's ``bench/goldens/``."""
    return Path(root) if root is not None else DEFAULT_GOLDENS_DIR


def load(rubric: str, name: str, root: str | Path | None = None) -> bytes:
    """The raw bytes of ``bench/goldens/<rubric>/<name>``."""
    p = goldens_dir(root) / rubric / name
    if not p.is_file():
        raise FileNotFoundError(f"golden missing: {p}")
    return p.read_bytes()


def load_text(rubric: str, name: str, root: str | Path | None = None) -> str:
    return load(rubric, name, root).decode("utf-8")


def load_json(rubric: str, name: str, root: str | Path | None = None):
    return json.loads(load(rubric, name, root))


def m1_module_source(root: str | Path | None = None) -> str:
    """The M1 golden code-gen module source."""
    return load_text("m1", "module.py", root)


def m1_test_source(root: str | Path | None = None) -> str:
    """The M1 golden 3-case test suite source."""
    return load_text("m1", "test_module.py", root)


def m4_bugs(root: str | Path | None = None) -> list:
    """The M4 seeded 10-bug ground truth (a list of {id, file, line, desc}).
    The fixture is a document ({scenario, description, bugs: [...]});
    this returns the ``bugs`` list (a bare list fixture is passed through)."""
    data = load_json("m4", "bugs.json", root)
    if isinstance(data, dict):
        bugs = data.get("bugs")
        if not isinstance(bugs, list):
            raise FileNotFoundError(
                f"golden m4/bugs.json has no 'bugs' list: {sorted(data)}"
            )
        return bugs
    return data


def m4_diff(root: str | Path | None = None) -> str:
    """The M4 seeded 10-bug diff text (the fixture the subagent reviews)."""
    return load_text("m4", "diff.patch", root)


def m5_qa(root: str | Path | None = None) -> dict:
    """The M5 QA golden: {"doc": str, "questions": [q, ...],
    "answers": [a, ...]} (5 pairs, binary-scored)."""
    return load_json("m5", "qa.json", root)
