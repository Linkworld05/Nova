# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checks that user-declined commands are reported as 'not executed' and the
agent is steered to stop proposing commands and finish honestly."""
import json
import tempfile
from pathlib import Path

import codex_agent

tmp = Path(tempfile.mkdtemp(prefix="nova-codex-decline-"))

ran = []
real_subprocess_run = codex_agent.subprocess.run
def guarded_subprocess_run(*a, **kw):
    # Declined commands must never reach the OS.
    ran.append(a[0] if a else kw.get("args"))
    raise AssertionError("subprocess.run called although the user declined")
codex_agent.subprocess.run = guarded_subprocess_run

approvals = []
def decline(text):
    approvals.append(text)
    return False

try:
    script = iter([
        {"tool": "run", "cmd": "pip install pyaudio"},
        {"tool": "run", "cmd": "pipwin install pyaudio"},
        {"tool": "run", "cmd": "conda install pyaudio"},
        {"final": "You declined the installs; here is what that blocks and what you can do."},
    ])
    def fake_chat(msgs, tokens=1200):
        return {"message": {"content": json.dumps(next(script))}}

    final, audit, msgs = codex_agent.run("install pyaudio", tmp, fake_chat, decline, max_steps=10)

    # Nothing executed, every proposal asked once and was declined.
    assert ran == [], ran
    assert len(approvals) == 3, approvals
    # Results say DECLINED / NOT executed, never "REFUSED".
    results = [m["content"] for m in msgs if m.get("role") == "user" and m["content"].startswith("RESULT:")]
    assert len(results) == 3, results
    assert all("NOT executed" in r for r in results), results
    assert not any(r.startswith("RESULT: REFUSED") for r in results), results
    # After the second decline the agent is told to stop proposing commands.
    steer = [m["content"] for m in msgs if "declined multiple commands" in m.get("content", "")]
    assert len(steer) == 1, steer
    pos = next(i for i, m in enumerate(msgs) if m.get("content") == steer[0])
    assert pos < len(msgs) - 2, "steering must arrive before the final reply"
    assert final.startswith("You declined"), final
    assert any("DECLINED" in a for a in audit), audit
finally:
    codex_agent.subprocess.run = real_subprocess_run

print("codex declined-command tests passed")
