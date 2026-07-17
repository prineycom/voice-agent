"""Tests for the whitelisted CLI worker tool (run_command).

No real subprocess is spawned — `asyncio.create_subprocess_exec` is monkeypatched
with a fake that records the argv it was called with and returns canned
stdout/stderr/returncode. The real `hermes` binary is never invoked, so these
tests are CI-friendly (no Hermes install, no Pi, no network).
"""

import asyncio
import pytest

import worker_tools
from worker_tools import run_command


class FakeProc:
    """Minimal stand-in for an asyncio.subprocess.Process."""

    def __init__(self, stdout: bytes, stderr: bytes, returncode: int):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True


@pytest.fixture
def fake_exec(monkeypatch):
    """Patch create_subprocess_exec to record calls and return FakeProcs.

    Usage: ``await fake_exec.set(stdout=b"...", stderr=b"...", returncode=0)``
    yields the recorded argv list via ``fake_exec.calls``.
    """
    calls: list[list[str]] = []
    next_result: dict = {"stdout": b"", "stderr": b"", "returncode": 0}

    class _Holder:
        def __init__(self):
            self.calls = calls

        def set(self, *, stdout=b"", stderr=b"", returncode=0):
            next_result["stdout"] = stdout
            next_result["stderr"] = stderr
            next_result["returncode"] = returncode

    holder = _Holder()

    async def fake_create(*args, **kwargs):
        calls.append(list(args))
        return FakeProc(
            next_result["stdout"], next_result["stderr"], next_result["returncode"]
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    return holder


@pytest.fixture(autouse=True)
def reset_config_cache():
    """Each test reads config.yaml fresh (or uses defaults) — no cross-test bleed."""
    worker_tools._invalidate_config_cache()
    yield
    worker_tools._invalidate_config_cache()


@pytest.mark.asyncio
async def test_whitelisted_hermes_runs_and_returns_stdout(fake_exec, tmp_path, monkeypatch):
    # point CONFIG_PATH at a tmp config with hermes allowed
    cfg = tmp_path / "config.yaml"
    cfg.write_text("worker_tools:\n  allowed_commands:\n    - hermes\n", encoding="utf-8")
    monkeypatch.setattr(worker_tools, "CONFIG_PATH", cfg)
    fake_exec.set(stdout=b"the answer is 42\n", stderr=b"some warning noise\n", returncode=0)

    out = await run_command("hermes memory list --source tool")

    assert fake_exec.calls == [["hermes", "memory", "list", "--source", "tool"]]
    # run_command is literal-shell-only: it returns the stdout tail verbatim and
    # ignores stderr on success (no session_id/-Q/--resume folding — retired by ADR-0022).
    assert out == "the answer is 42"


@pytest.mark.asyncio
async def test_non_whitelisted_command_rejected_before_exec(fake_exec, tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("worker_tools:\n  allowed_commands:\n    - hermes\n", encoding="utf-8")
    monkeypatch.setattr(worker_tools, "CONFIG_PATH", cfg)

    out = await run_command("ls /tmp")

    assert fake_exec.calls == []  # never spawned
    assert "not allowed" in out
    assert "ls" in out
    assert "hermes" in out  # the whitelist is named in the message


@pytest.mark.asyncio
async def test_malformed_shlex_returns_error_string_not_raise(fake_exec):
    out = await run_command("hermes chat -q 'unterminated")
    assert fake_exec.calls == []
    assert "malformed" in out.lower() or "quote" in out.lower()


@pytest.mark.asyncio
async def test_empty_command_returns_error_string(fake_exec):
    out = await run_command("")
    assert fake_exec.calls == []
    assert "empty" in out.lower()


@pytest.mark.asyncio
async def test_missing_binary_returns_error_string_not_raise(fake_exec, tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("worker_tools:\n  allowed_commands:\n    - hermes\n", encoding="utf-8")
    monkeypatch.setattr(worker_tools, "CONFIG_PATH", cfg)

    # simulate FileNotFoundError (binary not on PATH)
    async def boom(*args, **kwargs):
        raise FileNotFoundError(2, "No such file", args[0] if args else "?")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)

    out = await run_command("hermes chat -q 'hi' -Q --yolo --source tool")
    assert "not found" in out
    assert "hermes" in out


@pytest.mark.asyncio
async def test_nonzero_exit_returns_error_string_with_stderr_tail(fake_exec, tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("worker_tools:\n  allowed_commands:\n    - hermes\n", encoding="utf-8")
    monkeypatch.setattr(worker_tools, "CONFIG_PATH", cfg)
    fake_exec.set(stdout=b"", stderr=b"boom: something broke\n", returncode=1)

    out = await run_command("hermes chat -q 'hi' -Q --yolo --source tool")
    assert "exited 1" in out
    assert "boom: something broke" in out


@pytest.mark.asyncio
async def test_timeout_kills_proc_and_returns_error(fake_exec, tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "worker_tools:\n  allowed_commands:\n    - hermes\n  timeout_seconds: 0.01\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(worker_tools, "CONFIG_PATH", cfg)

    class SlowProc(FakeProc):
        async def communicate(self):
            await asyncio.sleep(10)  # longer than the 0.01s timeout

    proc = SlowProc(b"", b"", 0)

    async def fake_create(*args, **kwargs):
        fake_exec.calls.append(list(args))
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)

    out = await run_command("hermes chat -q 'long' -Q --yolo --source tool")
    assert "timed out" in out
    assert proc.killed is True


@pytest.mark.asyncio
async def test_config_missing_falls_back_to_default_whitelist(fake_exec, tmp_path, monkeypatch):
    # no config.yaml at the path -> default whitelist (hermes)
    monkeypatch.setattr(worker_tools, "CONFIG_PATH", tmp_path / "nonexistent.yaml")
    fake_exec.set(stdout=b"ok\n", stderr=b"", returncode=0)

    out = await run_command("hermes sessions list --limit 5")
    assert fake_exec.calls == [["hermes", "sessions", "list", "--limit", "5"]]
    assert "ok" in out


@pytest.mark.asyncio
async def test_extensible_whitelist_allows_extra_commands(fake_exec, tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("worker_tools:\n  allowed_commands:\n    - hermes\n    - git\n", encoding="utf-8")
    monkeypatch.setattr(worker_tools, "CONFIG_PATH", cfg)
    fake_exec.set(stdout=b"main\n", stderr=b"", returncode=0)

    out = await run_command("git branch --show-current")
    assert fake_exec.calls == [["git", "branch", "--show-current"]]
    assert "main" in out


@pytest.mark.asyncio
async def test_no_stdout_no_session_id_returns_no_output(fake_exec, tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("worker_tools:\n  allowed_commands:\n    - hermes\n", encoding="utf-8")
    monkeypatch.setattr(worker_tools, "CONFIG_PATH", cfg)
    fake_exec.set(stdout=b"", stderr=b"some spinner noise\n", returncode=0)

    out = await run_command("hermes sessions list")
    assert out == "(no output)"