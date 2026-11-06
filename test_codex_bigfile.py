# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checks: ranged reads (start/lines) expose any part of big files; the read cache
is per-region; and a 'final' claiming changes that never happened gets flagged."""
import json
import tempfile
from pathlib import Path

import codex_agent

tmp = Path(tempfile.mkdtemp(prefix="nova-codex-big-"))
big = tmp / "big.py"
lines = [f"line_{i:04d} = {i}" for i in range(1, 501)]
big.write_text("\n".join(lines) + "\n", encoding="utf-8")

approvals = []
def ok(text):
    approvals.append(text)
    return True

# 1) Ranged read returns exactly the requested window, with line header.
out = codex_agent._tool_read({"path": "big.py", "start": 200, "lines": 20}, tmp, ok)
assert "lines 200-219 of 500" in out, out
assert "line_0200" in out and "line_0219" in out and "line_0001" not in out, out[:200]

# 2) search -> ranged read -> exact edit, the workflow the agent was missing.
target_line = 300
res = codex_agent._tool_search({"path": "big.py", "term": "line_0300"}, tmp, ok)
assert f"big.py:{target_line}:" in res, res
region = codex_agent._tool_read({"path": "big.py", "start": target_line, "lines": 1}, tmp, ok)
exact = "line_0300 = 300"
assert exact in region, region
edit = codex_agent._tool_edit({"path": "big.py", "old": exact, "new": "line_0300 = 'edited'"}, tmp, ok)
assert edit.startswith("edited"), edit
assert "line_0300 = 'edited'" in big.read_text(encoding="utf-8")

# 3) Cache is per region: same path, different start executes; same start is cached.
script = iter([
    {"tool": "read", "path": "big.py", "start": 10, "lines": 5},
    {"tool": "read", "path": "big.py", "start": 400, "lines": 5},
    {"tool": "read", "path": "big.py", "start": 10, "lines": 5},   # duplicate region
    {"final": "done"},
])
def chat_region(msgs, tokens=1200):
    return {"message": {"content": json.dumps(next(script))}}
final, audit, msgs = codex_agent.run("inspect", tmp, chat_region, ok, max_steps=10)
assert final == "done", final
cached = [a for a in audit if "cached" in a]
executed = [a for a in audit if a.startswith(("1. ", "2. ")) and "read" in a and "cached" not in a]
assert len(executed) == 2, audit
assert len(cached) == 1 and "cached (step 1)" in cached[0], audit
note = [m["content"] for m in msgs if "same region" in m.get("content", "")]
assert note, "model should be told the re-read was a same-region cache hit"

# 4) start beyond EOF -> clean ERROR, not a crash.
try:
    codex_agent._tool_read({"path": "big.py", "start": 9999, "lines": 5}, tmp, ok)
    raise SystemExit("expected ValueError for start beyond EOF")
except ValueError as e:
    assert "beyond" in str(e), e

# 5) Honesty guard: edit fails, final claims success -> flagged.
script2 = iter([
    {"tool": "edit", "path": "big.py", "old": "this text is nowhere", "new": "x"},
    {"final": "I have successfully modified big.py to use the new value."},
])
def chat_claim(msgs, tokens=1200):
    return {"message": {"content": json.dumps(next(script2))}}
final2, audit2, _ = codex_agent.run("change it", tmp, chat_claim, ok, max_steps=5)
assert "No file change was actually confirmed" in final2, final2

# 6) Control: a successful edit means no flag.
script3 = iter([
    {"tool": "edit", "path": "big.py", "old": "line_0001 = 1", "new": "line_0001 = 'ok'"},
    {"final": "I have successfully modified big.py."},
])
def chat_ok(msgs, tokens=1200):
    return {"message": {"content": json.dumps(next(script3))}}
final3, _, _ = codex_agent.run("change it", tmp, chat_ok, ok, max_steps=5)
assert "No file change" not in final3, final3

print("codex big-file/ranged-read/honesty tests passed")
