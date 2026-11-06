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
                    page.locator('#connect-short-rests').check()
                    with page.expect_response('**/segments') as response:
                        page.locator('#preview-segments').click()
                    self.assertGreater(response.value.json()['connected_short_rests'],0)
                    page.locator('#note-editor>summary').click();page.locator('#note-0').fill('k')
                    self.assertTrue(page.locator('#segment-result').is_hidden())
                    page.locator('#phrase-review-only').uncheck();page.locator('#phrase-1>summary').click();page.locator('#phrase-reading-1').fill('kimi')
                    page.wait_for_function("document.getElementById('draft-status').textContent.includes('已保存到服务器')")
                    page.reload();page.wait_for_function("document.getElementById('note-0')?.value==='k'")
                    self.assertEqual(page.locator('#note-0').get_attribute('aria-invalid'),'true')
                    self.assertEqual(page.locator('#phrase-reading-1').input_value(),'kimi')
                    self.assertFalse(page.locator('#phrase-review-only').is_checked())
                    self.assertTrue(page.locator('#connect-short-rests').is_checked())
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

    def test_chinese_switch_and_hanzi_draft_roundtrip(self):
        import mido
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            executable=pw.chromium.executable_path
            if not Path(executable).exists():self.skipTest('Playwright Chromium is not installed')
            root=Path(tmp);(root/'uploads').mkdir();job=root/'jobs'/('c'*32);job.mkdir(parents=True)
            notes=[dict(start=1+i*.5,end=1.5+i*.5,pitch=60) for i in range(4)]
            prepared=dict(mode='midi',language='zh',bpm=120,notes=notes,labels=list('春风吹过'),revision='chinese',
                alignment=None,unmatched_moras=0,warnings=[],lyrics={'original':'春风吹过'},reading={'reading':'春 风 吹 过'})
            atomic_json(job/'prepared.json',prepared)
            atomic_json(job/'status.json',dict(id=job.name,status='awaiting_score',stage='review',message='test',created_at=time.time(),updated_at=time.time()))
            midi=mido.MidiFile();track=mido.MidiTrack();midi.tracks.append(track)
            track.extend([mido.Message('note_on',note=60,time=0),mido.Message('note_off',note=60,time=480)])
            midi.save(root/'test.mid')
            server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,config={'workspace':str(root)}))
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with pw.chromium.launch(headless=True,executable_path=executable,args=['--no-sandbox']) as browser:
                    page=browser.new_page();errors=[];submitted=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    def route_jobs(route):
                        if route.request.method=='POST':
                            submitted.append(route.request.post_data_json)
                            route.fulfill(json={'id':job.name})
                        else:route.continue_()
                    page.route('**/api/jobs',route_jobs)
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    page.locator('[data-mode=midi]').click();page.locator('#chinese-mode').check()
                    with page.expect_response('**/api/upload?*'):
                        page.locator('#midi-file').set_input_files(str(root/'test.mid'))
                    page.wait_for_function("document.getElementById('midi-file-info').textContent.includes('已上传')")
                    page.locator('#lyrics-text').fill('春风吹过');page.locator('#prepare').click()
                    page.wait_for_function("document.getElementById('note-0')?.value==='春'")
                    self.assertEqual(submitted[0]['language'],'zh')
                    self.assertTrue(page.locator('#reading-mode-panel').is_hidden())
                    self.assertTrue(page.locator('#note-romaji-0').is_hidden())
                    self.assertIn('无 LoRA',page.locator('#score-summary').inner_text())
                    page.locator('#note-0').fill('秋')
                    page.wait_for_function("document.getElementById('draft-status').textContent.includes('已保存到服务器')")
                    page.reload();page.wait_for_function("document.getElementById('note-0')?.value==='秋'")
                    self.assertEqual(page.locator('#note-preview-0').inner_text(),'将使用：秋')
                    self.assertEqual(page.locator('#reading-input-mode').input_value(),'native')
                    self.assertEqual(errors,[])
            finally:server.shutdown();server.server_close();thread.join()

    def test_embedded_midi_goes_straight_to_reference_and_render(self):
        import mido
        from unittest.mock import patch
        from song2voice.web.workflow import prepare
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            executable=pw.chromium.executable_path
            if not Path(executable).exists():self.skipTest('Playwright Chromium is not installed')
            root=Path(tmp);(root/'uploads').mkdir();(root/'jobs').mkdir()
            midi=mido.MidiFile(charset='utf-8');track=mido.MidiTrack();midi.tracks.append(track)
            for char in '春风吹过':
                track.extend([mido.MetaMessage('lyrics',text=char),mido.Message('note_on',note=60),mido.Message('note_off',note=60,time=480)])
            midi.save(root/'lyrics.mid');renders=[]
            def launch(cfg,job,action):
                if action=='prepare':
                    atomic_json(job/'status.json',dict(id=job.name,status='queued',stage='queued',message='test',created_at=time.time(),updated_at=time.time()))
                    with patch('song2voice.web.workflow.g2p',return_value=[['zh/a']]*4):
                        prepare(cfg,job,json.loads((job/'request.json').read_text()))
                elif action=='render':renders.append(json.loads((job/'render-request.json').read_text()))
                else:raise AssertionError(action)
            server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,config={'workspace':str(root)}))
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with patch('song2voice.web.server.launch',side_effect=launch),pw.chromium.launch(headless=True,executable_path=executable,args=['--no-sandbox']) as browser:
                    page=browser.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    page.locator('#lyrics-text').fill('旧歌词不应覆盖内嵌歌词')
                    page.locator('[data-mode=midi_lyrics]').click();page.locator('#chinese-mode').check()
                    self.assertTrue(page.locator('#lyrics-section').is_hidden())
                    with page.expect_response('**/api/upload?*'):
                        page.locator('#midi-file').set_input_files(str(root/'lyrics.mid'))
                    page.wait_for_function("document.getElementById('midi-file-info').textContent.includes('已上传')")
                    page.locator('#prepare').click();page.locator('#embedded-panel').wait_for(state='visible')
                    self.assertTrue(page.locator('#review-panel').is_hidden())
                    self.assertTrue(page.locator('#confirmed').is_hidden())
                    self.assertTrue(page.locator('#voice-panel').is_visible())
                    self.assertIn('4 个音符',page.locator('#embedded-summary').inner_text())
                    page.reload();page.locator('#embedded-panel').wait_for(state='visible')
                    self.assertTrue(page.locator('#review-panel').is_hidden())
                    page.locator('#reference-url').fill('https://music.163.com/song?id=12345')
                    with page.expect_response('**/render') as response:page.locator('#render').click()
                    self.assertTrue(response.value.ok)
                    self.assertEqual(renders[0]['labels'],list('春风吹过'))
                    self.assertFalse(renders[0]['confirmed'])
                    request=json.loads(next((root/'jobs').glob('*/request.json')).read_text())
                    self.assertEqual(request['lyric_source'],'embedded');self.assertEqual(request['lyrics_text'],'')
                    self.assertEqual(errors,[])
            finally:server.shutdown();server.server_close();thread.join()
