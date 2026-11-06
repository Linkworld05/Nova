# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression checks with isolated storage; no microphone or external requests."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import assistant
import reminders
import web_search


class ProjectFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent,prefix='nova-project-fixes-')
        self.root_patch=patch.object(assistant,'ROOT',Path(self.tmp.name))
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.tmp.cleanup()

    def test_public_and_personal_web_routing(self):
        for q in ['Who is the current president?', 'Tell me who the current president is',
                  'Can you tell me the latest news?', 'Dime quién es el presidente actual']:
            with self.subTest(q=q):self.assertTrue(web_search.needs_web(q))
        for q in ['What did Sarah say about the 2026 budget?', 'What is my latest reminder?',
                  'What did I say today?', 'Qué dijo Sam sobre el presupuesto 2026?',
                  'What is the latest from our meeting?', 'What time is it today?']:
            with self.subTest(q=q):self.assertFalse(web_search.needs_web(q))

    def test_failed_lookup_does_not_invent_current_answer(self):
        with patch.object(assistant,'retrieve',return_value=[]), \
             patch.object(assistant,'get_profile',return_value=''), \
             patch.object(web_search,'lookup',return_value=None), \
             patch.object(assistant,'chat') as model:
            self.assertIn("couldn't verify",assistant.respond('Who is the current president?'))
            model.assert_not_called()

    def test_lookup_success_has_source(self):
        with patch.object(assistant,'retrieve',return_value=[]), \
             patch.object(assistant,'get_profile',return_value=''), \
             patch.object(web_search,'lookup',return_value=('Public result',[{'title':'Source','url':'https://example.org'}])), \
             patch.object(assistant,'chat',return_value={'message':{'content':'Verified answer'}}):
            result=assistant.respond('Who is the current president?')
            self.assertIn('Verified answer',result)
            self.assertIn('https://example.org',result)

    def test_new_chat_clears_recent_context(self):
        app=SimpleNamespace(busy=False,chat_history=[{'role':'user','content':'Old turn'}],
                            question_generation=4,chatview=Mock(),question=Mock())
        assistant.App.new_chat(app)
        self.assertEqual(app.chat_history,[])
        self.assertEqual(app.question_generation,5)

    def test_followup_heading_variations(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            p=Path(tmp)/'memory.sqlite3'
            for heading in ['Action items','Action items:','## Action items','**Action items:**','Acciones pendientes:']:
                with self.subTest(heading=heading):
                    with reminders._connect(p) as db:db.execute('DELETE FROM followups')
                    summary=heading+'\n1. Sam: Send chiller quote\n\n## Open questions\n- Is the budget approved?'
                    self.assertEqual(reminders.capture_followups(summary,p),1)
                    self.assertEqual(reminders.list_followups(p)[0][2],'Sam')

    def test_brief_settings_are_not_pending_reminders(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            p=Path(tmp)/'memory.sqlite3'
            reminders.set_brief_time('08:00',p)
            self.assertEqual(reminders.list_pending(p),[])
            self.assertEqual(reminders.brief_time(p),'08:00')

    def test_reminders_wait_and_are_spoken_as_one_batch(self):
        app=SimpleNamespace(closing=False,recorder=object(),busy=False,
                            speaker=SimpleNamespace(active=Mock()),chat_append=Mock(),
                            voice_answer=Mock(),refresh_reminders=Mock(),_wake_stop=Mock())
        app.speaker.active.is_set.return_value=False
        rows=[(1,'2026-10-30 08:00','First task','reminder'),(2,'2026-10-30 08:00','Second task','reminder')]
        with patch.object(reminders,'due',return_value=rows) as due, \
             patch.object(reminders,'mark_done') as mark:
            assistant.App._fire_reminders(app)
            due.assert_not_called();mark.assert_not_called()
            app.recorder=None
            app.speaker.active.is_set.return_value=True
            assistant.App._fire_reminders(app)
            due.assert_not_called()
            app.speaker.active.is_set.return_value=False
            assistant.App._fire_reminders(app)
            app.voice_answer.assert_called_once()
            spoken=app.voice_answer.call_args.args[0]
            self.assertIn('First task',spoken);self.assertIn('Second task',spoken)
            app._wake_stop.assert_called_once()
            mark.assert_called_once_with([1,2])


if __name__=='__main__':unittest.main()
