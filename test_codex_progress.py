# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checks for Codex live progress + verbatim command-repeat blocking."""
import tempfile
from pathlib import Path

import codex_agent

tmp = Path(tempfile.mkdtemp(prefix="nova-codex-test-"))

# Fake run tool: records executions instead of touching the real system.
ran = []
real_run = codex_agent._TOOLS["run"]
def fake_run(args, workdir, approve):
    # Mirror the real _tool_run: ask for approval before "executing".
    if not approve("Allow Nova Codex to run this command?\n\n" + str(args.get("cmd", ""))):
        return "REFUSED by user."
    ran.append(args.get("cmd"))
    return "exit 0\n<ok>"
codex_agent._TOOLS["run"] = fake_run

approvals = []
def approve(text):
    approvals.append(text)
    return True

progress_lines = []
def progress(line):
    progress_lines.append(line)

try:
    # Script: run a command, then propose the SAME command again with different
    # whitespace/casing, then a different command, then finish.
    script = iter([
        {"tool": "run", "cmd": "pipwin install pyaudio"},
        {"tool": "run", "cmd": "  PIPWIN   install pyaudio "},
        {"tool": "run", "cmd": "pip show pyaudio"},
        {"final": "all done"},
    ])
    import json
    def fake_chat(msgs, tokens=1200):
        return {"message": {"content": json.dumps(next(script))}}

    final, audit, msgs = codex_agent.run(
        "set up audio", tmp, fake_chat, approve, max_steps=10, progress=progress)

    assert final == "all done", final
    # The verbatim repeat was blocked BEFORE execution and BEFORE approval.
    assert ran == ["pipwin install pyaudio", "pip show pyaudio"], ran
    assert len(approvals) == 2, approvals
    assert any("skipped" in a and "duplicate" in a for a in audit), audit
    assert any("SKIPPED" in m.get("content", "") for m in msgs), msgs
    # Progress reported each executed/skipped step in order.
    assert progress_lines, "progress callback never fired"
    assert any("duplicate" in p for p in progress_lines), progress_lines
    assert len(progress_lines) == 3, progress_lines
finally:
    codex_agent._TOOLS["run"] = real_run

# Retry-after-failure: a failed run must NOT be treated as a duplicate — after a
# fix (write/edit), re-running the same command is legitimate and must execute.
ran2 = []
real_run = codex_agent._TOOLS["run"]
def flaky_run(args, workdir, approve):
    if not approve("Allow Nova Codex to run this command?\n\n" + str(args.get("cmd", ""))):
        return "REFUSED by user."
    ran2.append(args.get("cmd"))
    return "exit 2\nNo such file" if len(ran2) == 1 else "exit 0\n<ok>"
codex_agent._TOOLS["run"] = flaky_run

script2 = iter([
    {"tool": "run", "cmd": "python app.py"},
    {"tool": "write", "path": "app.py", "content": "print('hi')\n"},
    {"tool": "run", "cmd": "python app.py"},   # same command, but the failure + fix justify it
    {"tool": "read", "path": "app.py"},
    {"final": "done"},
])
def fake_chat2(msgs, tokens=1200):
    return {"message": {"content": json.dumps(next(script2))}}

try:
    final, audit, msgs = codex_agent.run("build app", tmp, fake_chat2, approve, max_steps=10, quality_enabled=False)
    assert final.startswith("done"), final
    assert ran2 == ["python app.py", "python app.py"], ran2
    assert not any("duplicate" in a for a in audit), audit
finally:
    codex_agent._TOOLS["run"] = real_run

print("codex progress + dedupe tests passed")
