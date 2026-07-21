"""Unit tests for the hybrid Docker dependency probe."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "docker"
    / "scripts"
    / "verify_hybrid_deps.py"
)


def _load():
    import sys

    if not _SCRIPT.is_file():
        pytest.skip(f"missing {_SCRIPT}")
    spec = importlib.util.spec_from_file_location("verify_hybrid_deps", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # dataclasses looks up cls.__module__ in sys.modules during decoration.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_probes_include_historically_missing_deps() -> None:
    mod = _load()
    names = {p.module for p in mod.PROBES}
    # Regression guards for the mid-chat ModuleNotFoundError class.
    for required in ("orjson", "cryptography", "pdfplumber", "mcp", "dspy"):
        assert required in names
        probe = next(p for p in mod.PROBES if p.module == required)
        assert probe.required is True


def test_required_failures_exit_nonzero(monkeypatch) -> None:
    mod = _load()

    def fake_probe(p):
        if p.module == "orjson":
            return False, "ModuleNotFoundError: No module named 'orjson'"
        return True, "ok"

    monkeypatch.setattr(mod, "_probe_one", fake_probe)
    results = mod.run_probes(check_browsers=False)
    missing = [r for r in results if r["required"] and not r["ok"]]
    assert any(r["module"] == "orjson" for r in missing)
    assert mod.main([]) == 1
    assert mod.main(["--soft"]) == 0
