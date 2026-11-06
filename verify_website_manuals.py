# SPDX-License-Identifier: AGPL-3.0-or-later
"""Website snapshots, article images, source references and bounded crawling."""
import io,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
import website_manual
from manual_library import ManualLibrary

class WebsiteManualTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.library=ManualLibrary(self.tmp.name)
        self.url='https://example.org/docs/interface/main/'
        buffer=io.BytesIO();Image.new('RGB',(120,80),'gray').save(buffer,format='PNG');self.png=buffer.getvalue()
        self.pages={self.url:('<title>Main - Example Docs</title><nav>UNRELATED NAVIGATION</nav><article><h1>Main window</h1><p>The graph shows processor utilization.</p><img src="/docs/assets/cores.png" alt="Parked gray CPU cores"><a href="/docs/graph/">Graph details</a><a href="https://elsewhere.org/">Other site</a></article>'),
                    'https://example.org/docs/graph/':'<article><h1>CPU graph</h1><p>Gray cores are parked. Blue bars show active cores.</p></article>'}
    def tearDown(self):self.tmp.cleanup()
    def fetch(self,client,url,limit):
        if url.endswith('.png'):return url,self.png,'image/png'
        return url,self.pages[url].encode(),'text/html'
    def test_linked_pages_images_and_references_survive_restart(self):
        vision=Mock(return_value='The diagram contains gray squares for parked CPU cores.')
        with patch.object(website_manual,'fetch',side_effect=self.fetch):
            result=self.library.import_website(self.url,True,ocr=lambda image:'CPU parked',vision=vision)
        self.assertIn('2 web pages, 1 article images',result['notes'])
        vision.assert_called_once()
        reopened=ManualLibrary(self.tmp.name);hits=reopened.retrieve('parked gray CPU cores')
        self.assertTrue(any(s.get('kind')=='image' for s in hits))
        self.assertTrue(all(s.get('url','').startswith('https://example.org/docs/') for s in hits))
        figures=reopened.figure_sources(hits)
        self.assertTrue(figures and figures[0]['file'].is_file())
        self.assertTrue((reopened.folder/result['folder']/'source.zip').is_file())
        self.assertNotIn('UNRELATED NAVIGATION',' '.join(s['text'] for s in hits))
    def test_duplicate_import_reuses_existing_manual_without_reanalyzing(self):
        vision=Mock(return_value='Gray parked cores.')
        with patch.object(website_manual,'fetch',side_effect=self.fetch):
            first=self.library.import_website(self.url,True,vision=vision)
            second=self.library.import_website(self.url,True,vision=vision)
        self.assertEqual(first['id'],second['id']);self.assertTrue(second['duplicate'])
        self.assertEqual(len(self.library.list()),1);vision.assert_called_once()
    def test_single_page_option_and_scope_boundaries(self):
        with patch.object(website_manual,'fetch',side_effect=self.fetch):
            result=self.library.import_website(self.url,False)
        self.assertIn('1 web pages',result['notes'])
        self.assertFalse(website_manual.in_scope('https://elsewhere.org/docs/page/',self.url,'/docs/'))
        self.assertFalse(website_manual.in_scope('https://example.org/account/',self.url,'/docs/'))
        self.assertFalse(website_manual.in_scope('https://example.org/docs/assets/a.png',self.url,'/docs/'))
    def test_question_about_main_window_prefers_matching_article(self):
        with patch.object(website_manual,'fetch',side_effect=self.fetch):
            self.library.import_website(self.url,True,vision=lambda image,prompt:'Three separate parts appear in this settings window.')
        hits=self.library.retrieve('What are the three main parts of the main window?')
        self.assertEqual(hits[0]['kind'],'text')
        self.assertEqual(hits[0]['url'],self.url)
    def test_public_url_validation(self):
        for url in ['file:///secret','ftp://example.org/manual','http://user:password@example.org']:
            with self.assertRaises(ValueError):website_manual.normalize_url(url)
        with patch.object(website_manual.socket,'getaddrinfo',return_value=[(None,None,None,None,('127.0.0.1',80))]):
            with self.assertRaises(ValueError):website_manual.check_public('http://localhost/manual')

if __name__=='__main__':unittest.main()
