#!/usr/bin/env python3
"""Fetch pinned Seed-VC sources/weights, verifying Git blob and SHA-256 identities."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(8*1024*1024):
            h.update(block)
    return h.hexdigest()


def url_bytes(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=45) as response:
                return response.read()
        except OSError:
            if attempt == 2:
                raise
            time.sleep(attempt+1)


def sources(entries):
    jobs = []
    for entry in entries:
        repo, revision = entry['repo'], entry['revision']
        tree = json.loads(url_bytes(f'https://api.github.com/repos/{repo}/git/trees/{revision}?recursive=1'))
        if tree.get('truncated') or tree['sha'] != revision:
            raise ValueError('Incomplete or unexpected source tree: '+repo)
        destination = ROOT/'vendor'/entry['name']
        destination.mkdir(parents=True, exist_ok=True)
        for item in tree['tree']:
            path = item['path']
            if (item['type'] == 'blob' and
                (path.endswith(('.py', '.yml', '.yaml', '.json'))
                 or '/' not in path and path.endswith(('.txt', '.md'))
                 or path.endswith(('LICENSE', 'LICENSE.txt')))
                and not path.startswith(('assets/', 'examples/', 'demo/'))):
                jobs.append((entry, item, destination/path))
        (destination/'UPSTREAM_REVISION').write_text(revision+'\n')
    def fetch(job):
        entry, item, path = job
        url = f'https://raw.githubusercontent.com/{entry["repo"]}/{entry["revision"]}/{item["path"]}'
        raw = url_bytes(url)
        actual = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if actual != item['sha']:
            raise ValueError('Source hash mismatch: '+url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(fetch, jobs))
    print('Verified source files:', len(jobs))


def models(entries):
    def fetch(entry):
        path = ROOT/'models/svc'/entry['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or sha(path) != entry['sha256']:
            urls = [entry['url']]
            if entry['url'].startswith('https://huggingface.co/'):
                # Mirrors/CDN caches sometimes serve main when a revision URL is rate limited.
                # The locked SHA-256 is still mandatory, including for JSON/config files.
                urls.append(re.sub(r'/resolve/[0-9a-f]{40}/', '/resolve/main/', entry['url']))
            temporary = path.with_name(path.name+'.part')
            for index, url in enumerate(urls):
                try:
                    with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as stream:
                        while block := response.read(4*1024*1024):
                            stream.write(block)
                    if temporary.stat().st_size != entry['size'] or sha(temporary) != entry['sha256']:
                        raise ValueError('Model integrity mismatch: '+entry['path'])
                    temporary.replace(path)
                    break
                except (OSError, ValueError):
                    if index == len(urls)-1:
                        raise
        path.with_name(path.name+'.source.json').write_text(json.dumps(entry, indent=2)+'\n')
        print('Verified', entry['path'], flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch, entries))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--sources-only', action='store_true')
    group.add_argument('--models-only', action='store_true')
    args = parser.parse_args()
    lock = json.loads((ROOT/'src/song2voice/voice_conversion/svc.lock.json').read_text())
    if not args.models_only:
        sources(lock['sources'])
    if not args.sources_only:
        models(lock['assets'])
