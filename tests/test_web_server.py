from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from song2voice.web.server import Handler


class WebServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        for name in ['uploads','jobs']:(self.root/name).mkdir()
        self.cfg=dict(workspace=str(self.root))
        self.server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,config=self.cfg))
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def test_page_and_post_origin_and_private_artifacts(self):
        with urlopen(self.base+'/') as r:self.assertIn('MIDI + 歌词',r.read().decode())
        for headers in [{},{'X-UtaWeave':'local','Origin':'https://other.example'}]:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.base+'/api/jobs',b'{}',headers))
            self.assertEqual(error.exception.code,400)
        job=self.root/'jobs'/('a'*32);job.mkdir();(job/'render.local.json').write_text('{"private":"path"}')
        with self.assertRaises(HTTPError) as error:urlopen(self.base+'/files/'+job.name+'/render.local.json')
        self.assertEqual(error.exception.code,404)
        outside=self.root/'private.wav';outside.write_bytes(b'secret');(job/'leak.wav').symlink_to(outside)
        with self.assertRaises(HTTPError) as error:urlopen(self.base+'/files/'+job.name+'/leak.wav')
        self.assertEqual(error.exception.code,404)
