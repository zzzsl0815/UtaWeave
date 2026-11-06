"""Plan MIDI singing clips from notes and lyric boundaries, never fake audio.

Keep complete LRC phrases when possible; merge short neighbouring phrases and
split overlong ones at note gaps. Both source span and compiled VR duration are
bounded. Lyrics, tied syllables, pitch and note times are immutable here.
"""
import math

from ..conditioning import (NOTE_UNITS, compile_chunk_annotation, compiled_duration,
                            conditioning_bpm, quantize_duration, rest_tokens)

MAX_MIDI_SECONDS = 8.0
MIN_JOIN_GAP = .075
MIN_PHRASE_GAP = .004


def summary(split):
    return dict(planner=split['planner'],chunk_count=split['chunk_count'],
                max_seconds=split['max_content_seconds'],max_condition_seconds=split['max_condition_seconds'],
                longest_content_seconds=max(c['content_seconds'] for c in split['chunks']),
                longest_condition_seconds=max(c['estimated_duration'] for c in split['chunks']),
                connected_short_rests=sum(len(c['annotation'].get('connected_short_rests',[])) for c in split['chunks']),
                short_chunk_ids=split['short_chunk_ids'],chunks=[
                    {key:chunk[key] for key in ('id','content_start','content_end','content_seconds',
                                                'estimated_duration','lyric_phrase_ids','midi_note_indices','kana')}
                    for chunk in split['chunks']])


def plan_midi(score, words, maximum=MAX_MIDI_SECONDS, source_seconds=None, connect_short_rests=False):
    if not math.isfinite(maximum) or maximum<=0:
        raise ValueError('MIDI 每片时长上限必须为正数')
    maximum=min(maximum,MAX_MIDI_SECONDS)
    if not words:raise ValueError('MIDI 中没有可演唱的歌词')
    raw_bpm=score[0]['bpm'];bpm=conditioning_bpm(raw_bpm)
    if any(row['bpm']!=raw_bpm for row in score):raise ValueError('MIDI 条件需要统一的记谱 BPM')
    seconds_per_beat=60/bpm
    rest_cache={}
    def rest_units(seconds):
        if seconds not in rest_cache:rest_cache[seconds]=sum(NOTE_UNITS[t] for t in rest_tokens(seconds,bpm))
        return rest_cache[seconds]
    padding=rest_units(.25)+rest_units(.4)
    # Prefix sums exactly mirror compile_chunk_annotation, including its 75 ms
    # rest threshold. Avoid compiling every candidate window during the search.
    notes_prefix=[0.];rests_prefix=[0.];phrase_prefix=[0];long_gap_prefix=[0]
    gaps=[0.]+[max(0.,b['start']-a['end']) for a,b in zip(words,words[1:])]
    phrase_breaks=[False]+[a.get('phrase_id')!=b.get('phrase_id') for a,b in zip(words,words[1:])]
    for i,word in enumerate(words):
        tokens=word['note'] if bpm==raw_bpm else [quantize_duration(d,bpm) for d in word['pitch_dur']]
        notes_prefix.append(notes_prefix[-1]+sum(NOTE_UNITS[t] for t in tokens))
        rests_prefix.append(rests_prefix[-1]+(rest_units(gaps[i]) if gaps[i]>=MIN_JOIN_GAP else 0.))
        phrase_prefix.append(phrase_prefix[-1]+int(phrase_breaks[i]))
        long_gap_prefix.append(long_gap_prefix[-1]+int(gaps[i]>=1.2))
    count=len(words)
    candidates=[0]+[i for i in range(1,count) if gaps[i]>=MIN_JOIN_GAP or phrase_breaks[i] and gaps[i]>=MIN_PHRASE_GAP]+[count]
    best={0:0.};previous={};budgets={}
    for right in candidates[1:]:
        for left in reversed(candidates):
            if left>=right:continue
            duration=words[right-1]['end']-words[left]['start']
            if duration>maximum+1e-9:break
            if left not in best:continue
            units=padding+notes_prefix[right]-notes_prefix[left]+rests_prefix[right]-rests_prefix[left+1]
            estimate=units*seconds_per_beat
            if estimate>maximum+1e-9:continue
            # Preserve semantic lines. Below three seconds, reward useful
            # context rather than treating every short line as a finished clip.
            cost=20+15*max(0.,3-duration)**2
            cost+=20*(phrase_prefix[right]-phrase_prefix[left+1])
            cost+=120*(long_gap_prefix[right]-long_gap_prefix[left+1])
            if right<count:
                same_phrase=words[right-1].get('phrase_id') is not None and not phrase_breaks[right]
                cost+=(400 if same_phrase else 0)+.6/max(MIN_JOIN_GAP,gaps[right])
            total=best[left]+cost
            if total<best.get(right,float('inf')):
                best[right]=total;previous[right]=left;budgets[right]=estimate
    if count not in best:
        reachable=max(best);word=words[reachable] if reachable<count else words[-1]
        raise ValueError(f"MIDI 在 {word['start']:.2f} 秒附近无法切成不超过 {maximum:g} 秒的片段；"
                         '请检查超长延音或补充至少 75 毫秒的句间休止。不会截断歌词或音符。')
    edges=[];right=count
    while right:
        left=previous[right];edges.append((left,right));right=left
    edges.reverse()
    timeline_end=max(row['source_window']['start_seconds']+sum(row['word_dur']) for row in score)
    source_seconds=timeline_end+1.5 if source_seconds is None else source_seconds
    if source_seconds<words[-1]['end']:raise ValueError('MIDI 时间轴短于最后一个演唱音符')
    def boundary(index):
        if index==0:a,b=0.,words[0]['start']
        elif index==count:a,b=words[-1]['end'],source_seconds
        else:a,b=words[index-1]['end'],words[index]['start']
        return dict(time=(a+b)/2,quiet=[a,b],width=b-a,
                    evidence='midi_note_gap',lyric_boundary=bool(index in (0,count) or phrase_breaks[index]),
                    tight_join=bool(0<index<count and b-a<MIN_JOIN_GAP))
    chunks=[]
    for index,(left,right) in enumerate(edges):
        selected=words[left:right]
        chunk=dict(id=f'chunk_{index:02d}',words=selected,content_start=selected[0]['start'],
                   content_end=selected[-1]['end'],content_seconds=selected[-1]['end']-selected[0]['start'],
                   kana=''.join(w['word'] for w in selected),lyric_lines=sorted({w['line_index'] for w in selected}),
                   lyric_phrase_ids=sorted({w['phrase_id'] for w in selected if w.get('phrase_id') is not None}),
                   midi_note_indices=[n for w in selected for n in w.get('midi_note_indices',[])],
                   left_boundary=boundary(left),right_boundary=boundary(right))
        if connect_short_rests:chunk['connect_short_rests']=True
        chunk['annotation']=compile_chunk_annotation(chunk,raw_bpm)
        chunk['estimated_duration']=compiled_duration(chunk['annotation'])
        if not math.isclose(chunk['estimated_duration'],budgets[right],abs_tol=1e-8):
            raise ValueError('MIDI 切片预算与实际 VR 条件不一致')
        if chunk['estimated_duration']>maximum+1e-9:raise ValueError('MIDI 切片超过时长上限')
        chunks.append(chunk)
    return dict(chunks=chunks,chunk_count=len(chunks),original_phrase_count=len(score),
                lyric_phrase_count=len({w['phrase_id'] for w in words if w.get('phrase_id') is not None}),
                lyric_unit_count=count,all_lyrics_covered_once=True,max_content_seconds=maximum,
                max_condition_seconds=maximum,source_seconds=source_seconds,source_mode='midi',
                planner='midi_lyric_phrases_v1',uses_audio_silence=False,
                short_chunk_ids=[c['id'] for c in chunks if c['content_seconds']<2],
                policy='Prefer full lyric phrases; merge short neighbours; split long phrases at MIDI gaps >=75ms; lyric boundaries may use gaps >=4ms with bounded non-lyric joins; preserve notes and lyrics')
