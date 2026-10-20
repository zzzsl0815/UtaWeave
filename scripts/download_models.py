"""Download selected official models; existing model directories are preserved."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def safe_extract(archive, destination):
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            target=(destination/member.filename).resolve()
            if not target.is_relative_to(destination.resolve()) or stat.S_ISLNK(member.external_attr >> 16):
                raise ValueError('Unsafe archive entry: '+member.filename)
        package.extractall(destination)


def download(row, root):
    target=root/row['root']
    if target.exists():raise ValueError('Model directory exists; verify it or choose another --root: '+str(target))
    source=row['source']
    if source.get('type')=='huggingface' or row['id']=='qwen3-asr':
        from huggingface_hub import snapshot_download
        repo=source['repo'];revision=source['revision'];subfolder=source.get('subfolder')
        if subfolder:
            # Snapshot downloads retain the upstream subfolder layout.
            snapshot_download(repo,revision=revision,allow_patterns=subfolder+'/*',local_dir=root)
        else:
            snapshot_download(repo,revision=revision,local_dir=target)
        return
    if not source.get('official_url'):
        raise ValueError('No verified automatic download for '+row['id']+'; see docs/MODELS.md')
    root.mkdir(parents=True,exist_ok=True)
    archive=root/(row['id']+'.download.zip')
    urllib.request.urlretrieve(source['official_url'],archive)
    digest=hashlib.sha256()
    with archive.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024**2),b''):digest.update(block)
    if digest.hexdigest()!=source['official_sha256']:raise ValueError('Download checksum mismatch')
    unpack=root/('.unpack-'+row['id'])
    if unpack.exists():raise ValueError('Previous unpack directory exists: '+str(unpack))
    unpack.mkdir()
    safe_extract(archive,unpack)
    # Find the directory matching an actual model member from the lock.
    member=next((f['path'] for f in row['files'] if f['path'] in ('model.onnx','model.pt')),row['files'][0]['path'])
    possibilities=[p.parent for p in unpack.rglob(Path(member).name) if (p.parent/member).is_file()]
    if len(possibilities)!=1:raise ValueError('Ambiguous model package layout')
    shutil.move(str(possibilities[0]),str(target))
    if unpack.exists():shutil.rmtree(unpack)
    archive.unlink()
    print('Downloaded',row['id'],target)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT/'models')
    parser.add_argument('--only',nargs='+',required=True)
    args=parser.parse_args()
    rows={r['id']:r for r in json.loads((ROOT/'manifests/models.lock.json').read_text())['models']}
    for name in args.only:
        if name not in rows:parser.error('Unknown model ID: '+name)
        download(rows[name],args.root)
