"""tests — the hermetic test suite package.

Package marker (tests/fixtures/ imports use the in-package relative
form, `from .fixtures...` — the tests/test_smoke.py AST check skips
relative imports by construction). The @gpu target (tests/gpu/) is a
separate discover root with its own conventions (S6.6 runtime deps
allowed); it stays a plain dir.
"""
