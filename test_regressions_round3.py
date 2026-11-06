# SPDX-License-Identifier: AGPL-3.0-or-later
"""Round-3 regression tests (2026-11-03): findings from the reanalysis.
1. "git diff --no-index" (reads arbitrary files outside any repo) requires approval.
2. A file changed on disk after Nova read it cannot be overwritten or edited
   until it is read again (time-of-check/time-of-use guard).
"""
import json
import tempfile
from pathlib import Path

import codex_agent

tmp = Path(tempfile.mkdtemp(prefix="nova-regr3-"))

def ok(text):
    return True

def decline(text):
    return False

# 1) git --no-index reads arbitrary files -> must NOT auto-approve.
calls = []
def recording_approve(text):
    calls.append(text)
    return False

res = codex_agent._tool_run({"cmd": "git diff --no-index secret.txt nul"}, tmp, recording_approve)
assert res.startswith("USER DECLINED"), res
assert calls, "--no-index command must ask for approval"

calls.clear()
res = codex_agent._tool_run({"cmd": "git diff --stat"}, tmp, recording_approve)
assert not calls, "plain read-only command must stay auto-approved"

# 2) File changed on disk after the read -> write and edit refused until re-read.
victim = tmp / "live.txt"
victim.write_text("version one", encoding="utf-8")

class Script:
    """Yields scripted tool calls; runs a hook each time (used to simulate an
    external program changing the file between agent steps)."""
    def __init__(self, steps, hook=None):
        self._steps = iter(steps)
        self._hook = hook
    def __next__(self):
        item = next(self._steps)
        if self._hook:
            self._hook(item)
        return item

def external_change(item):
    if item.get("tool") == "write" and "stale attempt" in str(item.get("content")):
        # Someone/something edits the file after Nova read it but before the write.
        with victim.open("a", encoding="utf-8") as f:
            f.write(" + edits made elsewhere")

script = Script([
    {"tool": "read", "path": "live.txt"},
    {"tool": "write", "path": "live.txt", "content": "stale attempt"},
    {"tool": "read", "path": "live.txt"},
    {"tool": "write", "path": "live.txt", "content": "fresh, informed rewrite"},
    {"final": "done"},
], hook=external_change)

def chat(msgs, tokens=1200):
    return {"message": {"content": json.dumps(next(script))}}

final, audit, msgs = codex_agent.run("update it", tmp, chat, ok, max_steps=10)
assert "refused (stale read)" in "\n".join(audit), audit
assert victim.read_text(encoding="utf-8") == "fresh, informed rewrite", victim.read_text()

# Edit path: same staleness rule.
victim2 = tmp / "live2.txt"
victim2.write_text("alpha beta gamma", encoding="utf-8")
fired = [False]
def change_once(item):
    if item.get("tool") == "edit" and not fired[0]:
        fired[0] = True
        victim2.write_text("alpha beta gamma + user addition", encoding="utf-8")

script2 = Script([
    {"tool": "read", "path": "live2.txt"},
    {"tool": "edit", "path": "live2.txt", "old": "alpha beta", "new": "ALPHA BETA"},
    {"tool": "read", "path": "live2.txt"},
    {"tool": "edit", "path": "live2.txt", "old": "alpha beta gamma", "new": "alpha beta v2"},
    {"final": "done"},
], hook=change_once)

def chat2(msgs, tokens=1200):
    return {"message": {"content": json.dumps(next(script2))}}

final2, audit2, _ = codex_agent.run("capitalize it", tmp, chat2, ok, max_steps=10)
joined = "\n".join(audit2)
assert "refused (stale read)" in joined, joined
assert "user addition" in victim2.read_text(encoding="utf-8"), "the external edit must survive"
assert "alpha beta v2" in victim2.read_text(encoding="utf-8"), victim2.read_text()

print("round-3 regression tests passed")
