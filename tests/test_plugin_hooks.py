"""Standalone tests for the Hermes plugin hook handlers (no Hermes needed)."""
import os
import sys
import types
from pathlib import Path

from boster import safe_compress

HERE = Path(__file__).resolve().parent
PLUGIN_DIR = HERE.parent / "hermes_plugin"
sys.path.insert(0, str(PLUGIN_DIR))

# Stub hermes_constants so _log() resolves without a live Hermes install.
_stub = types.ModuleType("hermes_constants")
_stub.get_hermes_home = lambda: str(Path(os.environ.get("TMP", "/tmp")))
sys.modules["hermes_constants"] = _stub

from __init__ import register  # noqa: E402


def _collect_hooks():
    h = {}
    register(type("C", (), {"register_hook": lambda self, n, f: h.setdefault(n, f)})())
    return h


HOOKS = _collect_hooks()


def test_registers_both_hooks():
    assert "transform_terminal_output" in HOOKS
    assert "transform_tool_result" in HOOKS


def test_tool_result_pass_thru_clean_code():
    code = "def f():\n    x = 1\n    x = 1\n    # ----\n    return x\n"
    assert HOOKS["transform_tool_result"](tool_name="read_file", result=code) is None


def test_tool_result_non_string_untouched():
    assert HOOKS["transform_tool_result"](tool_name="x", args={}, result={"a": 1}) is None


def test_tool_result_short_untouched():
    assert HOOKS["transform_tool_result"](tool_name="x", result="tiny") is None


def test_tool_result_strips_ansi_big_saving():
    # Large ANSI-bloated blob that safe_compress shrinks well past the 10% gate.
    noisy = "\x1b[31m" * 200 + ("ERROR boom\n" + "\x1b[0m" * 200 + "a\r\n") * 30
    out = HOOKS["transform_tool_result"](tool_name="run", result=noisy)
    assert isinstance(out, str)
    assert "\x1b" not in out
    assert "boom" in out


def test_tool_result_disable_env():
    os.environ["BOSTER_DISABLE"] = "1"
    try:
        assert HOOKS["transform_tool_result"](tool_name="x", result="\x1b[31m" * 200) is None
    finally:
        os.environ.pop("BOSTER_DISABLE", None)


def test_safe_compress_exported():
    assert callable(safe_compress)
