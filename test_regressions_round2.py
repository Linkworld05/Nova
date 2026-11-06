# SPDX-License-Identifier: AGPL-3.0-or-later
"""Round-2 regression tests (2026-11-03): the remaining holes reported after
the first fix round.
1. Whitelisted commands with write-capable options (--output) require approval.
2. Failed, ranged, or TRUNCATED reads never unlock a whole-file write;
   an untruncated full read does; 'edit' keeps working after a ranged read.
3. Old and new revisions of the same manual are never retrieved together.
"""
import json
import tempfile
from pathlib import Path

import codex_agent
import assistant

tmp = Path(tempfile.mkdtemp(prefix="nova-regr2-"))

def ok(text):
    return True

def decline(text):
    return False

# 1) git --output writes a file -> must NOT auto-approve.
calls = []
def recording_approve(text):
    calls.append(text)
    return False

res = codex_agent._tool_run({"cmd": "git log --output=pwned.txt"}, tmp, recording_approve)
assert res.startswith("USER DECLINED"), res
assert calls, "--output command must ask for approval"

calls.clear()
res = codex_agent._tool_run({"cmd": "git log --oneline"}, tmp, recording_approve)
assert not calls, "plain read-only command must stay auto-approved"

# 2a) A FAILED read does not unlock a write, and does not poison the cache.
victim = tmp / "victim.txt"
victim.write_text("precious content here", encoding="utf-8")
script = iter([
    {"tool": "read", "path": "victim.txt", "start": 9999, "lines": 5},   # fails: beyond EOF
    {"tool": "write", "path": "victim.txt", "content": "garbage"},        # must be refused
    {"tool": "read", "path": "victim.txt", "start": 9999, "lines": 5},   # must run again, not 'cached'
    {"tool": "read", "path": "victim.txt"},                               # untruncated full read
    {"tool": "write", "path": "victim.txt", "content": "legitimate"},
    {"final": "done"},
])
def chat_s(msgs, tokens=1200):
    return {"message": {"content": json.dumps(next(script))}}
final, audit, msgs = codex_agent.run("work", tmp, chat_s, ok, max_steps=12)
assert victim.read_text(encoding="utf-8") == "legitimate", victim.read_text()
assert any("refused (not fully read)" in a for a in audit), audit
errors = [a for a in audit if "beyond" in a.lower() or "ERROR" in a]
cached = [a for a in audit if "cached" in a]
assert len(errors) >= 2, "the failed read must execute again, not be served from cache"
assert not cached, cached

# 2b) A TRUNCATED plain read never unlocks a whole-file write; 'edit' still works.
big = tmp / "big.py"
big.write_text("\n".join(f"line_{i:04d} = {i}" for i in range(1, 700)), encoding="utf-8")  # ~13k chars, over the 6000-char read cap
script = iter([
    {"tool": "read", "path": "big.py"},                                   # truncated at 2500 chars
    {"tool": "write", "path": "big.py", "content": "only what i saw"},    # must be refused
    {"tool": "read", "path": "big.py", "start": 10, "lines": 1},          # ranged read
    {"tool": "write", "path": "big.py", "content": "only a window"},      # still refused
    {"tool": "edit", "path": "big.py", "old": "line_0010 = 10", "new": "line_0010 = 'ok'"},  # allowed
    {"final": "done"},
])
final, audit, msgs = codex_agent.run("work big", tmp, chat_s, ok, max_steps=12)
text = big.read_text(encoding="utf-8")
assert "only what i saw" not in text and "only a window" not in text, text[:200]
assert "line_0010 = 'ok'" in text and "line_0699 = 699" in text, "edit must preserve unseen lines"
assert sum(1 for a in audit if "refused (not fully read)" in a) == 2, audit

# 3) Two revisions of the same manual: only the newest is retrieved.
class RevLib:
    def __init__(self):
        self.scopes = []
    def list(self):
        # list() is ordered newest-first (id DESC); same 'name' = same manual, new revision
        return [
            {"id": 9, "title": "ACME Guide", "name": "acme.pdf", "version": 2},
            {"id": 4, "title": "ACME Guide", "name": "acme.pdf", "version": 1},
        ]
    def retrieve(self, question, scope, history=None):
        self.scopes.append(str(scope))
        return [{"manual_id": int(scope), "page": 1, "id": "p1", "text": "revision text v" + scope,
                 "label": f"M{scope} p.1", "title": "ACME Guide", "version": int(scope)}]

lib = RevLib()
hits = assistant.manual_context("how do I configure ACME?", lib, [])
assert hits and all(h["manual_id"] == 9 for h in hits), hits
assert lib.scopes == ["9"], lib.scopes  # the old revision (4) must never be queried

print("round-2 regression tests passed")
