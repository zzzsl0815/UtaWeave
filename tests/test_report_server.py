from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from song2voice.report_server import ReportHandler


class QuietHandler(ReportHandler):
    def log_message(self, *args):
        pass


class ReportServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.data = bytes(range(256))*100
        (Path(cls.folder.name)/'song.mp3').write_bytes(cls.data)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=cls.folder.name))
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/song.mp3'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.folder.cleanup()

    def test_seek_returns_only_requested_bytes(self):
        for value, start, end in [('bytes=1000-1999', 1000, 2000),
                                  ('bytes=25000-', 25000, len(self.data)),
                                  ('bytes=-128', len(self.data)-128, len(self.data)),
                                  ('bytes=25000-999999', 25000, len(self.data))]:
            with self.subTest(value=value), urlopen(Request(self.url, headers={'Range': value})) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.headers['Content-Range'], f'bytes {start}-{end-1}/{len(self.data)}')
                self.assertEqual(int(response.headers['Content-Length']), end-start)
                self.assertEqual(response.read(), self.data[start:end])

    def test_unsatisfiable_ranges_have_no_body(self):
        for value in ['bytes=999999-', 'bytes=200-100', 'bytes=-0']:
            with self.subTest(value=value), self.assertRaises(HTTPError) as caught:
                urlopen(Request(self.url, headers={'Range': value}))
            with caught.exception as response:
                self.assertEqual(response.code, 416)
                self.assertEqual(response.headers['Content-Range'], f'bytes */{len(self.data)}')
                self.assertEqual(response.read(), b'')

    def test_normal_download_head_and_stale_validator(self):
        for headers in [{}, {'Range':'bytes=10-20', 'If-Range':'stale'}, {'Range':'bytes=1-2,5-6'}]:
            with urlopen(Request(self.url, headers=headers)) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), self.data)
                self.assertEqual(response.headers['Accept-Ranges'], 'bytes')
        with urlopen(Request(self.url, method='HEAD')) as response:
            self.assertEqual(int(response.headers['Content-Length']), len(self.data))
            self.assertEqual(response.read(), b'')


if __name__ == '__main__':
    unittest.main()
