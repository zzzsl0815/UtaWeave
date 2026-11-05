"""Serve local listening reports with byte ranges for reliable audio seeking."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re


class ReportHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def send_head(self):
        self.remaining = None
        value = self.headers.get('Range', '')
        match = re.fullmatch(r'bytes=(\d*)-(\d*)', value.strip())
        path = Path(self.translate_path(self.path))
        if self.command != 'GET' or not match or not path.is_file():
            return super().send_head()
        if not any(match.groups()):
            return super().send_head()
        try:
            stream = path.open('rb')
        except OSError:
            return super().send_head()
        stat = os.fstat(stream.fileno())
        # A mismatching validator requires a fresh full representation.
        if_range = self.headers.get('If-Range')
        if if_range and if_range != self.date_time_string(stat.st_mtime):
            stream.close()
            return super().send_head()
        first, last = match.groups()
        size = stat.st_size
        start = int(first) if first else max(0, size-int(last))
        end = min(size-1, int(last)) if first and last else size-1
        if start >= size or end < start:
            stream.close()
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{size}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return None
        self.remaining = end-start+1
        stream.seek(start)
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(str(path)))
        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length', str(self.remaining))
        self.send_header('Last-Modified', self.date_time_string(stat.st_mtime))
        self.end_headers()
        return stream

    def copyfile(self, source, outputfile):
        if self.remaining is None:
            return super().copyfile(source, outputfile)
        while self.remaining:
            block = source.read(min(64*1024, self.remaining))
            if not block:
                break
            outputfile.write(block)
            self.remaining -= len(block)

    def do_GET(self):
        try:
            super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            # Seeking and changing tracks cancel the preceding media request.
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8768)
    parser.add_argument('--bind', default='127.0.0.1')
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error('Report directory does not exist')
    handler = partial(ReportHandler, directory=str(args.directory.resolve()))
    with ThreadingHTTPServer((args.bind, args.port), handler) as server:
        print(f'Listening report: http://{args.bind}:{server.server_port}/', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
