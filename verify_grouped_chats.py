# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chat grouping, migration preservation, and isolated conversation context."""
import sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from chat_history import ChatHistory

class GroupedChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.path=Path(self.tmp.name)/'memory.sqlite3'
    def tearDown(self):self.tmp.cleanup()
    def test_legacy_followups_migrate_together_and_back_up_original(self):
        original=[(1,'2026-10-30 10:00','Earlier chat','Earlier answer'),
                  (2,'2026-11-01 11:48','[Picture: Clipboard picture]\nDo you know this software?','Yes.'),
                  (3,'2026-11-01 12:07','There is a message that appears.','Follow-up answer.')]
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE chat_turns(id INTEGER PRIMARY KEY,created TEXT,question TEXT,answer TEXT)')
            db.executemany('INSERT INTO chat_turns VALUES(?,?,?,?)',original)
            db.commit()
        store=ChatHistory(self.path);threads=store.threads()
        self.assertEqual(len(threads),2);self.assertEqual(threads[0]['count'],2)
        self.assertEqual(threads[0]['title'],'Do you know this software?')
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT id,created,question,answer FROM chat_turns ORDER BY id').fetchall(),original)
        backups=list((self.path.parent/'backups'/'before-grouped-chats').glob('*.sqlite3'))
        self.assertEqual(len(backups),1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute('SELECT * FROM chat_turns ORDER BY id').fetchall(),original)
        self.assertEqual(ChatHistory(self.path).threads(),threads)
        self.assertEqual(len(list(backups[0].parent.glob('*.sqlite3'))),1)
    def test_new_chat_boundaries_are_exact_even_within_one_minute(self):
        store=ChatHistory(self.path);first=store.active()
        store.save_turn('First question','A',first);store.save_turn('Follow-up','B',first)
        second=store.create();store.save_turn('Separate chat','C',second)
        self.assertEqual([r['count'] for r in store.threads()],[1,2])
        store.set_active(first);store.save_turn('Continue first chat','D',first)
        self.assertEqual(len(store.turns(first)),3)
        self.assertEqual(store.threads()[0]['id'],first)
        context=store.recent(first)
        self.assertFalse(any(m['content']=='Separate chat' for m in context))
        self.assertEqual(ChatHistory(self.path).active(),first)
    def test_empty_new_chat_survives_restart_without_old_context(self):
        store=ChatHistory(self.path);first=store.active();store.save_turn('Old question','Old answer',first)
        empty=store.create();reopened=ChatHistory(self.path)
        self.assertEqual(reopened.active(),empty);self.assertEqual(reopened.recent(empty),[])
        self.assertEqual(len(reopened.threads()),1)

if __name__=='__main__':unittest.main()
