# SPDX-License-Identifier: AGPL-3.0-or-later
"""Clipboard-image handling checks without changing the user's clipboard."""
import base64,io,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from PIL import Image
import assistant,picture_analysis


class ClipboardPictureTests(unittest.TestCase):
    def test_bitmap_becomes_an_attachment_without_saving_a_file(self):
        image=Image.new('RGBA',(120,80),(255,0,0,255))
        with patch.object(picture_analysis.ImageGrab,'grabclipboard',return_value=image):
            picture=picture_analysis.picture_from_clipboard()
        self.assertEqual(picture.name,'Clipboard picture')
        with Image.open(io.BytesIO(base64.b64decode(picture.encoded))) as encoded:
            self.assertEqual(encoded.size,(120,80))
            self.assertGreater(encoded.getpixel((0,0))[0],250)

    def test_native_windows_dib_decoding(self):
        image=Image.new('RGB',(80,60),'blue');buffer=io.BytesIO();image.save(buffer,format='BMP')
        # This is the exact DIB payload Pillow receives from CF_DIB on Windows.
        with patch.object(Image.core,'grabclipboard_win32',return_value=('DIB',buffer.getvalue()[14:])):
            picture=picture_analysis.picture_from_clipboard()
        self.assertEqual(picture.name,'Clipboard picture')
        with Image.open(io.BytesIO(base64.b64decode(picture.encoded))) as encoded:
            self.assertGreater(encoded.getpixel((0,0))[2],250)

    def test_copied_image_file_is_supported(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            path=Path(tmp)/'copied.png';Image.new('RGB',(80,60),'green').save(path)
            original=path.read_bytes()
            with patch.object(picture_analysis.ImageGrab,'grabclipboard',return_value=['notes.txt',str(path)]):
                self.assertEqual(picture_analysis.picture_from_clipboard().name,'copied.png')
            self.assertEqual(path.read_bytes(),original)

    def test_text_and_non_image_files_return_none(self):
        for content in [None,[],['notes.txt']]:
            with self.subTest(content=content),patch.object(picture_analysis.ImageGrab,'grabclipboard',return_value=content):
                self.assertIsNone(picture_analysis.picture_from_clipboard())

    def test_shortcut_consumes_image_and_preserves_normal_text_paste(self):
        app=SimpleNamespace(busy=False,recorder=None,status=Mock(),show_picture=Mock())
        event=SimpleNamespace(widget=Mock())
        picture=SimpleNamespace(name='Clipboard picture')
        with patch.object(assistant,'picture_from_clipboard',return_value=picture):
            self.assertEqual(assistant.App.paste_picture(app,event),'break')
            app.show_picture.assert_called_once_with(picture)
        app.show_picture.reset_mock()
        with patch.object(assistant,'picture_from_clipboard',return_value=None):
            self.assertIsNone(assistant.App.paste_picture(app,event))
            app.show_picture.assert_not_called()

    def test_busy_and_clipboard_errors_preserve_current_attachment(self):
        previous=object();app=SimpleNamespace(busy=True,recorder=None,status=Mock(),
                                             show_picture=Mock(),attached_picture=previous)
        with patch.object(assistant,'picture_from_clipboard') as read:
            assistant.App.paste_picture(app);read.assert_not_called()
        app.busy=False
        with patch.object(assistant,'picture_from_clipboard',side_effect=OSError('Clipboard is busy')):
            assistant.App.paste_picture(app)
        self.assertIs(app.attached_picture,previous);app.show_picture.assert_not_called()
        self.assertIn('Could not paste picture',app.status.set.call_args.args[0])


if __name__=='__main__':unittest.main()
