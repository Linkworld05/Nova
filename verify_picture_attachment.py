# SPDX-License-Identifier: AGPL-3.0-or-later
"""Attachment regression tests; temporary fixtures and mocked local vision HTTP."""
import base64,io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace,MethodType
from unittest.mock import Mock,patch
from PIL import Image
import assistant,picture_analysis


class PictureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent,prefix='nova-picture-check-')
        self.root_patch=patch.object(assistant,'ROOT',Path(self.tmp.name));self.root_patch.start()
    def tearDown(self):
        self.root_patch.stop();self.tmp.cleanup()

    def test_prepare_resizes_preserves_original_and_flattens_transparency(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            p=Path(tmp)/'transparent.png'
            Image.new('RGBA',(3000,600),(255,0,0,0)).save(p)
            original=p.read_bytes();picture=picture_analysis.prepare_picture(p)
            self.assertEqual(p.read_bytes(),original)
            self.assertEqual(picture.name,p.name)
            with Image.open(io.BytesIO(base64.b64decode(picture.encoded))) as encoded:
                self.assertEqual(encoded.size,(2048,410))
                self.assertEqual(encoded.getpixel((0,0)),(255,255,255))
                self.assertFalse(encoded.getexif())
            self.assertLessEqual(picture.thumbnail.width,96)
            self.assertLessEqual(picture.thumbnail.height,72)

    def test_exif_orientation_is_applied(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            p=Path(tmp)/'rotated.jpg';exif=Image.Exif();exif[274]=6
            Image.new('RGB',(80,40),'red').save(p,exif=exif)
            picture=picture_analysis.prepare_picture(p)
            with Image.open(io.BytesIO(base64.b64decode(picture.encoded))) as encoded:
                self.assertEqual(encoded.size,(40,80))

    def test_rejects_invalid_and_oversized_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            p=Path(tmp)/'fake.png';p.write_text('not an image')
            with self.assertRaises(ValueError):picture_analysis.prepare_picture(p)
            Image.new('RGB',(8,8),'blue').save(p)
            with patch.object(picture_analysis,'MAX_FILE_BYTES',1):
                with self.assertRaises(ValueError):picture_analysis.prepare_picture(p)
            with patch.object(picture_analysis,'MAX_PIXELS',4):
                with self.assertRaises(ValueError):picture_analysis.prepare_picture(p)

    def test_vision_request_contains_attachment_and_uses_localhost(self):
        response=Mock();response.__enter__=Mock(return_value=io.BytesIO(b'{"message":{"content":"A red circle."}}'))
        response.__exit__=Mock(return_value=False)
        with patch.object(assistant.urllib.request,'urlopen',return_value=response) as request:
            self.assertEqual(assistant.vision_reply('encoded-test-image','What color?',[]),'A red circle.')
            req=request.call_args.args[0]
            self.assertEqual(req.full_url,'http://127.0.0.1:11434/api/chat')
            body=json.loads(req.data)
            self.assertEqual(body['messages'][0]['images'],['encoded-test-image'])
            self.assertIn('What color?',body['messages'][0]['content'])

    def test_empty_vision_answer_is_reported_as_failure(self):
        # Each retry receives a fresh stream, as a real HTTP request would.
        with patch.object(assistant.urllib.request,'urlopen',side_effect=lambda *a,**k:io.BytesIO(b'{"message":{"content":""}}')) as request, \
             patch.dict('os.environ',{'OMI_VISION_FALLBACK':'fixture-fallback'}):
            with self.assertRaisesRegex(RuntimeError,'empty response'):
                assistant.vision_reply('encoded-test-image','Describe this picture.')
            self.assertEqual(request.call_count,3)

    def test_attachment_bypasses_pc_commands_and_profile_learning(self):
        app=SimpleNamespace(busy=False,recorder=None,attached_picture=SimpleNamespace(name='test.png',encoded='image'),
            question=Mock(),chat_history=[],settings={'pc_control':True},_wake_stop=Mock(),
            chat_append=Mock(),voice_answer=Mock(),refresh_turns=Mock(),refresh_sidebar=Mock())
        app.question.get.return_value=''
        app._current_project=lambda:None
        app._picture_or_command=MethodType(assistant.App._picture_or_command,app)
        app.remove_picture=lambda:setattr(app,'attached_picture',None)
        app.work=lambda fn,done,failed=None:done(fn())
        with patch.object(assistant,'vision_reply',return_value='A circle.') as vision, \
             patch.object(assistant,'classify_command',return_value=[]), \
             patch.object(assistant,'command_or_respond') as commands, \
             patch.object(assistant,'save_turn') as save,patch.object(assistant,'refresh_profile') as profile:
            assistant.App.answer(app)
            vision.assert_called_once_with('image','Describe this picture.',[])
            commands.assert_not_called();profile.assert_not_called()
            self.assertIn('[Picture: test.png]',save.call_args.args[0])
            self.assertIsNone(app.attached_picture)
            self.assertIsNotNone(app.context_picture)


if __name__=='__main__':unittest.main()
