"""Explicit monophonic MIDI/lyric editing; no inferred ground-truth alignment."""
from bisect import bisect_right
from copy import deepcopy
import json
import math
from pathlib import Path
import re

from .conditioning import quantize_duration, require_conditioning


def inspect_midi(path):
    import mido
    try:
        midi = mido.MidiFile(path, charset='utf-8')
    except UnicodeDecodeError:
        midi = mido.MidiFile(path, charset='shift_jis')
    if midi.type == 2 or midi.ticks_per_beat <= 0:
        raise ValueError('仅支持共享时间轴的 PPQ MIDI（type 0/1）')
    tempos = {0: 500000}
    for track in midi.tracks:
        tick = 0
        for event in track:
            tick += event.time
            if event.type == 'set_tempo':
                if event.tempo <= 0: raise ValueError('MIDI 速度必须为正数')
                tempos[tick] = event.tempo
    ticks = sorted(tempos)
    seconds = [0.]
    for left, right in zip(ticks, ticks[1:]):
        seconds.append(seconds[-1]+mido.tick2second(right-left, midi.ticks_per_beat, tempos[left]))
    def clock(tick):
        index = bisect_right(ticks, tick)-1
        return seconds[index]+mido.tick2second(tick-ticks[index], midi.ticks_per_beat, tempos[ticks[index]])
    tracks = []
    for index, track in enumerate(midi.tracks):
        tick, active, notes, lyrics = 0, {}, [], {}
        for event in track:
            tick += event.time
            if event.type == 'lyrics': lyrics[tick] = event.text.strip()
            if event.type not in ('note_on', 'note_off') or event.channel == 9: continue
            key = (event.channel, event.note)
            if event.type == 'note_on' and event.velocity:
                if key in active: raise ValueError('同一音高重复起音而未结束，请先整理 MIDI')
                active[key] = tick
            elif key in active:
                start = active.pop(key)
                if tick <= start: continue
                notes.append(dict(start=clock(start), end=clock(tick), pitch=event.note,
                                  tick=start, end_tick=tick, channel=event.channel))
        if active: raise ValueError('MIDI 存在未结束的音符')
        notes.sort(key=lambda n: (n['start'], n['pitch']))
        if not notes: continue
        if len(notes)>5000: raise ValueError('单轨音符超过 5000 个，请分段导入')
        for i, note in enumerate(notes):
            note.update(index=i, lyric=lyrics.get(note['tick'], ''))
        overlap = any(a['end']>b['start']+.001 for a,b in zip(notes, notes[1:]))
        tracks.append(dict(index=index, name=track.name or f'音轨 {index+1}', notes=notes,
                           polyphonic=overlap, count=len(notes), seconds=notes[-1]['end']))
    if not tracks: raise ValueError('MIDI 中没有非打击乐音符')
    return dict(tracks=tracks, bpm=60000000/tempos[0],
                tempo_changes=[dict(seconds=clock(t), bpm=60000000/tempos[t]) for t in ticks],
                warning='速度变化按真实秒数保留；VR 音符类别按初始速度近似量化。' if len(ticks)>1 else None)


def clean_lyrics(text):
    # Keep the original LRC separately; do not include timestamps or metadata in singing.
    lines=[]
    for line in str(text).splitlines():
        if re.match(r'^\[(?:ar|ti|al|by|offset|length|re|ve):',line,re.I):continue
        line=re.sub(r'\[[^\]]*\]','',line).strip()
        if line: lines.append(line)
    return '\n'.join(lines)


def phonemize(text=None, readings=None):
    """Executed in the labeling environment, where pyopenjtalk is installed."""
    import pyopenjtalk
    def katakana(s):
        return ''.join(chr(ord(c)+0x60) if 'ぁ'<=c<='ゖ' else c for c in s)
    def phones(s):
        if not s or re.search(r'[^ぁ-ゖァ-ヺー]',s):
            raise ValueError('音符读音请使用日语假名；延音音符填写 -，休止填写 SP')
        result=pyopenjtalk.g2p(katakana(s)).split()
        result=[p.lower() if p in ('I','U') else p for p in result if p not in ('sil','pau')]
        if not result:raise ValueError('无法确定读音：'+s+'；长音请填对应元音或 -')
        return result
    if readings is not None:
        return {s:phones(s) for s in dict.fromkeys(readings) if s not in ('-','SP','AP')}
    reading=pyopenjtalk.g2p(clean_lyrics(text),kana=True)
    moras=[]
    for c in reading:
        if c in 'ァィゥェォャュョヮ' and moras:moras[-1]+=c
        elif c=='ー' and moras:
            vowel=phones(moras[-1])[-1]
            if vowel in 'aiueo':moras.append(dict(a='ア',i='イ',u='ウ',e='エ',o='オ')[vowel])
        elif 'ァ'<=c<='ヺ':moras.append(c)
    return dict(reading=reading, moras=moras,
                warning='自动读音和顺序分配仅为草稿；人名、助词、唱法及一字多音需要核对。')


def notes_to_score(notes, labels, phones, bpm, lead_in=.5):
    if not notes or len(labels)!=len(notes): raise ValueError('每个音符都需要读音、延音 - 或休止 SP')
    if any(not str(s).strip() for s in labels):raise ValueError('仍有未分配歌词的音符')
    offset=max(0.,lead_in-float(notes[0]['start']))
    if notes[-1]['end']+offset>1200:raise ValueError('单次歌曲最长 20 分钟，请分段处理')
    score=[];row=None;cursor=None
    for note,label in zip(notes,labels):
        label=str(label).strip();start=float(note['start'])+offset;end=float(note['end'])+offset
        pitch=note['pitch']
        if not all(math.isfinite(x) for x in (start,end)) or end<=start or not 1<=pitch<=127:
            raise ValueError('MIDI 音符时间或音高无效')
        if cursor is not None and start<cursor-.001:raise ValueError('所选旋律轨有重叠音符，请选择单声部轨道')
        if row is None or start-cursor>=.25:
            if label=='-':raise ValueError('新唱句的第一个音符不能是延音 -')
            row=dict(item_name=f'midi_{len(score):03d}',line_index=len(score),
                     source_window=dict(start_seconds=start),bpm=bpm,has_score=True,
                     word=[],word_dur=[],pitch=[],pitch_dur=[],pitch2word=[],note=[],ph=[],ph2word=[])
            score.append(row);cursor=start
        gap=max(0.,start-cursor)
        if label=='-':
            if gap>.001 or row['word'][-1] in ('SP','AP'):raise ValueError('延音 - 必须紧接前一个有歌词的音符，不能跨过休止')
            owner=len(row['word'])-1;row['word_dur'][owner]+=end-cursor
            duration=end-cursor
        else:
            if gap>1e-5:
                owner=len(row['word']);row['word'].append('SP');row['word_dur'].append(gap)
                row['pitch'].append(0);row['pitch_dur'].append(gap);row['pitch2word'].append(owner)
                row['note'].append(quantize_duration(gap,bpm));row['ph'].append('SP');row['ph2word'].append(owner)
            owner=len(row['word']);duration=end-start
            row['word'].append(label);row['word_dur'].append(duration)
            phonemes=['SP'] if label in ('SP','AP') else phones[label]
            row['ph'].extend(phonemes);row['ph2word'].extend([owner]*len(phonemes))
        row['pitch'].append(0 if label in ('SP','AP') else pitch);row['pitch_dur'].append(duration)
        row['pitch2word'].append(owner);row['note'].append(quantize_duration(duration,bpm));cursor=end
    if not any(w not in ('SP','AP') for r in score for w in r['word']):raise ValueError('至少需要一个有歌词的音符')
    require_conditioning(score)
    return score,offset


def edit_score(score, edits, phones):
    result=deepcopy(score)
    originals=[(r,i) for r in result for i,w in enumerate(r['word']) if w not in ('SP','AP')]
    if len(edits)!=len(originals):raise ValueError('歌词单元数量与原乐谱不符')
    for (row,index),text in zip(originals,edits):
        if not text or text in ('SP','AP','-'):raise ValueError('原曲模式仅可修正读音，不能删除已有歌词单元')
        if text==row['word'][index]:continue
        row['word'][index]=text
        pairs=[(p,o) for p,o in zip(row['ph'],row['ph2word']) if o!=index]
        pairs.extend((p,index) for p in phones[text]);pairs.sort(key=lambda x:x[1])
        row['ph']=[p for p,o in pairs];row['ph2word']=[o for p,o in pairs]
    require_conditioning(result)
    return result


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('request',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();request=json.loads(a.request.read_text())
    a.output.write_text(json.dumps(phonemize(**request),ensure_ascii=False,indent=2)+'\n')
