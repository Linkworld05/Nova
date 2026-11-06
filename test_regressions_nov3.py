# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression tests for the 2026-11-03 reliability fixes:
1. Command approval cannot be bypassed by chaining after a read-only prefix.
2. Coding mode refuses to overwrite a file that was never read.
3. The agent's final answer is kept in the history handed back for follow-ups.
4. _manual_safe_history strips citation-bearing assistant turns (revision mixing).
5. manual_context falls back to the previous turn's manuals for follow-ups.
"""
import json
import tempfile
from pathlib import Path

import codex_agent
import assistant

tmp = Path(tempfile.mkdtemp(prefix="nova-regr-"))

def ok(text):
    return True

def decline(text):
    return False

# 1) Chaining after a read-only prefix must NOT auto-approve.
calls = []
def recording_approve(text):
    calls.append(text)
    return False

res = codex_agent._tool_run({"cmd": "git status; echo pwned"}, tmp, recording_approve)
assert res.startswith("USER DECLINED"), res
assert calls and "git status; echo pwned" in calls[0], calls

calls.clear()
res = codex_agent._tool_run({"cmd": "git status"}, tmp, recording_approve)
assert not calls, "plain read-only command must stay auto-approved"

calls.clear()
res = codex_agent._tool_run({"cmd": "git log --oneline | sort"}, tmp, recording_approve)
assert calls, "a piped read-only command must fall back to approval"

# 2) Write to an existing, never-read file is refused; after a read it succeeds.
victim = tmp / "victim.txt"
victim.write_text("precious original content", encoding="utf-8")
script = iter([
    {"tool": "write", "path": "victim.txt", "content": "hallucinated replacement"},
    {"tool": "read", "path": "victim.txt"},
    {"tool": "write", "path": "victim.txt", "content": "legitimate replacement"},
    {"final": "done"},
])
def chat_w(msgs, tokens=1200):
    return {"message": {"content": json.dumps(next(script))}}
final, audit, msgs = codex_agent.run("overwrite it", tmp, chat_w, ok, max_steps=10)
assert victim.read_text(encoding="utf-8") == "legitimate replacement", victim.read_text()
assert any("refused (not" in a for a in audit), audit

# New files can still be written without a prior read.
script = iter([
    {"tool": "write", "path": "brand_new.py", "content": "print('hi')"},
    {"final": "done"},
])
final, audit, msgs = codex_agent.run("create a file", tmp, chat_w, ok, max_steps=5)
assert (tmp / "brand_new.py").is_file()

# 3) The final answer is part of the returned history.
assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"] == final and final.startswith("done"), msgs[-1]

# 4) Citation-bearing assistant turns are stripped; user context survives.
history = [
    {"role": "user", "content": "How do I set up Process Lasso?"},
    {"role": "assistant", "content": "Open Options > General [M2 p.4] and enable X."},
    {"role": "user", "content": "What about the second filter?"},
]
safe = assistant._manual_safe_history(history)
assert len(safe) == 2 and safe[0]["role"] == "user" and safe[1]["content"].startswith("What about"), safe
assert assistant._manual_safe_history(None) == []

# 5) Follow-up fallback: no software named -> previous manuals supply context.
class FakeLib:
    def list(self):
        return [{"id": 7, "title": "Process Lasso Guide"}]
    def retrieve(self, question, scope, history=None):
        return [{"manual_id": 7, "page": 3, "id": "p3", "text": "filter two info", "label": "M1 p.3",
                 "title": "Process Lasso Guide", "version": "1.0"}] if str(scope) == "7" else []

assert assistant.manual_context("what about the second filter?", FakeLib(), []) is None
hits = assistant.manual_context("what about the second filter?", FakeLib(), [], fallback_ids=[7])
assert hits and hits[0]["manual_id"] == 7, hits

print("2026-11-03 regression tests passed")
