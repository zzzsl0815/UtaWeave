"""Portable local listening page: native files plus gain-only matched previews."""
import html
import json
import math
from pathlib import Path

from ..score import sha
from ..selection import atomic_json

LABELS = {'guide': 'VR 原始导唱', 'seed_vc': 'Seed-VC · 44.1 kHz'}


def preview(source, destination):
    import numpy as np
    import soundfile as sf
    audio, sr = sf.read(source, dtype='float32')
    source_hash = sha(source)
    sidecar = destination.with_suffix('.json')
    if sidecar.exists() and destination.exists():
        old = json.loads(sidecar.read_text())
        if old.get('source_sha256') == source_hash and old.get('sha256') == sha(destination):
            return old
    rms = float(np.sqrt(np.mean(audio.astype(float)**2)))
    peak = float(np.max(np.abs(audio)))
    # One scalar gain only: no compression, denoising, stretching, or resampling.
    gain = min(10**(-22/20)/max(rms, 1e-10), .95/max(peak, 1e-10))
    destination.parent.mkdir(parents=True, exist_ok=True)
    sf.write(destination, audio*gain, sr, subtype='PCM_24')
    row = dict(source_sha256=source_hash, gain_db=20*math.log10(gain),
               sample_rate=sr, sha256=sha(destination), target_rms_dbfs=-22)
    atomic_json(sidecar, row)
    return row


def build_report(run, recipe):
    run = Path(run)
    esc = lambda value: html.escape(str(value), quote=True)
    def audio_cell(source, name):
        source = Path(source)
        listen = run/'listen'/name
        details = preview(source, listen)
        return (f'<audio controls preload="none" src="{esc(listen.relative_to(run))}"></audio>'
                f'<p><a href="{esc(source.relative_to(run))}" download>原生 WAV</a> · '
                f'试听增益 {details["gain_db"]:+.1f} dB</p>')
    reference_html = audio_cell(run/'inputs'/recipe['inputs']['reference'], 'reference.wav')
    reference = recipe['reference']
    reference_html += f'<p>已选参考：{esc(reference["candidate_id"])}</p>'
    if reference.get('source_window'):
        window = reference['source_window']
        reference_html += (f'<p>原曲窗口：{window["start_seconds"]:.3f}–'
                           f'{window["end_seconds"]:.3f} 秒</p>')
    rows = []
    columns = ['guide', 'seed_vc']
    for case in recipe['cases']:
        cells = []
        for name in columns:
            if name == 'guide':
                source = run/'inputs'/recipe['inputs']['guides'][case['id']]
                info = ''
            else:
                result_path = run/name/(case['id']+'.json')
                try:
                    result = json.loads(result_path.read_text())
                    if not isinstance(result, dict):
                        result = {}
                    if result.get('status') == 'complete':
                        if sha(result['audio']) != result['audio_sha256']:
                            raise ValueError('Audio hash mismatch')
                except (OSError, ValueError, KeyError, TypeError):
                    result = dict(error='结果缺失或校验失败，等待重新生成')
                if result.get('status') != 'complete':
                    cells.append('<td class="pending">'+esc(result.get('error', '尚未生成'))+'</td>')
                    continue
                source = Path(result['audio'])
                info = f'<p>{result["seconds"]:.2f}s · 时长变化 {result["duration_delta_ms"]:+.1f}ms</p>'
            cells.append('<td>'+audio_cell(source, f'{case["id"]}_{name}.wav')+info+'</td>')
        description = (f'<strong>{esc(case["id"])} · {esc(case.get("title", ""))}</strong>'
            f'<p>{esc(case.get("artist", ""))} · {esc(case.get("lyric", ""))}</p><p>{esc(case.get("kana", ""))}</p>'
            f'<p class="tags">{esc(" / ".join(case.get("tags", [])))}</p>')
        rows.append('<tr><td>'+description+'</td>'+''.join(cells)+'</tr>')
    document = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>UtaWeave 演唱结果</title>
<style>body{font:16px/1.6 system-ui,sans-serif;margin:32px;color:#202b36;background:#f6f8fa}
main{max-width:1700px;margin:auto}h1{font-size:28px}p{margin:6px 0}aside{background:#fff;padding:20px;border-radius:12px;margin:20px 0}
table{width:100%;border-collapse:collapse;background:white}th,td{text-align:left;padding:16px;border:1px solid #dce2e8;vertical-align:top}
th{background:#e8eef6}audio{width:230px;max-width:100%}.scroll{overflow:auto}.tags{color:#355a7c}.pending{color:#9a4524}
button{padding:8px 14px;cursor:pointer}a{color:#215cb1}small{color:#586575}</style>
<main><h1>VR 导唱 → Seed-VC</h1>
<p>复用 VR 导唱 · 固定目标参考 · 原调原速</p>
<aside><strong>目标音色参考</strong>REFERENCE
<p>播放器采用单一增益匹配试听音量；原生 WAV 保留后端采样率与幅度。没有添加混响、降噪或时间拉伸。</p>
<p>建议逐句比较：①咬字、长音和促音；②滑音、颤音和气口；③与目标声音的相似度；④粗糙感、金属感及高音稳定性。</p>
<small>标签为选样依据或待听辨现象，不代表人工确认。此页用于试听检查，不代表已完成主观质量评估。</small>
<p><button onclick="document.querySelectorAll('audio').forEach(a=>a.pause())">全部暂停</button>
<a href="recipe.json">复现记录</a> · <a href="status.json">运行状态</a></p></aside>
<div class="scroll"><table><thead><tr><th>短句</th>HEADERS</tr></thead><tbody>ROWS</tbody></table></div>
<p>RUN</p></main><script>document.querySelectorAll('audio').forEach(a=>a.addEventListener('play',()=>{
document.querySelectorAll('audio').forEach(b=>{if(a!==b)b.pause()})}));</script></html>'''
    document = document.replace('REFERENCE', reference_html).replace('HEADERS',
        ''.join('<th>'+esc(LABELS[n])+'</th>' for n in columns)).replace('ROWS', ''.join(rows)).replace('RUN', esc(run.name))
    (run/'index.html').write_text(document, encoding='utf-8')
