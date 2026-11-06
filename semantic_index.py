# SPDX-License-Identifier: AGPL-3.0-or-later
"""CPU-only multilingual retrieval. Originals and manual databases are read-only here."""
import hashlib
import json
import math
import sqlite3
import threading
import time
import urllib.request
from array import array
from contextlib import contextmanager
from pathlib import Path
import model_runtime

MODEL = 'embeddinggemma:latest'
_INDEX_LOCK = threading.Lock()


def embed(texts):
    request = urllib.request.Request('http://127.0.0.1:11434/api/embed',
        data=json.dumps({'model': MODEL, 'input': texts, 'truncate': False, 'keep_alive': '10m',
                         'options': {'num_gpu': 0, 'num_thread': 4}}).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=120) as response:
        result = json.load(response)
    vectors = result['embeddings']
    if len(vectors) != len(texts) or any(not v or not all(math.isfinite(x) for x in v) for v in vectors):
        raise ValueError('Invalid local embeddings.')
    return vectors


def normalized(vector):
    norm = math.sqrt(sum(v*v for v in vector))
    if not norm:
        raise ValueError('Empty embedding.')
    return array('f', (v/norm for v in vector))


class SemanticIndex:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'nova-knowledge-index.sqlite3'
        self.last_error = None
        self._query_cache = None

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=20)
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS vectors(chunk INTEGER PRIMARY KEY, digest TEXT, model TEXT, vector BLOB)')
                db.execute('CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT)')
                yield db
        finally:
            db.close()

    def model_identity(self):
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=5) as response:
            models = json.load(response).get('models', [])
        match = next((m for m in models if m.get('name') == MODEL), None)
        if not match:
            raise ValueError('Install embeddinggemma with Ollama before building the semantic index.')
        return MODEL + ':' + match['digest']

    def build(self, library, progress=lambda text: None):
        if not _INDEX_LOCK.acquire(blocking=False):
            raise ValueError('A semantic index is already being built.')
        try:
            identity = self.model_identity()
            with library.connect() as db:
                rows = db.execute('SELECT c.id,c.text,m.title FROM chunks c JOIN manuals m ON m.id=c.manual_id ORDER BY c.id').fetchall()
            with self.connect() as db:
                cached = {r[0]: (r[1], r[2]) for r in db.execute('SELECT chunk,digest,model FROM vectors')}
            pending = []
            for row in rows:
                text = 'title: ' + row['title'] + ' | text: ' + row['text']
                sha = hashlib.sha256(text.encode()).hexdigest()
                if cached.get(row['id']) != (sha, identity):
                    pending.append((row['id'], sha, text))
            for start in range(0, len(pending), 12):
                batch = pending[start:start+12]
                vectors = embed([r[2] for r in batch])
                with self.connect() as db:
                    db.executemany('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?)',
                                   [(row[0], row[1], identity, normalized(v).tobytes()) for row, v in zip(batch, vectors)])
                progress(f'Indexed {min(start+12,len(pending))}/{len(pending)} new or changed manual passages (CPU).')
            with self.connect() as db:
                db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('active_model', identity))
                db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('updated', str(time.time())))
            self.last_error = None
            return {'total': len(rows), 'updated': len(pending)}
        finally:
            _INDEX_LOCK.release()

    def scores(self, question, rows):
        if not self.path.exists() or not model_runtime.settings(self.root).get('semantic_search', False):
            return {}
        try:
            # Only vectors from the active model generation may be compared.
            with self.connect() as db:
                identity = db.execute("SELECT value FROM metadata WHERE key='active_model'").fetchone()
                if not identity:
                    return {}
                vectors = {r[0]: (r[1],r[2]) for r in db.execute('SELECT chunk,digest,vector FROM vectors WHERE model=?', (identity[0],))}
            if self.model_identity()!=identity[0]:
                self.last_error='ModelChanged';return {}
            available = [r for r in rows if r['id'] in vectors and vectors[r['id']][0]==hashlib.sha256(('title: '+r['title']+' | text: '+r['text']).encode()).hexdigest()]
            if not available:
                return {}
            cache_key=(identity[0],question[:2000])
            if self._query_cache and self._query_cache[0]==cache_key and time.monotonic()-self._query_cache[1]<60:
                query=self._query_cache[2]
            else:
                query = normalized(embed(['task: search result | query: ' + question[:2000]])[0])
                self._query_cache=(cache_key,time.monotonic(),query)
            scores = {}
            for row in available:
                vector = array('f');vector.frombytes(vectors[row['id']][1])
                if len(vector) == len(query):
                    score = sum(a*b for a,b in zip(vector,query))
                    if score >= .30:
                        scores[row['id']] = score
            self.last_error = None
            return scores
        except (OSError, ValueError, KeyError, TypeError, IndexError, sqlite3.Error) as exc:
            self.last_error = type(exc).__name__
            return {}  # Lexical retrieval remains available when Ollama is down.

    def status(self):
        if not self.path.exists():
            return 'Not built; lexical search is active.'
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='active_model'").fetchone()
            count = db.execute('SELECT COUNT(*) FROM vectors WHERE model=?', (row[0],)).fetchone()[0] if row else 0
        return f'{count} passages indexed locally. Embeddings run on the CPU.'
