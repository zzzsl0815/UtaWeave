"""Build a local full-song listening page from a completed render manifest."""
import html
import json
from pathlib import Path
import shutil
from urllib.parse import quote

from ..score import sha


def build_report(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    manifest = json.loads(manifest_path.read_text())
    if manifest['status'] != 'complete':
        raise ValueError('A completed render manifest is required')
    esc = lambda value: html.escape(str(value), quote=True)
    outputs = manifest['outputs']
    urls = {}
    for name in ('mix', 'vocal', 'natural'):
        urls[name] = {}
        for kind in ('mp3', 'wav'):
            path = Path(outputs[name][kind])
            path = (root/path).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Report audio must be inside the render directory')
            if sha(path) != outputs[name][kind+'_sha256']:
                raise ValueError('Export hash mismatch: '+name+'/'+kind)
            urls[name][kind] = quote(path.relative_to(root).as_posix())
    duration = float(outputs['mix']['seconds'])
    language = '中文普通话' if manifest.get('language') == 'zh' else '日语'
    model = '原版 VR · 未加载 LoRA' if manifest.get('use_lora') is False else 'VR · 日语 LoRA'
    tracks, downloads = [], []
    for name, label in [('mix', '伴奏混音'), ('vocal', '纯人声')]:
        pressed = 'true' if name == 'mix' else 'false'
        tracks.append(f'<button data-src="{urls[name]["mp3"]}" aria-pressed="{pressed}">{label}</button>')
    for name, label in [('mix', '伴奏混音'), ('vocal', '纯人声'), ('natural', '原速串联')]:
        downloads.append(f'<li>{label}：<a href="{urls[name]["wav"]}" download>WAV</a> · '
                         f'<a href="{urls[name]["mp3"]}" download>MP3</a></li>')
    phrases = []
    for chunk in manifest['plan']['chunks']:
        start = float(chunk['words'][0]['start'])
        lyric = ''.join(word['word'] for word in chunk['words'])
        phrases.append(f'<li><button data-time="{max(0., start-.3):.3f}">'
                       f'{int(start)//60}:{int(start)%60:02d}</button> {esc(lyric)}</li>')
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>UtaWeave 整曲试听</title>
<style>body{{font:16px/1.7 system-ui;margin:24px auto;padding:0 20px;max-width:1000px;background:#f6f8fa;color:#202b36}}
section{{background:white;border-radius:12px;padding:20px;margin:20px 0}}button{{padding:8px 14px;margin:4px;cursor:pointer}}
button[aria-pressed=true]{{background:#215cb1;color:white}}#seek{{width:100%}}audio{{width:100%}}li{{margin:10px 0}}
a{{color:#215cb1}}#playback-time{{font-variant-numeric:tabular-nums}}</style>
<h1>VR＋Seed-VC 整曲试听</h1>
<p>{language} · {model} → Seed-VC</p>
<p>先听咬字和演唱节奏，再比较目标音色、高音稳定性与句间衔接。自动检查通过不代表音质合格。</p>
<section><div id="tracks">{''.join(tracks)}</div><p id="current">当前：伴奏混音。切换音轨时保留播放位置。</p>
<audio id="main" preload="metadata" data-duration="{duration}" src="{urls['mix']['mp3']}"></audio>
<button id="play-toggle" aria-label="播放">播放</button><button data-skip="-5">后退 5 秒</button>
<button data-skip="5">前进 5 秒</button><span id="playback-time"></span>
<input id="seek" aria-label="播放位置" type="range" min="0" max="{duration}" step="0.01" value="0">
<label>音量 <input id="volume" type="range" min="0" max="1" step="0.01" value="1"></label>
<p id="playback-status" role="status">准备就绪</p></section>
<section><h2>唱句跳转</h2><ol>{''.join(phrases)}</ol></section>
<section><h2>原速串联</h2><p>未做时间拉伸，包含句间间隔，与原曲时间轴不同。</p>
<audio controls preload="none" src="{urls['natural']['mp3']}"></audio></section>
<section><h2>下载</h2><ul>{''.join(downloads)}</ul><a href="{quote(manifest_path.name)}">运行记录</a></section>
<script src="listening_player.js"></script></html>'''
    shutil.copyfile(Path(__file__).with_name('listening_player.js'), root/'listening_player.js')
    page = root/'index.html'
    page.write_text(document, encoding='utf-8')
    return page
