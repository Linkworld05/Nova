# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checks for per-project memory: NOVA.md is injected at task start (fresh and
follow-up runs), and the remember tool appends dated facts."""
import json
import tempfile
from pathlib import Path

import codex_agent

tmp = Path(tempfile.mkdtemp(prefix="nova-codex-mem-"))
(tmp / "app.py").write_text("print('hi')\n", encoding="utf-8")

approvals = []
def ok(text):
    approvals.append(text)
    return True

# 1) remember creates NOVA.md with header and a dated entry.
out = codex_agent._tool_remember({"text": "No C compiler on this PC — pip installs needing builds fail."}, tmp, ok)
assert out.startswith("saved to NOVA.md"), out
mem = (tmp / "NOVA.md").read_text(encoding="utf-8")
assert "# Nova Project Memory" in mem
assert "- 20" in mem and "No C compiler on this PC" in mem, mem

# 2) remember appends; entries accumulate.
codex_agent._tool_remember({"text": "Tests run with ./.venv/Scripts/python.exe test_x.py"}, tmp, ok)
mem = (tmp / "NOVA.md").read_text(encoding="utf-8")
assert mem.count("- 20") == 2, mem
assert "Tests run with ./.venv/Scripts/python.exe" in mem

# 3) Fresh run injects the memory before the question.
script = iter([{"final": "ack"}])
def chat(msgs, tokens=1200):
    return {"message": {"content": json.dumps(next(script))}}
final, audit, msgs = codex_agent.run("hello", tmp, chat, ok, max_steps=5)
mem_msgs = [m for m in msgs if m.get("role") == "user" and "PROJECT MEMORY" in m.get("content", "")]
assert len(mem_msgs) == 1, msgs
assert "No C compiler on this PC" in mem_msgs[0]["content"]
qpos = next(i for i, m in enumerate(msgs) if m.get("content") == "hello")
mpos = next(i for i, m in enumerate(msgs) if m is mem_msgs[0])
assert mpos < qpos, "memory must precede the question"

# 4) Follow-up run re-injects fresh memory even though history exists.
final, audit, msgs2 = codex_agent.run("again", tmp, chat, ok, max_steps=5, history=msgs)
mem_msgs2 = [m for m in msgs2 if m.get("role") == "user" and "PROJECT MEMORY" in m.get("content", "")]
assert len(mem_msgs2) == 2, "follow-up must carry both the old and the fresh memory block"

# 5) No NOVA.md -> no injection, no crash.
tmp2 = Path(tempfile.mkdtemp(prefix="nova-codex-nomem-"))
script2 = iter([{"final": "ack"}])
def chat2(msgs, tokens=1200):
    return {"message": {"content": json.dumps(next(script2))}}
final, audit, msgs3 = codex_agent.run("hello", tmp2, chat2, ok, max_steps=5)
assert not any(m.get("role") == "user" and "PROJECT MEMORY" in m.get("content", "") for m in msgs3), msgs3

print("codex project-memory tests passed")
