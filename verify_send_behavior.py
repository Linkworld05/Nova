# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sending immediately commits the outgoing message and preserves the next draft."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import assistant

class SendTests(unittest.TestCase):
    def app(self):
        app=SimpleNamespace(busy=False,recorder=None,attached_picture=None,context_picture=None,
                            question=Mock(),chat_history=[],settings={},chat_append=Mock(),
                            voice_answer=Mock(),refresh_turns=Mock(),refresh_sidebar=Mock(),_wake_stop=Mock())
        app.manual_library=None
        app._current_project=lambda:None
        app._last_manual_ids=lambda:None
        app.codex_mode=Mock();app.codex_mode.get.return_value=False
        app.question.get.return_value='Hello Nova'
        app.callbacks=[];app.work=lambda fn,done,failed:app.callbacks.append((fn,done,failed))
        return app
    def test_outgoing_message_is_immediate_and_input_clears_before_reply(self):
        app=self.app();assistant.App.answer(app)
        app.chat_append.assert_called_once_with('You','Hello Nova')
        app.question.delete.assert_called_once_with(0,'end')
        with patch.object(assistant,'save_turn'),patch.object(assistant,'refresh_profile'):
            app.callbacks[0][1]('Hello!')
        self.assertEqual([c.args[0] for c in app.chat_append.call_args_list],['You','Assistant'])
    def test_picture_moves_out_of_composer_and_remains_as_context(self):
        app=self.app();picture=SimpleNamespace(name='Photo',encoded='image')
        app.attached_picture=picture
        app.remove_picture=lambda:setattr(app,'attached_picture',None)
        assistant.App.answer(app)
        self.assertIsNone(app.attached_picture);self.assertIs(app.context_picture,picture)
        self.assertIs(app.chat_append.call_args.kwargs['picture'],picture)
    def test_failure_does_not_overwrite_next_draft(self):
        app=self.app();assistant.App.answer(app)
        app.question.get.return_value='Next draft'
        app.status=Mock()
        app.callbacks[0][2]('Offline')
        app.question.insert.assert_not_called()
        self.assertEqual(app.chat_append.call_args.args[0],'Assistant')
    def test_empty_composer_does_not_resend_previous_picture(self):
        app=self.app();app.context_picture=SimpleNamespace(name='Old photo',encoded='image')
        app.question.get.return_value=''
        assistant.App.answer(app)
        self.assertEqual(app.callbacks,[]);app.chat_append.assert_not_called()

if __name__=='__main__':unittest.main()
