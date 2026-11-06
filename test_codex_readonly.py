# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checks: shell file-viewing is blocked (use read/search/list), read-only commands
auto-run without approval, mutating commands still ask, and the search tool works."""
import tempfile
from pathlib import Path

import codex_agent

tmp = Path(tempfile.mkdtemp(prefix="nova-codex-ro-"))
(tmp / "captions.py").write_text("class CaptionApp:\n    def __init__(self):\n        self.x = 1\n", encoding="utf-8")
(tmp / "notes.txt").write_text("hello World\nsecond line\n", encoding="utf-8")

approvals = []
def ok(text):
    approvals.append(text)
    return True

calls = []
real = codex_agent.subprocess.run
def fake_subprocess(*a, **kw):
    calls.append(a[0] if a else kw.get("args"))
    class R:
        returncode = 0
        stdout = b""
        stderr = b""
    return R()
codex_agent.subprocess.run = fake_subprocess

try:
    # 1) PowerShell file viewing/searching: blocked, no approval, no execution.
    for bad in ('powershell -Command "Get-Content captions.py | Select-String -Pattern \'def __init__\'"',
                "findstr /n def captions.py",
                "type captions.py",
                "dir"):
        approvals.clear()
        try:
            codex_agent._tool_run({"cmd": bad}, tmp, ok)
            raise SystemExit(f"should have been blocked: {bad}")
        except ValueError as e:
            assert "blocked" in str(e), e
        assert approvals == [], (bad, approvals)
    assert calls == [], calls

    # 2) Read-only commands: execute WITHOUT any approval dialog.
    for good in ("pip show pyaudio", "git status -sb", "git diff --stat", "python --version"):
        approvals.clear()
        out = codex_agent._tool_run({"cmd": good}, tmp, ok)
        assert out.startswith("(auto-approved, read-only)"), out
        assert approvals == [], (good, approvals)
    assert calls, "read-only commands should have executed"
    calls.clear()

    # 3) Mutating commands still require approval.
    approvals.clear()
    out = codex_agent._tool_run({"cmd": "pip install pyaudio"}, tmp, ok)
    assert len(approvals) == 1 and "pip install pyaudio" in approvals[0]
    assert not out.startswith("(auto-approved"), out
    approvals.clear()

    # 4) search tool: content grep with line numbers, no approval.
    res = codex_agent._tool_search({"path": ".", "term": "captionapp"}, tmp, ok)
    assert "captions.py:1:" in res and "class CaptionApp" in res, res
    res = codex_agent._tool_search({"path": "notes.txt", "term": "world"}, tmp, ok)
    assert "notes.txt:1: hello World" in res, res
    res = codex_agent._tool_search({"path": ".", "term": "zzz-no-such-text"}, tmp, ok)
    assert res.startswith("No matches"), res
    assert approvals == [], approvals

    # 5) Dedupe/decline still intact after these changes (regression guard).
    assert "USER DECLINED" in codex_agent._tool_run({"cmd": "pip uninstall x"}, tmp, lambda t: False)
finally:
    codex_agent.subprocess.run = real

print("codex read-only/auto-approve/search tests passed")
