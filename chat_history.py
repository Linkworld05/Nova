# SPDX-License-Identifier: AGPL-3.0-or-later
"""Saved chat threads with non-destructive migration of older flat history."""
import re,sqlite3,uuid
from contextlib import contextmanager,closing
from datetime import datetime
from pathlib import Path

def title_for(question):
    text=re.sub(r'^\[(?:Picture|Manual):[^\n]*\]\s*','',question).strip()
    return ' '.join(text.split())[:64] or 'New chat'

class ChatHistory:
    def __init__(self,path):
        self.path=Path(path)
        if self.path.exists():
            with self.connect() as db:
                columns={r[1] for r in db.execute('PRAGMA table_info(chat_turns)')}
                if columns and 'thread_id' not in columns:
                    folder=self.path.parent/'backups'/'before-grouped-chats';folder.mkdir(parents=True,exist_ok=True)
                    target=folder/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.sqlite3')
                    with closing(sqlite3.connect(target)) as backup:db.backup(backup)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS chat_turns(id INTEGER PRIMARY KEY,created TEXT NOT NULL,question TEXT NOT NULL,answer TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS chat_threads(id INTEGER PRIMARY KEY,created TEXT NOT NULL,updated TEXT NOT NULL,title TEXT NOT NULL,legacy INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS chat_state(key TEXT PRIMARY KEY,value TEXT)')
            if 'thread_id' not in {r[1] for r in db.execute('PRAGMA table_info(chat_turns)')}:
                db.execute('ALTER TABLE chat_turns ADD COLUMN thread_id INTEGER REFERENCES chat_threads(id)')
            if 'project' not in {r[1] for r in db.execute('PRAGMA table_info(chat_threads)')}:
                db.execute('ALTER TABLE chat_threads ADD COLUMN project TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS chat_turn_thread ON chat_turns(thread_id,id)')
            if not db.execute('SELECT 1 FROM chat_turns WHERE thread_id IS NULL LIMIT 1').fetchone():return
            previous=None;thread=None
            for row in db.execute('SELECT id,created,question,thread_id FROM chat_turns ORDER BY id').fetchall():
                try:stamp=datetime.fromisoformat(row['created'])
                except ValueError:stamp=None
                if row['thread_id'] is not None:
                    thread=row['thread_id'];previous=stamp;continue
                old=db.execute('SELECT legacy FROM chat_threads WHERE id=?',(thread,)).fetchone() if thread else None
                try:gap=(stamp-previous).total_seconds() if stamp and previous else None
                except TypeError:gap=None
                if not old or not old['legacy'] or gap is None or gap<0 or gap>1800 or stamp.date()!=previous.date():
                    thread=db.execute('INSERT INTO chat_threads(created,updated,title,legacy) VALUES(?,?,?,1)',(row['created'],row['created'],title_for(row['question']))).lastrowid
                db.execute('UPDATE chat_turns SET thread_id=? WHERE id=?',(thread,row['id']))
                db.execute('UPDATE chat_threads SET updated=? WHERE id=?',(row['created'],thread));previous=stamp
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def create(self):
        now=datetime.now().strftime('%Y-%m-%d %H:%M')
        with self.connect() as db:
            tid=db.execute('INSERT INTO chat_threads(created,updated,title) VALUES(?,?,?)',(now,now,'New chat')).lastrowid
            db.execute("INSERT INTO chat_state VALUES('active',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(tid),))
        return tid
    def active(self):
        with self.connect() as db:
            row=db.execute("SELECT t.id FROM chat_threads t JOIN chat_state s ON s.value=CAST(t.id AS TEXT) WHERE s.key='active'").fetchone()
            if row:return row['id']
            row=db.execute('SELECT thread_id FROM chat_turns WHERE thread_id IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
        if row:self.set_active(row['thread_id']);return row['thread_id']
        return self.create()
    def set_active(self,tid):
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM chat_threads WHERE id=?',(tid,)).fetchone():raise ValueError('Chat not found.')
            db.execute("INSERT INTO chat_state VALUES('active',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(tid),))
    def save_turn(self,question,answer,tid=None,project=None):
        tid=self.active() if tid is None else tid;now=datetime.now().strftime('%Y-%m-%d %H:%M')
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM chat_threads WHERE id=?',(tid,)).fetchone():raise ValueError('Chat not found.')
            count=db.execute('SELECT COUNT(*) FROM chat_turns WHERE thread_id=?',(tid,)).fetchone()[0]
            db.execute('INSERT INTO chat_turns(created,question,answer,thread_id) VALUES(?,?,?,?)',(now,question.strip(),answer.strip(),tid))
            if not count:db.execute('UPDATE chat_threads SET title=? WHERE id=?',(title_for(question),tid))
            if project:db.execute('UPDATE chat_threads SET project=? WHERE id=?',(project,tid))
            db.execute('UPDATE chat_threads SET updated=? WHERE id=?',(now,tid))
        return tid
    def threads(self,limit=300):
        with self.connect() as db:
            rows=db.execute('SELECT t.*,COUNT(c.id) AS count,MAX(c.id) AS last_turn FROM chat_threads t JOIN chat_turns c ON c.thread_id=t.id GROUP BY t.id ORDER BY last_turn DESC LIMIT ?',(limit,)).fetchall()
        return [dict(r) for r in rows]
    def thread_project(self,tid):
        with self.connect() as db:
            row=db.execute('SELECT project FROM chat_threads WHERE id=?',(tid,)).fetchone()
        return row['project'] if row and row['project'] else None
    def turns(self,tid,limit=None):
        with self.connect() as db:
            if limit is None:rows=db.execute('SELECT id,created,question,answer FROM chat_turns WHERE thread_id=? ORDER BY id',(tid,)).fetchall()
            else:rows=list(reversed(db.execute('SELECT id,created,question,answer FROM chat_turns WHERE thread_id=? ORDER BY id DESC LIMIT ?',(tid,limit)).fetchall()))
        return [tuple(r) for r in rows]
    def recent(self,tid,limit=6):
        messages=[]
        for _,_,question,answer in self.turns(tid,limit):messages.extend([{'role':'user','content':question},{'role':'assistant','content':answer}])
        return messages
