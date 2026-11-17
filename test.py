"""Compatibility test entry point; use `python -m pytest` for the full suite."""

import sys

import pytest

if __name__ == "__main__":
    raise SystemExit(pytest.main(sys.argv[1:] or ["-q"]))
