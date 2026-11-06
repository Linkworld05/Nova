# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression: the agent must salvage a complete JSON tool call when the model
appends a stray '}' or a second JSON object after it (observed with
qwen3-coder:30b on 2026-11-05 — it wrote valid writes that never executed
because of one extra closing brace on its own line)."""
import json
import tempfile
from pathlib import Path

import codex_agent

tmp = Path(tempfile.mkdtemp(prefix="nova-codex-salvage-"))


def run_script(replies):
    script = iter(replies)
    def fake_chat(msgs, tokens=1200):
        reply = next(script)
        if not isinstance(reply, str):
            reply = json.dumps(reply)
        return {"message": {"content": reply}}
    approvals = []
    return codex_agent.run(
        "build a tiny thing", tmp, fake_chat,
        lambda text: approvals.append(text) or True,
        max_steps=10)


# 1. Stray trailing brace on its own line after a complete write call.
write_call = {"tool": "write", "path": "hello.txt",
              "content": "hello\nworld"}
final, audit, msgs = run_script([
    json.dumps(write_call) + "\n}",
    {"final": "done"},
])
assert final.startswith("done"), final
assert (tmp / "hello.txt").read_text() == "hello\nworld"
assert any("stray brace" in m.get("content", "") for m in msgs), msgs
# The write must appear in the audit trail.
assert any("write" in a and "hello.txt" in a for a in audit), audit

# 2. Two JSON objects in one reply: execute the first, ignore the second.
(tmp / "multi.txt").unlink(missing_ok=True)
final, audit, msgs = run_script([
    json.dumps({"tool": "write", "path": "multi.txt", "content": "first"}) +
    "\n" + json.dumps({"final": "premature"}),
    {"tool": "run", "cmd": "echo hi"},
    {"final": "really done"},
])
assert final.startswith("really done"), final
assert (tmp / "multi.txt").read_text() == "first"
assert any("only the first" in m.get("content", "") for m in msgs), msgs

# 3. Genuinely broken JSON still counts as a failure (no silent salvage).
script2 = iter([
    '{"tool": "list", "path": ',
    '{"tool": "list", "path": ',
    '{"tool": "list", "path": ',
    '{"tool": "list", "path": ',
    '{"tool": "list", "path": ',
])
def broken_chat2(msgs, tokens=1200):
    return {"message": {"content": next(script2)}}
final, audit, msgs = codex_agent.run(
    "try listing", tmp, broken_chat2, lambda text: True, max_steps=10)
assert "invalid JSON" in final or "tool protocol" in final, final
assert not any("stray brace" in m.get("content", "") or "only the first" in m.get("content", "")
               for m in msgs if m.get("role") == "user"), msgs

print("codex json-salvage tests passed")

# 4. An identical call with INVALID arguments must also trip the repeat guard
# (observed live: the model resent the same invalid run_tests 29 times, burning
# the whole step budget in seconds).
bad_call = {"tool": "run_tests", "directory": ".", "pattern": "test_*.py"}
final, audit, msgs = run_script([bad_call] * 8)
assert "repeated tool loop" in final, final
# The guard fires on the 6th identical call, before any execution (invalid calls
# never execute anyway).
assert len(audit) == 0, audit
# The error feedback must name the offending argument and the allowed ones.
assert any("Unexpected argument for run_tests: directory" in m.get("content", "") and "Allowed arguments" in m.get("content", "")
           for m in msgs if m.get("role") == "user"), msgs

print("codex invalid-repeat guard tests passed")

# 5. Oscillation guard: an alternating read/list cycle that mutates nothing
# must stop instead of burning the whole step budget (observed live: 25 steps
# of read→list→run_tests repetition).
osc = iter([
    {"tool": "read", "path": "app.py"},
    {"tool": "list", "path": "."},
    {"tool": "run_tests", "framework": "python"},
] * 4 + [{"final": "never reached"}])
def osc_chat(msgs, tokens=1200):
    reply = next(osc)
    return {"message": {"content": json.dumps(reply)}}
final, audit, msgs = codex_agent.run(
    "fix the app", tmp, osc_chat, lambda text: True, max_steps=30)
assert "oscillating tool loop" in final, final

print("codex oscillation guard tests passed")

# 6. Literal newlines inside JSON strings must parse (observed with
# qwen3-coder: the model wrote edit old/new text with raw newlines).
call = '{"tool":"write","path":"nl.txt","content":"line1\nline2"}'
final, audit, msgs = run_script([call, {"final": "done"}])
assert final.startswith("done"), final
assert (tmp / "nl.txt").read_text() == "line1\nline2"
print("codex literal-newline JSON tests passed")

# 7. A JSON object whose string VALUES contain markdown ``` fences (a README
# with code blocks, observed live while creating a project) must NOT have the
# fence-extraction regex amputate it into a braceless fragment — the reply
# already IS a JSON object, so fences are not a wrapper to strip.
readme_call = json.dumps({
    "tool": "write", "path": "README.md",
    "content": "# Project\n\nRun it:\n\n```bash\n   python main.py\n```\n\nDone."})
final, audit, msgs = run_script([readme_call, {"final": "done"}])
assert final.startswith("done"), final
assert (tmp / "README.md").read_text().startswith("# Project"), \
    (tmp / "README.md").read_text() if (tmp / "README.md").exists() else "missing"
print("codex fence-inside-JSON tests passed")
