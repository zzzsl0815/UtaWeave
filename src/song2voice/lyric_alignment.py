"""LRC-assisted lyric drafts. Timing and allocation scores are heuristics, not truth."""
from bisect import bisect_left
import math
import re

import numpy as np


STAMP=re.compile(r'\[(\d+):(\d{1,2}(?:\.\d+)?)\]')


def parse_lrc(text):
    from .midi_score import split_lyrics_text
    events=[];untimed=[]
    for source_line,line in enumerate(str(text).splitlines()):
        stamps=list(STAMP.finditer(line))
        cleaned=split_lyrics_text(line)
        if cleaned['excluded_credit_lines']:continue
        content=cleaned['text']
        if not stamps:
            if content:untimed.append(content)
            continue
        for stamp in stamps:
            second=float(stamp[2])
            if second>=60:raise ValueError('LRC 秒数必须小于 60')
            events.append(dict(time=int(stamp[1])*60+second,text=content,source_line=source_line))
    events.sort(key=lambda e:e['time'])
    unique=[]
    for event in events:
        if unique and abs(event['time']-unique[-1]['time'])<1e-6:
            if event['text']==unique[-1]['text'] or not event['text']:continue
            if not unique[-1]['text']:unique[-1]=event;continue
            raise ValueError('同一 LRC 时间戳有不同歌词，请先选择原文，移除并列翻译')
        unique.append(event)
    lines=[]
    for i,event in enumerate(unique):
        if event['text']:
            lines.append(dict(id=len(lines),**event,end=unique[i+1]['time'] if i+1<len(unique) else None))
    offset=re.search(r'\[offset:\s*([+-]?\d+)\s*\]',str(text),re.I)
    return dict(lines=lines,untimed_lines=untimed,offset_tag_ms=int(offset[1]) if offset else 0)


def fit_clock(notes,lines,offset=None,scale=None):
    """Find a small affine clock correction against MIDI onset/gap evidence."""
    starts=np.array([n['start'] for n in notes]);ends=np.array([n['end'] for n in notes])
    times=np.array([line['time'] for line in lines]);gaps=starts-np.r_[0.,ends[:-1]]
    # Weak onsets remain candidates; rests make phrase starts more plausible.
    onset_penalty=.22*np.exp(-np.maximum(gaps,0)/.12)
    def evaluate(shifts,ratio):
        target=times[None,:]*ratio+shifts[:,None]
        at=np.searchsorted(starts,target)
        lo=np.clip(at-1,0,len(starts)-1);hi=np.clip(at,0,len(starts)-1)
        left=np.abs(target-starts[lo]);right=np.abs(target-starts[hi])
        dist=np.minimum(left+onset_penalty[lo],right+onset_penalty[hi])
        # Large misses cannot be hidden by a dense middle section.
        cost=np.mean(np.minimum(dist,2)**2,axis=1)
        cost+=.2*np.maximum(0,starts[0]-target[:,0]-.6)**2
        cost+=.12*np.maximum(0,np.abs(target[:,0]-starts[0])-.2)**2
        cost+=.2*np.maximum(0,target[:,-1]-ends[-1])**2
        cost+=.03*abs(ratio-1)
        return cost
    if offset is not None and (not math.isfinite(offset) or abs(offset)>300):raise ValueError('时间偏移范围为 ±300 秒')
    if scale is not None and (not math.isfinite(scale) or not .5<=scale<=2):raise ValueError('时间比例范围为 0.5–2')
    first_shift=starts[0]-times[0]
    shifts=np.array([offset]) if offset is not None else np.arange(first_shift-12,first_shift+12.001,.05)
    ratios=np.array([scale]) if scale is not None else np.arange(.95,1.05001,.001)
    options=[]
    for ratio in ratios:
        costs=evaluate(shifts,ratio);i=int(np.argmin(costs));options.append((float(costs[i]),float(shifts[i]),float(ratio)))
    best=min(options)
    for ratio in ([best[2]] if scale is not None else np.arange(max(.95,best[2]-.001),min(1.05,best[2]+.001)+.00001,.0001)):
        fine=np.array([offset]) if offset is not None else np.arange(best[1]-.06,best[1]+.061,.005)
        costs=evaluate(fine,ratio);i=int(np.argmin(costs));candidate=(float(costs[i]),float(fine[i]),float(ratio))
        if candidate<best:best=candidate
    _,shift,ratio=best;targets=times*ratio+shift
    residuals=[float(np.min(abs(starts-t))) for t in targets]
    ambiguous=any(c[0]<best[0]+.002 and (abs(c[1]-shift)>.6 or abs(c[2]-ratio)>.005) for c in options)
    return dict(offset_seconds=round(shift,6),scale=round(ratio,6),
        median_onset_error_seconds=float(np.median(residuals)),ambiguous=bool(ambiguous),
        method='manual' if offset is not None and scale is not None else 'MIDI onset and gap fit',
        limit_warning=bool(scale is None and (ratio<.9502 or ratio>1.0498)),
        interpretation='MIDI seconds = LRC seconds * scale + offset; no audio was used')


def combine_units(units):
    values=[u['text'] for u in units]
    return ' '.join(values) if any(u['lang']=='en' for u in units) else ''.join(values)


def allocate_units(notes,units):
    """Monotone many-to-many allocation; no discarded or reordered units."""
    n,m=len(notes),len(units)
    if not n or not m:return dict(labels=['SP']*n,unit_indices=[[] for _ in notes],unassigned=list(range(m)),warnings=['本句没有对应音符或读音'])
    if n*m>250000:return dict(labels=['']*n,unit_indices=[[] for _ in notes],unassigned=list(range(m)),warnings=['本句范围过长，请补充分句时间戳后重新准备'])
    weights=np.array([u.get('weight',1.) for u in units]);prefix=np.r_[0.,np.cumsum(weights)]
    durations=np.array([note['end']-note['start'] for note in notes]);expected=durations/durations.sum()*weights.sum()
    # Extend only genuinely contiguous notes. Detached notes may repeat a vowel
    # explicitly, without moving note endpoints or swallowing a source rest.
    best={0:0.};history=[]
    for i in range(n):
        next_states={};parents={}
        for used in range(m+1):
            state=best.get(used)
            if state is None:continue
            for take in range(0,min(8,m-used)+1):
                if take==0:
                    if not used or i==0:continue
                    prior=units[used-1]
                    if notes[i]['start']-notes[i-1]['end']<=.001:label='-';penalty=.7
                    elif prior.get('vowel'):
                        label=prior['vowel'];penalty=1.5
                    else:continue
                    ids=[]
                else:
                    selected=units[used:used+take];label=combine_units(selected);ids=list(range(used,used+take));penalty=.16*(take-1)**2
                    if len(label)>64:continue
                    if selected[0]['text'] in ('ン','ッ') and used:penalty+=.35
                amount=prefix[used+take]-prefix[used]
                cost=state+(amount-expected[i])**2/max(.8,expected[i])+penalty
                key=used+take
                if key not in next_states or cost<next_states[key]:
                    next_states[key]=cost;parents[key]=(used,label,ids)
        best=next_states;history.append(parents)
    if m not in best:
        return dict(labels=['']*n,unit_indices=[[] for _ in notes],unassigned=list(range(m)),warnings=['本句读音与音符差异过大，无法保留全部读音分配，请修正本句范围或读音'])
    path=[];cursor=m
    for parents in reversed(history):
        cursor,label,ids=parents[cursor];path.append((label,ids))
    path.reverse();labels=[x[0] for x in path];indices=[x[1] for x in path]
    assert [u for ids in indices for u in ids]==list(range(m))
    warnings=[]
    if any(not ids and label!='-' for label,ids in path):warnings.append('有分离音符使用元音续唱，请试听确认')
    if max(map(len,indices))>=3:warnings.append('一个音符承载三个及以上读音单元')
    if m/max(n,1)>1.8 or n/max(m,1)>1.8:warnings.append('读音与音符数量差异较大')
    return dict(labels=labels,unit_indices=indices,unassigned=[],warnings=warnings)


def phrase_ranges(notes,lines,readings,clock):
    """Joint monotone boundaries: nearby onsets, rests and reading density.

    Empty LRC events are separate nodes: notes in instrumental spans stay SP.
    Count compatibility is only a soft prior; notes and syllables are not 1:1.
    """
    starts=[n['start'] for n in notes];ratio=clock['scale'];shift=clock['offset_seconds']
    times=sorted({line['time'] for line in lines}|{line['end'] for line in lines if line['end'] is not None})
    positions={t:i for i,t in enumerate(times)}
    edges={positions[line['time']]:i for i,line in enumerate(lines)}
    targets=[t*ratio+shift for t in times]
    candidates=[]
    for target in targets:
        nearby=[i for i,x in enumerate(starts) if abs(x-target)<=.9]
        nearby.append(bisect_left(starts,target))
        candidates.append(sorted(set(nearby)))
    # The last lyric extends to the end of the melody unless LRC ends it explicitly.
    if lines[-1]['end'] is None:
        times.append(None);targets.append(notes[-1]['end']);candidates.append([len(notes)])
    def onset_cost(layer,index):
        target=targets[layer]
        if layer not in edges:
            # A blank timestamp ends singing; it need not coincide with an
            # onset. Any point in the rest after the last sung note is valid.
            before=notes[index-1]['end'] if index else 0.
            after=starts[index] if index<len(notes) else max(before,target)
            return max(before-target,target-after,0.)**2
        if index==len(notes):return min(3.,abs(notes[-1]['end']-target))**2
        gap=starts[index]-(notes[index-1]['end'] if index else 0)
        return (starts[index]-target)**2+.09*math.exp(-max(gap,0)/.12)
    states={i:(onset_cost(0,i),[i]) for i in candidates[0]}
    for layer in range(1,len(times)):
        next_states={}
        lyric=edges.get(layer-1)
        units=readings[lyric]['units'] if lyric is not None else None
        weight=sum(u.get('weight',1.) for u in units) if units is not None else 0
        for right in candidates[layer]:
            for left,(cost,path) in states.items():
                if right<left:continue
                count=right-left
                if units is not None:
                    if not count:penalty=8.+weight
                    else:
                        # Cap this prior: abbreviated refrains must not steal notes
                        # from neighbouring lines simply to match their unit count.
                        penalty=.3*min((count-weight)**2/max(4,weight),6.)
                else:penalty=0.
                value=cost+onset_cost(layer,right)+penalty
                if right not in next_states or value<next_states[right][0]:next_states[right]=(value,path+[right])
        if not next_states:raise ValueError('歌词时间戳无法保持顺序匹配 MIDI，请检查时间偏移或版本')
        states=next_states
    path=min(states.values(),key=lambda x:x[0])[1]
    return [(path[positions[line['time']]],path[positions[line['end']]] if line['end'] is not None else len(notes)) for line in lines]


def build_alignment(notes,parsed,readings,clock,ranges):
    lines=parsed['lines'];ratio=clock['scale'];shift=clock['offset_seconds']
    labels=['SP']*len(notes);owner=[None]*len(notes);phrases=[];unassigned=0
    for j,(line,reading,(left,right)) in enumerate(zip(lines,readings,ranges)):
        target=line['time']*ratio+shift;group=notes[left:right]
        mapping=allocate_units(group,reading['units']);warnings=list(mapping['warnings'])+reading.get('warnings',[])
        if len(group)>3*max(1,len(reading['units'])):warnings.append('歌词可能省略了重复或哼唱，请补全本句读音后重新分配')
        residual=abs(group[0]['start']-target) if group else float('inf')
        if residual>.5:warnings.append('LRC 起句与音符偏差较大，请核对分句范围')
        if clock['ambiguous'] or clock['limit_warning']:warnings.append('整体时间匹配不确定，请核对偏移、速度比例或歌曲版本')
        previous_end=ranges[j-1][1] if j else 0
        if left>previous_end and group and group[0]['start']-notes[left-1]['end']<.7:
            warnings.append('起句前还有未分配音符，已暂设 SP，请检查本句的首音符编号')
        for i,label in zip(range(left,right),mapping['labels']):
            if owner[i] is not None:raise ValueError('LRC 分句重叠，请检查时间戳')
            labels[i]=label;owner[i]=j
        unassigned+=len(mapping['unassigned'])
        phrases.append(dict(id=j,text=line['text'],lrc_start=line['time'],lrc_end=line['end'],
            midi_start=group[0]['start'] if group else None,midi_end=group[-1]['end'] if group else None,
            note_start=left,note_end=right,note_indices=list(range(left,right)),reading=reading['reading'],units=reading['units'],
            reading_warnings=reading.get('warnings',[]),
            unit_indices=mapping['unit_indices'],unassigned=mapping['unassigned'],
            warnings=list(dict.fromkeys(warnings)),needs_review=bool(warnings),onset_error_seconds=residual if math.isfinite(residual) else None))
    return dict(method='lrc_phrase_dp',clock=clock,phrases=phrases,labels=labels,note_phrase=owner,
        unassigned_units=unassigned,unowned_notes=[i for i,v in enumerate(owner) if v is None],
        review_phrases=sum(p['needs_review'] for p in phrases),total_units=sum(len(r['units']) for r in readings),
        exact_text_alignment_verified=False)


def align_lrc(notes,parsed,readings,offset=None,scale=None):
    lines=parsed['lines']
    if not notes:raise ValueError('没有可分配的音符')
    if len(lines)<2:raise ValueError('按句对齐至少需要两行带时间戳的歌词')
    if len(readings)!=len(lines):raise ValueError('LRC 和读音行数不一致')
    clock=fit_clock(notes,lines,offset,scale)
    # A constant LRC offset tag is subsumed by the fitted total offset. Never
    # apply it again; manual offset fields use the original stamped seconds.
    clock['lrc_offset_tag_ms']=parsed.get('offset_tag_ms',0)
    return build_alignment(notes,parsed,readings,clock,phrase_ranges(notes,lines,readings,clock))
