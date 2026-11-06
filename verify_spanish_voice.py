# SPDX-License-Identifier: AGPL-3.0-or-later
"""Voice-language selection and staged preference checks; no audio capture."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import local_audio,piper_speech


class SpanishVoiceTests(unittest.TestCase):
    def setUp(self):
        # Synthetic metadata: no executable, audio device or model is required.
        self.enterContext(patch.object(piper_speech,'available',return_value=True))
        self.enterContext(patch.object(piper_speech,'exe_path',return_value=Path('piper-fixture.exe')))
        self.enterContext(patch.object(piper_speech,'voice_list',return_value=[
            {'key':'es_MX-claude-high','name':'Piper · Español (México) · Fixture','culture':'es_MX'},
            {'key':'en_US-fixture','name':'Piper · English · Fixture','culture':'en_US'}]))
    def test_spanish_and_english_routing(self):
        for text in ['Hola, soy Nova.','¿En qué puedo ayudarte?','Voy a revisar el presupuesto de la reunión.','Claro.','De acuerdo.']:
            with self.subTest(text=text):
                self.assertIn('México',local_audio.resolve_voice(text,local_audio.AUTO_VOICE))
        self.assertEqual(local_audio.resolve_voice('The reminder is ready.',local_audio.AUTO_VOICE),'MeloTTS · English (US)')

    def test_explicit_voice_is_preserved(self):
        self.assertEqual(local_audio.resolve_voice('Hola','MeloTTS · Spanish'),'MeloTTS · Spanish')

    def test_spanish_fallback_uses_spanish(self):
        self.assertIn('México',local_audio.language_fallback('es'))
        script=local_audio.speech_script('Mañana, reunión y presupuesto.',local_audio.language_fallback('es'))
        self.assertIn('$OutputEncoding=[Text.UTF8Encoding]::new($false)',script)

    def test_preference_applies_once_and_keeps_other_settings(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            p=Path(tmp)/'voice-settings.json';pending=p.with_name('pending-voice.json')
            settings={'voice':'MeloTTS · English (US)','wake':True,'microphone':'Original device'}
            p.write_text(json.dumps(settings),encoding='utf-8')
            pending.write_text(json.dumps({'voice':local_audio.AUTO_VOICE}),encoding='utf-8')
            updated=local_audio.apply_pending_voice(p,settings)
            self.assertEqual(updated['voice'],local_audio.AUTO_VOICE)
            self.assertEqual(updated['microphone'],'Original device')
            self.assertTrue(updated['wake']);self.assertFalse(pending.exists())
            self.assertEqual(json.loads(p.read_text()),updated)
            updated['voice']='MeloTTS · Spanish'
            self.assertEqual(local_audio.apply_pending_voice(p,updated)['voice'],'MeloTTS · Spanish')


if __name__=='__main__':unittest.main()
