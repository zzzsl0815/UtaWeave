"""Verify local model bytes against the archived manifest; never loads pickle."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024**2),b''):h.update(block)
    return h.hexdigest()


def verify(root, names=None):
    rows=json.loads((ROOT/'manifests/models.lock.json').read_text())['models']
    failures=[]
    known={r['id'] for r in rows}
    if names and set(names)-known:raise ValueError('Unknown model ID')
    for row in rows:
        if names and row['id'] not in names:continue
        location=ROOT/row['root'] if row['root'].startswith('weights/') else root/row['root']
        for item in row['files']:
            path=location/item['path']
            if not path.is_file() or path.stat().st_size!=item['bytes'] or digest(path)!=item['sha256']:
                failures.append(str(path))
        print(row['id'],len(row['files']),'files')
    if failures:raise SystemExit('Missing or changed model files:\n'+'\n'.join(failures))
    print('All requested model files match SHA-256.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT/'models')
    parser.add_argument('--only',nargs='+')
    args=parser.parse_args()
    verify(args.root,args.only)
