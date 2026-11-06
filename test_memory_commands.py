# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checks for explicit user-directed memory: remember/recall/forget, command
routing, injection into answers, and learning from web lookups."""
import json
import tempfile
from pathlib import Path

import assistant

db = Path(tempfile.gettempdir()) / "nova-test-memory.sqlite3"
db.unlink(missing_ok=True)

# 1) remember / recall / forget against an isolated DB.
assistant.remember_fact("the office PC has no C compiler", path=db)
assistant.remember_fact("ExampleApp tests run via .venv Scripts python", path=db)
out = assistant.recall_memory("office PC", path=db)
assert "no C compiler" in out and "ExampleApp" not in out, out
out = assistant.recall_memory("", path=db)
assert "no C compiler" in out and "ExampleApp" in out, out
matches = assistant.memory_matches("how do I run the ExampleApp tests?", path=db)
assert any("ExampleApp" in m for m in matches), matches
out = assistant.forget_memory("office PC", path=db)
assert "Forgot 1" in out, out
assert "no C compiler" not in assistant.recall_memory("", path=db)
out = assistant.forget_memory("everything", path=db)
assert "Forgot everything" in out, out
assert assistant.recall_memory("", path=db).startswith(("Nothing", "No tengo")), out

# 2) Command routing handles the memory action.
res = assistant._run_commands([{"action": "memory", "remember": "my GPU is a 3080 Ti"}], "q", [])
assert "Remembered" in res and "3080 Ti" in res, res
res = assistant._run_commands([{"action": "memory", "recall": "GPU"}], "q", [])
assert "3080 Ti" in res, res
res = assistant._run_commands([{"action": "memory", "forget": "GPU"}], "q", [])
assert "Forgot 1" in res, res

# 3) respond() injects saved memories into the prompt.
real_chat, real_retrieve, real_matches = assistant.chat, assistant.retrieve, assistant.memory_matches
captured = {}
def fake_chat(messages, tokens=700, model=None):
    captured["messages"] = messages
    return {"message": {"content": "Use the saved notes."}}
assistant.chat, assistant.retrieve = fake_chat, lambda q: []
assistant.memory_matches = lambda q, **kw: real_matches(q, path=db, **kw)
try:
    assistant.remember_fact("the monitor is 4K and HDR", path=db)
    out = assistant.respond("Is my monitor HDR?", [], None)
    prompt = json.dumps(captured["messages"][-1]["content"])
    assert "USER'S SAVED MEMORIES" in prompt and "4K and HDR" in prompt, prompt
    assert "[memory]" in out, out
finally:
    assistant.chat, assistant.retrieve = real_chat, real_retrieve

# 4) Web lookups are learned (deduplicated).
learned = []
real_remember, real_needs, real_lookup = assistant.remember_fact, assistant.web_search.needs_web, assistant.web_search.lookup
real_exists = assistant.memory_exists
def _capture(fact, **kw):
    learned.append(fact)
    real_remember(fact, path=db)  # store for real so memory_exists dedupe works
assistant.remember_fact = _capture
assistant.memory_exists = lambda key, **kw: real_exists(key, path=db)
assistant.web_search.needs_web = lambda q: True
assistant.web_search.lookup = lambda q: ("RESULT BLOCK: do X then Y", [{"title": "Example", "url": "https://example.com"}])
assistant.chat, assistant.retrieve = fake_chat, lambda q: []
try:
    assistant.respond("How do I reset the router?", [], None)
    assert learned and "(from the web)" in learned[0] and "reset the router" in learned[0], learned
    n = len(learned)
    assistant.respond("How do I reset the router?", [], None)
    assert len(learned) == n, "duplicate web memory should be skipped"
finally:
    assistant.chat, assistant.retrieve = real_chat, real_retrieve
    assistant.memory_matches = real_matches
    assistant.remember_fact, assistant.web_search.needs_web, assistant.web_search.lookup = real_remember, real_needs, real_lookup
    assistant.memory_exists = real_exists

db.unlink(missing_ok=True)
print("explicit memory + web-learning tests passed")
