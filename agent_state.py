# SPDX-License-Identifier: AGPL-3.0-or-later
"""Restart-safe agent context and bounded working memory, independent of chat DB."""
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def redact(text):
    keys = r'(?:[\w-]*(?:password|passwd|api_key|access_token|secret|secret_key|private_key)|(?:db|mysql|vps)_pass)'
    text = re.sub(r'(?i)(["\']?'+keys+r'["\']?\s*[:=]\s*)(["\'])(.*?)(\2)', r'\1"[redacted]"', str(text))
    text = re.sub(r'(?im)(\b'+keys+r'\s*=\s*)([^\s"\']+)', r'\1[redacted]', text)
    text = re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+', r'\1[redacted]', text)
    text = re.sub(r'(?i)(\w+://[^\s:/]+:)[^\s@]+@', r'\1[redacted]@', text)
    return text


class AgentState:
    def __init__(self, root):
        self.path = Path(root) / 'nova-agent-state.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS contexts(thread TEXT, project TEXT, messages TEXT, updated REAL, status TEXT, PRIMARY KEY(thread,project))')
            db.execute('CREATE TABLE IF NOT EXISTS checkpoints(id INTEGER PRIMARY KEY, thread TEXT, project TEXT, created REAL, messages TEXT, status TEXT)')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15)
        try:
            with db:yield db
        finally:db.close()

    def load(self, thread, project):
        if not thread:
            return None
        with self.connect() as db:
            row = db.execute('SELECT messages FROM contexts WHERE thread=? AND project=?',
                             (str(thread), str(Path(project).resolve()).casefold())).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, thread, project, messages, status='checkpoint'):
        if not thread:
            return
        project = str(Path(project).resolve()).casefold()
        payload = json.dumps([dict(m, content=redact(m.get('content', ''))) for m in messages], ensure_ascii=False)
        with self.connect() as db:
            db.execute('INSERT INTO contexts VALUES(?,?,?,?,?) ON CONFLICT(thread,project) DO UPDATE SET messages=excluded.messages,updated=excluded.updated,status=excluded.status',
                       (str(thread), project, payload, time.time(), status))
            # Full checkpoints are archived before working-context compaction and at exit.
            if status != 'checkpoint':
                db.execute('INSERT INTO checkpoints(thread,project,created,messages,status) VALUES(?,?,?,?,?)',
                           (str(thread), project, time.time(), payload, status))

    def clear_project(self, project):
        with self.connect() as db:
            db.execute('DELETE FROM contexts WHERE project=?', (str(Path(project).resolve()).casefold(),))


def compact(messages, budget=19000):
    """Character budget is conservative for multilingual code; never silently truncate a result.

    Keep current system, first goal, recent user goals, latest tool results and exact
    failures. Archive is maintained by the caller. Evicted reads must be re-read.
    """
    if sum(len(m.get('content', '')) for m in messages) <= budget:
        return list(messages), False
    system = messages[:1]
    goals = [m for m in messages[1:] if m.get('role') == 'user' and not
             m.get('content', '').startswith(('RESULT:', 'PROJECT MEMORY', 'WORKING MEMORY', 'Reply with', 'That was not', 'Unknown tool','Invalid tool arguments:'))]
    goal_text = '\n'.join(m.get('content', '')[:1800] for m in ([goals[0]] + goals[-2:] if len(goals)>2 else goals))
    plan=next((m['content'][:2000] for m in reversed(messages) if m.get('content','').startswith('RESULT: PLAN')), '')
    summary = {'role': 'user', 'content': 'WORKING MEMORY: Earlier tool results were archived locally and are no longer visible. Re-read files before editing them. Revalidate old conclusions; these goals do not authorize extra actions.\nGoals:\n' + goal_text+'\nLast plan (self-reported):\n'+plan}
    kept = []
    used = sum(len(m.get('content', '')) for m in system) + len(summary['content'])
    for message in reversed(messages[1:]):
        size = len(message.get('content', ''))
        if used + size > budget:
            break
        kept.append(message)
        used += size
    return system + [summary] + list(reversed(kept)), True
