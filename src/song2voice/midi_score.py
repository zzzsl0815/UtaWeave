"""Explicit monophonic MIDI/lyric editing; no inferred ground-truth alignment."""
from bisect import bisect_right
from copy import deepcopy
import json
import math
from pathlib import Path
import re

from .conditioning import quantize_duration, require_conditioning, conditioning_bpm


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
    bpm=60000000/tempos[0]
    return dict(tracks=tracks, bpm=bpm,render_bpm=conditioning_bpm(bpm),
                tempo_changes=[dict(seconds=clock(t), bpm=60000000/tempos[t]) for t in ticks],
                warning='速度变化按真实秒数保留；VR 音符类别按初始速度近似量化。' if len(ticks)>1 else None)


def split_lyrics_text(text):
    # Keep the original LRC separately; do not include timestamps or metadata in singing.
    lines=[];excluded=[]
    for line in str(text).splitlines():
        if re.match(r'^\[(?:ar|ti|al|by|offset|length|re|ve):',line,re.I):continue
        line=re.sub(r'\[[^\]]*\]','',line).strip()
        credit=r'(?:作词|作詞|作曲|编曲|編曲|制作人|製作人|プロデューサー|演唱|歌手|词曲|詞曲|混音|母带|母帶|录音|錄音|Lyrics?\s+by|Composed\s+by|Arranged\s+by|Produced\s+by)'
        if re.match(r'^'+credit+r'(?:\s*[/&、]\s*'+credit+r')*\s*[:：]',line,re.I):
            excluded.append(line);continue
        if line: lines.append(line)
    return dict(text='\n'.join(lines),excluded_credit_lines=excluded)


def clean_lyrics(text):
    return split_lyrics_text(text)['text']


def require_edited_mapping(prepared,labels):
    if prepared.get('alignment') and prepared['alignment'].get('unassigned_units',0)>0:
        raise ValueError('仍有整句读音未分配，请在按句编辑中修正范围或读音并重新分配，不能跳过这些歌词生成。')
    if prepared.get('unmatched_moras',0)>0 and labels==prepared['labels']:
        raise ValueError(f"自动歌词草稿还有 {prepared['unmatched_moras']} 个拍音未分配，不能直接生成。请先修改逐音符对应：一个音符可填多个拍音，延音填 -；也可返回输入页修正歌词范围。")


def phonemize(text=None, readings=None, texts=None, english_runtime=None, language='ja', indexed=False):
    """Chinese characters or Japanese moras, plus explicit English words."""
    from .language import song_language
    if song_language({'language':language}) == 'zh':
        from .chinese_g2p import phonemize_chinese
        return phonemize_chinese(text, readings, texts, english_runtime, indexed)
    import pyopenjtalk
    import unicodedata
    from functools import lru_cache
    english=None
    vowels=dict(a='ア',i='イ',u='ウ',e='エ',o='オ')
    latin=r"[A-Za-z]+(?:['’][A-Za-z]+)*"
    def english_phones(word):
        nonlocal english
        if english is None:
            if not english_runtime:raise ValueError('英文歌词需要配置已有 TiFA 英文 G2P 资产，或手动填写假名读音')
            import sys
            sys.path.insert(0,str(Path(english_runtime['upstream_repo']).resolve()))
            from inference.TiFA.g2p.converters.lstm import LSTMConverter
            root=Path(english_runtime['tifa_model'])
            english=LSTMConverter(dict_path=str(root/'dictionaries/ds_cmudict-07b.txt'),
                model_path=str(root/'assets/LstmG2p-Eng'),beam_size=4)
        result=english.convert(word.lower())[0].readings[0].paths[0]
        phones=[p for group in result for p in group.phonemes]
        if not phones:raise ValueError('无法确定英文单词读音：'+word)
        return ['en/'+p.removeprefix('en/') for p in phones]
    @lru_cache(maxsize=2048)
    def japanese_phones(s):
        s=''.join(chr(ord(c)+0x60) if 'ぁ'<=c<='ゖ' else c for c in s)
        result=pyopenjtalk.g2p(s).split()
        result=[p.lower() if p in ('I','U') else p for p in result if p not in ('sil','pau')]
        if not result:raise ValueError('无法确定读音：'+s)
        return result
    def split_runs(s):
        return re.split('('+latin+')',unicodedata.normalize('NFKC',s))
    def phones(s):
        if not s or re.search(r"[^ぁ-ゖァ-ヺーA-Za-z'’\s]",s):
            raise ValueError('音符读音请使用日语假名或英文单词；延音填 -，休止填 SP')
        result=[]
        for part in split_runs(s):
            if re.fullmatch(latin,part):result.extend(english_phones(part))
            elif part.strip():result.extend(japanese_phones(part.strip()))
        return result
    if readings is not None:
        return {s:phones(s) for s in dict.fromkeys(readings) if s not in ('-','SP','AP')}
    def phrase(s):
        # Furigana supplies a reading, rather than an additional sung word.
        normalized=re.sub(r'[一-龯々〆ヵヶ]+[（(]([ぁ-ゖァ-ヺー]+)[）)]',r'\1',clean_lyrics(s))
        units=[];warnings=[]
        for part in split_runs(normalized):
            if re.fullmatch(latin,part):
                ph=english_phones(part)
                weight=max(1,sum(p.removeprefix('en/') in ('aa','ae','ah','ao','aw','ax','ay','eh','er','ey','ih','iy','ow','oy','uh','uw') for p in ph))
                units.append(dict(text=part,lang='en',weight=weight,phones=ph,vowel=None))
                warnings.append('含英文词：保留英文发音，需试听确认演唱与断词')
                continue
            if not re.search(r'[一-龯ぁ-ゖァ-ヺ0-9]',part):continue
            reading=pyopenjtalk.g2p(part,kana=True);moras=[]
            for c in reading:
                if c in 'ァィゥェォャュョヮ' and moras:moras[-1]+=c
                elif c=='ー' and moras:
                    last=japanese_phones(moras[-1])[-1]
                    if last in vowels:moras.append(vowels[last])
                    else:warnings.append('未能识别长音元音，请修改本句读音')
                elif 'ァ'<=c<='ヺ':moras.append(c)
            for mora in moras:
                ph=japanese_phones(mora)
                units.append(dict(text=mora,lang='ja',weight=1,phones=ph,vowel=vowels.get(ph[-1])))
        return dict(reading=' '.join(u['text'] for u in units),moras=[u['text'] for u in units],units=units,
            warnings=list(dict.fromkeys(warnings)),warning='自动读音与音符分配是草稿，分句时间和发音需要核对。')
    if texts is not None:return dict(lines=[phrase(s) for s in texts])
    return phrase(text)


def notes_to_score(notes, labels, phones, bpm, lead_in=.5, phrases=None):
    if not notes or len(labels)!=len(notes): raise ValueError('每个音符都需要读音、延音 - 或休止 SP')
    if any(not str(s).strip() for s in labels):raise ValueError('仍有未分配歌词的音符')
    offset=max(0.,lead_in-float(notes[0]['start']))
    if notes[-1]['end']+offset>1200:raise ValueError('单次歌曲最长 20 分钟，请分段处理')
    source_bpm=bpm;bpm=conditioning_bpm(bpm)
    owners=[None]*len(notes)
    if phrases is not None:
        last=0;ids=set()
        for phrase in phrases:
            left,right=phrase['note_start'],phrase['note_end'];ident=phrase['id']
            if type(left) is not int or type(right) is not int or not last<=left<=right<=len(notes) or type(ident) is not int or ident in ids:
                raise ValueError('LRC 分句范围必须有序、不重叠，且句子编号唯一')
            owners[left:right]=[ident]*(right-left);last=right;ids.add(ident)
    score=[];row=None;cursor=None
    for note_index,(note,label) in enumerate(zip(notes,labels)):
        label=str(label).strip();start=float(note['start'])+offset;end=float(note['end'])+offset
        pitch=note['pitch']
        if not all(math.isfinite(x) for x in (start,end)) or end<=start or not 1<=pitch<=127:
            raise ValueError('MIDI 音符时间或音高无效')
        if cursor is not None and start<cursor-.001:raise ValueError('所选旋律轨有重叠音符，请选择单声部轨道')
        # These are semantic lyric lines, not blocks delimited by MIDI note-off
        # gaps. A tied syllable stays atomic even across a supplied LRC boundary.
        if row is None or owners[note_index]!=row['phrase_id'] and label!='-':
            if label=='-':raise ValueError('新唱句的第一个音符不能是延音 -')
            row=dict(item_name=f'midi_{len(score):03d}',line_index=len(score),
                     source_window=dict(start_seconds=start),bpm=bpm,source_bpm=source_bpm,has_score=True,
                     source_mode='midi',phrase_id=owners[note_index],has_lyric_phrases=phrases is not None,
                     word=[],word_dur=[],pitch=[],pitch_dur=[],pitch2word=[],note=[],ph=[],ph2word=[],note_indices=[])
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
                row['note_indices'].append(None)
                row['note'].append(quantize_duration(gap,bpm));row['ph'].append('SP');row['ph2word'].append(owner)
            owner=len(row['word']);duration=end-start
            row['word'].append(label);row['word_dur'].append(duration)
            phonemes=['SP'] if label in ('SP','AP') else (phones[note_index] if isinstance(phones,list) else phones[label])
            row['ph'].extend(phonemes);row['ph2word'].extend([owner]*len(phonemes))
        row['pitch'].append(0 if label in ('SP','AP') else pitch);row['pitch_dur'].append(duration)
        row['pitch2word'].append(owner);row['note'].append(quantize_duration(duration,bpm));row['note_indices'].append(note_index);cursor=end
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
