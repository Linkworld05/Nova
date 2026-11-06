# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local model profiles, cancellable GPU scheduling, and content-free metrics."""
import json
import os
import sqlite3
import threading
import time
import urllib.request
from contextlib import contextmanager, closing
from pathlib import Path

PROFILES = {
    'Fast': {'chat': 'qwen3:8b', 'code': 'qwen3:14b', 'ctx': 4096},
    'Balanced': {'chat': 'qwen3:14b', 'code': 'qwen3:14b', 'ctx': 8192},
    'Deep': {'chat': 'qwen3:14b', 'code': 'qwen3-coder:30b', 'ctx': 8192},
}
_GPU = threading.Lock()
_RUNTIMES = {}
_RUNTIMES_LOCK = threading.Lock()


def settings(root):
    try:
        value = json.loads((Path(root) / 'nova-runtime.json').read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def configure(root, **changes):
    data = settings(root)
    data.update(changes)
    if data.get('profile', 'Balanced') not in PROFILES:
        raise ValueError('Unknown model profile.')
    path = Path(root) / 'nova-runtime.json'
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(temporary, path)


@contextmanager
def gpu_slot(control=None):
    """One GPU request at a time in this Nova process; Stop works while waiting."""
    while not _GPU.acquire(timeout=.1):
        if control is not None:
            control.check()
    try:
        if control is not None:
            control.check()
        yield
    finally:
        _GPU.release()


def runtime(root):
    key = str(Path(root).resolve())
    with _RUNTIMES_LOCK:
        if key not in _RUNTIMES:
            _RUNTIMES[key] = Runtime(Path(key))
        return _RUNTIMES[key]


class Runtime:
    def __init__(self, root):
        self.root = root
        self.profile_lock = threading.Lock()
        self.last_profile_update = 0

    def selection(self, purpose='chat'):
        name = settings(self.root).get('profile', 'Balanced')
        return name, PROFILES.get(name, PROFILES['Balanced'])

    def record(self, purpose, model, started, result=None, error=None):
        """Never persist prompts, responses, commands, credentials, or error text."""
        try:
            with closing(sqlite3.connect(self.root / 'nova-metrics.sqlite3', timeout=5)) as db, db:
                db.execute('CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY, created REAL, purpose TEXT, model TEXT, seconds REAL, input_tokens INTEGER, output_tokens INTEGER, eval_ns INTEGER, status TEXT)')
                result = result or {}
                db.execute('INSERT INTO calls(created,purpose,model,seconds,input_tokens,output_tokens,eval_ns,status) VALUES(?,?,?,?,?,?,?,?)',
                           (time.time(), purpose, model, time.monotonic()-started,
                            result.get('prompt_eval_count', 0), result.get('eval_count', 0),
                            result.get('eval_duration', 0), 'error' if error else 'ok'))
        except (OSError, sqlite3.Error):
            pass  # Telemetry must never break the user's task.

    def recent(self):
        path = self.root / 'nova-metrics.sqlite3'
        if not path.exists():
            return []
        with closing(sqlite3.connect(path)) as db:
            return db.execute('SELECT purpose,model,seconds,input_tokens,output_tokens,status FROM calls ORDER BY id DESC LIMIT 15').fetchall()

    def chat(self, messages, tokens=700, model=None, purpose='chat', control=None, allow_thinking=True, response_format=None):
        name, profile = self.selection(purpose)
        model = model or os.environ.get('OMI_CODEX_MODEL' if purpose == 'code' else 'OMI_MODEL') or profile[purpose]
        # Extra thinking is opt-in, only for the supported general Qwen model.
        think = allow_thinking and name == 'Deep' and purpose == 'chat' and model.startswith('qwen3:')
        options = {'num_ctx': profile['ctx'], 'num_predict': max(tokens, 2400) if think else tokens,
                   'temperature': .2}
        if purpose == 'code':options['num_ctx']=8192
        started = time.monotonic()
        result = None
        error = None
        try:
            with gpu_slot(control):
                if control is not None:
                    from codex_controls import ollama_chat
                    result = ollama_chat(messages, model, options['num_predict'], control,
                                         options=options, think=think,response_format=response_format)
                else:
                    payload={'model': model, 'messages': messages, 'stream': False,
                             'think': think, 'keep_alive': '5m', 'options': options}
                    if purpose=='code':
                        from agent_protocol import SCHEMA
                        payload['format']=SCHEMA
                    if response_format is not None:
                        payload['format']=response_format
                        payload['options']['temperature']=0
                    body=json.dumps(payload).encode()
                    request = urllib.request.Request('http://127.0.0.1:11434/api/chat', data=body,
                                                     headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(request, timeout=420 if purpose == 'chat' else 600) as response:
                        result = json.load(response)
                if not result.get('message', {}).get('content', '').strip():
                    raise RuntimeError('The local model returned an empty answer. Try Balanced or a shorter request.')
                return result
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            self.record(purpose, model, started, result, error)

    def refresh_profile(self, callback, material):
        """Coalesce learning: at most once per ten minutes, never queue many threads."""
        if not self.profile_lock.acquire(blocking=False):
            return
        if time.monotonic() - self.last_profile_update < 600:
            self.profile_lock.release()
            return
        def worker():
            try:
                # Small delay lets the current answer/voice finish before learning.
                time.sleep(2)
                if _GPU.locked():return
                callback(material)
                self.last_profile_update = time.monotonic()
            finally:
                self.profile_lock.release()
        threading.Thread(target=worker, daemon=True).start()
