"""Optional browser regression test: pip install playwright + Chromium to run."""
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
import time
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from song2voice.lyric_alignment import align_lrc, parse_lrc
from song2voice.selection import atomic_json
from song2voice.web.server import Handler


@unittest.skipIf(sync_playwright is None, 'Playwright is optional')
class EditorDraftBrowserTests(unittest.TestCase):
    def test_raw_input_survives_reload_disconnect_and_another_tab(self):
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            executable=pw.chromium.executable_path
            if not Path(executable).exists():self.skipTest('Playwright Chromium is not installed')
            root=Path(tmp);(root/'uploads').mkdir();job=root/'jobs'/('a'*32);job.mkdir(parents=True)
            notes=[dict(start=1+i*.5,end=1.2+i*.5,pitch=60) for i in range(8)]
            def reading(text):
                return dict(reading=' '.join(text),moras=list(text),units=[dict(text=c,lang='ja',weight=1,vowel='ア') for c in text],warnings=[])
            alignment=align_lrc(notes,parse_lrc('[00:01]アイウエ\n[00:03]カキクケ'),[reading('アイウエ'),reading('カキクケ')],offset=0,scale=1)
            prepared=dict(mode='midi',bpm=120,notes=notes,labels=alignment.pop('labels'),revision='original',alignment=alignment,
                          unmatched_moras=0,warnings=[],lyrics={'original':'アイウエ カキクケ'},reading={'reading':'アイウエ カキクケ'})
            atomic_json(job/'prepared.json',prepared)
            atomic_json(job/'status.json',dict(id=job.name,status='awaiting_score',stage='review',message='test',created_at=time.time(),updated_at=time.time()))
            server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,config={'workspace':str(root)}))
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with pw.chromium.launch(headless=True,executable_path=executable,args=['--no-sandbox']) as browser:
                    page=browser.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    url=f'http://127.0.0.1:{server.server_port}/#job={job.name}'
                    page.goto(url);page.wait_for_function("document.getElementById('draft-status').textContent.includes('开始编辑')")
                    page.locator('#segment-panel>summary').click();page.locator('#preview-segments').click()
                    page.locator('#segment-result').wait_for(state='visible')
                    self.assertEqual(page.locator('#segment-rows tr').count(),1)
                    self.assertIn('1 段',page.locator('#segment-summary').inner_text())
                    self.assertTrue(page.locator('#segment-rows .romaji-text').inner_text())
                    page.locator('#note-editor>summary').click();page.locator('#note-0').fill('k')
                    self.assertTrue(page.locator('#segment-result').is_hidden())
                    page.locator('#phrase-review-only').uncheck();page.locator('#phrase-1>summary').click();page.locator('#phrase-reading-1').fill('kimi')
                    page.wait_for_function("document.getElementById('draft-status').textContent.includes('已保存到服务器')")
                    page.reload();page.wait_for_function("document.getElementById('note-0')?.value==='k'")
                    self.assertEqual(page.locator('#note-0').get_attribute('aria-invalid'),'true')
                    self.assertEqual(page.locator('#phrase-reading-1').input_value(),'kimi')
                    self.assertFalse(page.locator('#phrase-review-only').is_checked())
                    self.assertTrue(page.locator('#phrase-0').is_visible())
                    other=browser.new_page();other.goto(url);other.wait_for_function("document.getElementById('note-0')?.value==='k'")
                    other.locator('#note-editor>summary').click();other.locator('#note-0').fill('oo')
                    other.wait_for_function("document.getElementById('draft-status').textContent.includes('已保存到服务器')")
                    self.assertEqual(page.locator('#note-0').input_value(),'k');other.close()
                    page.route('**/api/jobs/**/draft',lambda route:route.abort())
                    page.locator('#note-editor>summary').click();page.locator('#note-0').fill('zz')
                    page.wait_for_function("document.getElementById('draft-status').textContent.includes('暂未同步')")
                    page.reload();page.wait_for_function("document.getElementById('note-0')?.value==='zz'")
                    page.unroute('**/api/jobs/**/draft');page.evaluate('draftStore.flush()')
                    page.wait_for_function("document.getElementById('draft-status').textContent.includes('已保存到服务器')")
                    self.assertGreaterEqual(len(list((job/'drafts').glob('*.json'))),2)
                    self.assertEqual(json.loads((job/'prepared.json').read_text()),prepared)
                    self.assertFalse((job/'process.json').exists());self.assertEqual(errors,[])
            finally:server.shutdown();server.server_close();thread.join()
