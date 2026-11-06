# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistent import, versioning, retrieval, and citation tests using generated manuals."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import pymupdf
from docx import Document
import assistant
from manual_library import ManualLibrary

class ManualTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.root=Path(self.tmp.name);self.lib=ManualLibrary(self.root)
    def tearDown(self):self.tmp.cleanup()
    def pdf(self,name='Pump manual.pdf',pressure='420 kPa'):
        path=self.root/name
        with pymupdf.open() as doc:
            for text in ['Pump maintenance manual. Safety instructions.',f'Pressure limit is {pressure}. Replace the filter every 90 days.','Torque bolts to 12 Nm.']:
                page=doc.new_page();page.insert_text((60,60),text)
            doc.save(path)
        return path
    def test_pdf_pages_saved_source_and_restart(self):
        path=self.pdf();original=path.read_bytes();m=self.lib.import_file(path)
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(m['pages'],3)
        self.assertEqual((self.lib.folder/m['folder']/'source.pdf').read_bytes(),original)
        reopened=ManualLibrary(self.root)
        self.assertEqual(reopened.scope(),str(m['id']))
        hits=reopened.retrieve('What is the pressure limit?')
        self.assertEqual(hits[0]['page'],2);self.assertIn('420 kPa',hits[0]['text'])
    def test_duplicates_and_revisions_keep_old_contents(self):
        path=self.pdf();old=self.lib.import_file(path)
        self.assertTrue(self.lib.import_file(path)['duplicate']);self.assertEqual(len(self.lib.list()),1)
        path.unlink();self.pdf(pressure='610 kPa');new=self.lib.import_file(path)
        self.assertEqual(new['version'],2);self.assertEqual(self.lib.scope(),str(new['id']))
        self.assertIn('610 kPa',self.lib.retrieve('pressure limit')[0]['text'])
        self.assertIn('420 kPa',self.lib.retrieve('pressure limit',str(old['id']))[0]['text'])
        self.assertEqual(len(self.lib.list()),2)
    def test_word_tables_and_text_have_reference_page_numbers(self):
        path=self.root/'Instructions.docx';doc=Document();doc.add_heading('Compressor service',0)
        doc.add_paragraph('Replace the intake filter every 70 days.')
        table=doc.add_table(rows=1,cols=2);table.cell(0,0).text='Coolant capacity';table.cell(0,1).text='14 liters';doc.save(path)
        manual=self.lib.import_file(path)
        hit=self.lib.retrieve('coolant capacity')[0]
        self.assertEqual(hit['page'],1);self.assertIn('14 liters',hit['text'])
        self.assertIn('reference-copy',manual['notes'])
        text=self.root/'Guide.txt';text.write_text('Start-up instructions.\fDisconnect power before cleaning. Safety checklist.',encoding='utf-8')
        m=self.lib.import_file(text);self.assertEqual(m['pages'],2)
        self.assertEqual(self.lib.retrieve('Disconnect power cleaning')[0]['page'],2)
    def test_scanned_pdf_ocr_and_citation(self):
        from PIL import Image
        import io
        out=io.BytesIO();Image.new('RGB',(300,300),'white').save(out,format='PNG')
        path=self.root/'Scanned.pdf'
        with pymupdf.open() as doc:
            p=doc.new_page();p.insert_image(p.rect,stream=out.getvalue());doc.save(path)
        m=self.lib.import_file(path,ocr=lambda encoded:'The lubrication interval is 45 days.')
        self.assertIn('OCR pages: 1',m['notes'])
        self.assertEqual(self.lib.retrieve('lubrication interval')[0]['page'],1)
    def test_failed_import_does_not_change_scope(self):
        m=self.lib.import_file(self.pdf())
        path=self.root/'Invalid.pdf';path.write_text('not a PDF')
        with self.assertRaises(Exception):self.lib.import_file(path)
        self.assertEqual(self.lib.scope(),str(m['id']));self.assertEqual(len(self.lib.list()),1)
    def test_no_match_does_not_call_web_or_model(self):
        m=self.lib.import_file(self.pdf())
        with patch.object(assistant,'chat') as model,patch.object(assistant.web_search,'lookup') as web:
            answer=assistant.respond_from_manuals('unicorn recipes',[],self.lib,str(m['id']))
            self.assertIn("couldn't find",answer);model.assert_not_called();web.assert_not_called()
    def test_answer_has_grounded_page_sources_and_rejects_invalid_citation(self):
        m=self.lib.import_file(self.pdf());label=f"[M{m['id']} p.2]"
        with patch.object(assistant,'chat',return_value={'message':{'content':'The pressure limit is 420 kPa. '+label}}):
            answer=assistant.respond_from_manuals('pressure limit',[],self.lib,str(m['id']))
            self.assertIn(label,answer);self.assertIn('Manual sources:',answer)
        with patch.object(assistant,'chat',return_value={'message':{'content':'Use 600 kPa [M999 p.20]'}}):
            answer=assistant.respond_from_manuals('pressure limit',[],self.lib,str(m['id']))
        self.assertIn('cannot confirm',answer)
        self.assertNotIn('[M999',answer)

if __name__=='__main__':unittest.main()
