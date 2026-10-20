"""SSH-friendly reference review and a small shortlist-only download bundle."""
import csv
import io
import json
from pathlib import Path
import sys
import zipfile

from .selection import select_reference, reference_status
from .score import sha


def candidate_by_number(run, value):
    manifest = json.loads((Path(run)/'reference_candidates.json').read_text())
    exact = [r for r in manifest['candidates'] if r['id']==str(value)]
    if exact:
        return exact[0]
    numbered = [r for i,r in enumerate(manifest['candidates'],1)
                if str(r.get('shortlist_number',i))==str(value)]
    if len(numbered)!=1:
        raise ValueError('Unknown candidate number or id')
    return numbered[0]


def list_candidates(run):
    run = Path(run).resolve()
    manifest = json.loads((run/'reference_candidates.json').read_text())
    def timestamp(t):
        return f'{int(t//60):02d}:{t%60:06.3f}'
    print('编号  原曲起止时间                 时长       SingMOS  处理方式')
    for i,row in enumerate(manifest['candidates'],1):
        processing = '仅分离' if row.get('processing')=='native_vocal' else '去混响' if row.get('processing')=='dereverb' else '未指定'
        duration = row['end_seconds']-row['start_seconds']
        print(f"{row.get('shortlist_number',i):>3}   {timestamp(row['start_seconds'])}–{timestamp(row['end_seconds'])}   {duration:>5.2f}s    {row['score']:>6.3f}   {processing}")
    print('试听文件目录: '+str(run/'candidates'))
    return manifest


def terminal_review(run, show_table=True):
    expected = sha(Path(run)/'reference_candidates.json')
    manifest = list_candidates(run) if show_table else json.loads((Path(run)/'reference_candidates.json').read_text())
    if not sys.stdin.isatty():
        raise ValueError('No interactive terminal. Use select-reference --candidate NUMBER_OR_ID.')
    while True:
        value = input('请输入候选编号（q保留待选状态）: ').strip()
        if value.lower() in ['q','quit','exit']:
            return None
        try:
            row = next((r for i,r in enumerate(manifest['candidates'],1)
                        if value==r['id'] or value==str(r.get('shortlist_number',i))),None)
            if row is None:
                raise ValueError('Unknown candidate number or id')
            return select_reference(run,row['id'],expected_manifest_sha256=expected,
                                    expected_candidate_sha256=row['sha256'],method='terminal')
        except ValueError as error:
            print(str(error))


def export_candidates(run, output):
    run, output = Path(run).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Export already exists; choose a new ZIP filename')
    manifest = json.loads((run/'reference_candidates.json').read_text())
    output.parent.mkdir(parents=True,exist_ok=True)
    records = []
    try:
        with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
            for i,row in enumerate(manifest['candidates'],1):
                from .selection import inside
                path = inside(run,row['path'])
                if sha(path)!=row['sha256']:
                    raise ValueError('Candidate changed before export')
                name = f'{i:02d}_reference.wav'
                archive.write(path,name)
                records.append(dict(number=i,candidate_id=row['id'],file=name,sha256=row['sha256'],
                    score=row['score'],start_seconds=row['start_seconds'],end_seconds=row['end_seconds'],
                    processing=row.get('processing')))
            exported = dict(source_sha256=manifest['source']['audio_sha256'],
                manifest_sha256=sha(run/'reference_candidates.json'),candidates=records)
            archive.writestr('candidates.json',json.dumps(exported,ensure_ascii=False,indent=2))
            table = io.StringIO()
            writer = csv.DictWriter(table,fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
            archive.writestr('candidates.csv',table.getvalue())
            archive.writestr('README.txt','Listen locally, then confirm on the remote host:\n'
                'song2voice select-reference --run RUN --candidate CANDIDATE_ID --manifest-sha256 '+exported['manifest_sha256']+'\n'
                'Only shortlisted WAVs are included. No model weights, full song, or credentials.\n')
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return dict(archive=str(output),candidates=len(records),bytes=output.stat().st_size)
