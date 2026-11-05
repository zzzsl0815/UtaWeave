"""Persistent, verified reference snapshots for human and automatic choices."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
import os

from .score import sha


class SelectionRequired(ValueError):
    pass


class StaleSelection(ValueError):
    pass


def atomic_json(path, value):
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.'+path.name, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def selection_lock(run):
    # GPU execution and selection locking currently target Linux.
    import fcntl
    with (run/'.reference_selection.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def inside(run, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError('Candidate paths must be relative to the task directory')
    path = (run/relative).resolve()
    if not path.is_relative_to(run):
        raise ValueError('Candidate path leaves the task directory')
    return path


def resolve_reference_run(run):
    run = Path(run).resolve()
    if not (run/'reference_candidates.json').exists() and (run/'latest.json').exists():
        value = json.loads((run/'latest.json').read_text())['run']
        target = (run/Path(value)).resolve()
        if not target.is_relative_to(run) or target == run:
            raise ValueError('Reference latest pointer leaves its output directory')
        run = target
    return run


def reference_status(run):
    run = resolve_reference_run(run)
    manifest_path = run/'reference_candidates.json'
    manifest = json.loads(manifest_path.read_text())
    result = dict(state='awaiting_reference_selection', selection=None,
                  manifest_sha256=sha(manifest_path), source_sha256=manifest['source']['audio_sha256'])
    selected_path = run/'reference.selected.json'
    if not selected_path.exists():
        return result
    try:
        selected = json.loads(selected_path.read_text())
        candidate = next((c for c in manifest['candidates'] if c['id']==selected['candidate_id']), None)
        if not candidate or candidate['sha256']!=selected['sha256']:
            raise ValueError('The selected candidate no longer matches the shortlist')
        if selected['source_sha256']!=result['source_sha256']:
            raise ValueError('The selected reference belongs to a different source')
        if selected.get('manifest_sha256',result['manifest_sha256'])!=result['manifest_sha256']:
            raise ValueError('The shortlist changed; confirm the reference again')
        # Version 0 records used an absolute candidate path. They remain
        # readable until explicitly reselected into a portable frozen snapshot.
        audio = Path(selected['audio'])
        audio = audio.resolve() if audio.is_absolute() else inside(run,selected['audio'])
        if not audio.is_relative_to(run) or sha(audio)!=selected['sha256']:
            raise ValueError('The saved reference audio is missing or changed')
        recipe = selected.get('preprocessing_recipe_sha256')
        if recipe and sha(run/'reference_recipe.json')!=recipe:
            raise ValueError('The reference preparation record changed')
        result.update(state='ready',selection=selected)
    except (ValueError, OSError, KeyError, TypeError) as error:
        result.update(state='invalid_reference',error=str(error))
    return result


def require_reference(run):
    run = resolve_reference_run(run)
    status = reference_status(run)
    if status['state']!='ready':
        raise SelectionRequired(status.get('error','Reference preparation has not completed'))
    result = dict(status['selection'])
    result['audio'] = str((run/result['audio']).resolve())
    return result


def select_reference(run, candidate_id, *, expected_manifest_sha256=None,
                     expected_candidate_sha256=None, method='cli', decision=None):
    run = Path(run).resolve()
    with selection_lock(run):
        manifest_path = run/'reference_candidates.json'
        manifest_hash = sha(manifest_path)
        if expected_manifest_sha256 and manifest_hash!=expected_manifest_sha256:
            raise StaleSelection('The shortlist changed; refresh before confirming')
        manifest = json.loads(manifest_path.read_text())
        found = [c for c in manifest['candidates'] if c['id']==candidate_id]
        if len(found)!=1:
            raise ValueError('Choose a candidate from the prepared shortlist')
        candidate = found[0]
        if expected_candidate_sha256 and candidate['sha256']!=expected_candidate_sha256:
            raise StaleSelection('The candidate changed; refresh before confirming')
        source = inside(run,candidate['path'])
        if sha(source)!=candidate['sha256']:
            raise ValueError('Selected reference file changed')
        folder = run/'selected_reference'
        folder.mkdir(exist_ok=True)
        target = folder/(candidate['sha256']+source.suffix.lower())
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=folder,delete=False) as stream:
                temporary = Path(stream.name)
            try:
                shutil.copyfile(source,temporary)
                if sha(temporary)!=candidate['sha256']:
                    raise ValueError('Candidate changed while freezing the reference')
                os.replace(temporary,target)
            finally:
                temporary.unlink(missing_ok=True)
        if sha(target)!=candidate['sha256']:
            raise ValueError('Frozen reference file changed')
        if decision is not None and (not isinstance(decision, dict) or not decision.get('strategy')):
            raise ValueError('Automatic selection requires a recorded decision strategy')
        value = dict(schema_version=1,status=('provided_clip' if method == 'provided_clip' else
            'automatic_selected' if decision is not None else 'human_selected'),candidate_id=candidate_id,
            source_sha256=manifest['source']['audio_sha256'],manifest_sha256=manifest_hash,
            audio=str(target.relative_to(run)),sha256=candidate['sha256'],score=candidate['score'],
            processing=candidate.get('processing'),
            source_window=dict(start_seconds=candidate['start_seconds'],end_seconds=candidate['end_seconds']))
        if decision is not None:
            value['decision'] = decision
        if (run/'reference_recipe.json').exists():
            value['preprocessing_recipe_sha256'] = sha(run/'reference_recipe.json')
        path = run/'reference.selected.json'
        if path.exists():
            previous = json.loads(path.read_text())
            if all(previous.get(k)==v for k,v in value.items()):
                return previous
            history = run/'selection_history'
            history.mkdir(exist_ok=True)
            atomic_json(history/(sha(path)[:16]+'.json'),previous)
        value.update(confirmed_at=datetime.now(timezone.utc).isoformat(),confirmed_via=method)
        atomic_json(path,value)
        atomic_json(run/'progress.json',dict(status='ready',selection=value['status'],candidate_id=candidate_id))
        return value
