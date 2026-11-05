"""Editor drafts are separate from the validated score and never launch workers."""
import json
import math
import re
import time

from ..selection import atomic_json


def validate_draft(value, job_id):
    if not isinstance(value, dict) or value.get('schema') != 1 or value.get('job_id') != job_id:
        raise ValueError('草稿格式或任务编号不匹配')
    if not re.fullmatch(r'[a-f0-9]{32}', str(value.get('client_id', ''))):
        raise ValueError('草稿窗口编号无效')
    sequence = value.get('sequence')
    if type(sequence) is not int or not 1 <= sequence <= 2**53-1:
        raise ValueError('草稿序号无效')
    if not isinstance(value.get('prepared_revision'), str):
        raise ValueError('草稿缺少乐谱版本')
    if value.get('reading_mode') not in ('romaji', 'native'):
        raise ValueError('草稿输入方式无效')
    labels = value.get('labels')
    if not isinstance(labels, list) or not 1 <= len(labels) <= 10000 or any(not isinstance(x, str) or len(x) > 64 for x in labels):
        raise ValueError('草稿音符无效')
    if not isinstance(value.get('fields'), dict) or not isinstance(value.get('phrase_drafts'), list):
        raise ValueError('草稿输入字段无效')
    timestamp = value.get('updated_at')
    if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
        raise ValueError('草稿时间无效')
    # Incomplete/invalid spellings are deliberately accepted for later editing.
    json.dumps(value, allow_nan=False)


def save_draft(job, value):
    validate_draft(value, job.name)
    root = job / 'drafts'
    root.mkdir(exist_ok=True)
    path = root / (value['client_id'] + '.json')
    if path.exists():
        previous = json.loads(path.read_text())
        if previous['sequence'] >= value['sequence']:
            return dict(ok=True, sequence=previous['sequence'], saved_at=previous['saved_at'])
    saved = dict(value, saved_at=time.time())
    atomic_json(path, saved)
    return dict(ok=True, sequence=saved['sequence'], saved_at=saved['saved_at'])


def list_drafts(job):
    result = []
    for path in (job / 'drafts').glob('*.json'):
        value = json.loads(path.read_text())
        result.append({k: value[k] for k in ('client_id', 'sequence', 'prepared_revision', 'updated_at', 'saved_at')})
        if value.get('title'):result[-1]['title']=value['title']
    return sorted(result, key=lambda x: x['saved_at'], reverse=True)
