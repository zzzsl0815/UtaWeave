"""Coverage-preserving policies for inference, independent of training gates."""
from copy import deepcopy
from bisect import bisect_left
import math
import re


def lyric_windows(lines, duration_seconds, sample_rate, lead_seconds=.3):
    """Keep every nonempty timed lyric, including title matches and English.

    Empty lyric timestamps delimit instrumentals. Windows share boundaries;
    changing an inference context must not erase, duplicate or retime a lyric.
    """
    if not math.isfinite(duration_seconds) or duration_seconds<=0 or sample_rate<=0:
        raise ValueError('Invalid source audio clock')
    if not math.isfinite(lead_seconds) or lead_seconds<0:
        raise ValueError('Invalid alignment lead context')
    ordered=sorted(lines,key=lambda row:row['start_ms'])
    if len({row['start_ms'] for row in ordered})!=len(ordered):
        raise ValueError('Conflicting lyrics share a timestamp; preserve and resolve the input conflict')
    length=round(duration_seconds*sample_rate)
    result=[]
    for i,row in enumerate(ordered):
        if not row['text'].strip():continue
        lyric_start=row['start_ms']/1000
        if not 0<=lyric_start<duration_seconds:
            raise ValueError('Lyric timestamp outside source audio')
        end=ordered[i+1]['start_ms']/1000 if i+1<len(ordered) else duration_seconds+lead_seconds
        a=max(0,round((lyric_start-lead_seconds)*sample_rate))
        b=min(length,round((end-lead_seconds)*sample_rate))
        if b<=a:raise ValueError('Empty source window for a nonempty lyric')
        result.append(dict(line_index=len(result),source_line_index=i,text=row['text'],start_ms=row['start_ms'],
            start_sample=a,end_sample=b,sample_rate=sample_rate,start_seconds=a/sample_rate,end_seconds=b/sample_rate))
    if not result:raise ValueError('No nonempty timed lyrics')
    return result


def language_runs(text):
    """Route Latin words to English while preserving original Japanese spans."""
    expression=re.compile(r"[A-Za-z]+(?:['’\-][A-Za-z]+)*(?:\s+[A-Za-z]+(?:['’\-][A-Za-z]+)*)*")
    result=[];cursor=0
    for match in expression.finditer(text):
        if match.start()>cursor:result.append(dict(language='ja',text=text[cursor:match.start()]))
        result.append(dict(language='en',text=match[0]));cursor=match.end()
    if cursor<len(text):result.append(dict(language='ja',text=text[cursor:]))
    return [r for r in result if r['text'].strip()]


def intersect_notes_with_units(data, boundary_tolerance=1e-5):
    """Map measured note intervals to every original lyric unit.

    The native midpoint map can omit a short mora entirely. Intersections
    preserve its actual share of a note and keep unvoiced intervals as zero,
    rather than inventing pitches or silently losing a word at VR conversion.
    """
    if not math.isfinite(boundary_tolerance) or not 0<=boundary_tolerance<=1e-4:
        raise ValueError('Invalid numerical boundary tolerance')
    for name in ['word_dur','pitch_dur']:
        if not data[name] or any(not math.isfinite(v) or v<=0 for v in data[name]):
            raise ValueError('Invalid annotation durations')
    if len(data['word'])!=len(data['word_dur']) or len(data['pitch'])!=len(data['pitch_dur']):
        raise ValueError('Annotation array lengths differ')
    def edges(durations):
        result=[0.]
        for duration in durations:result.append(result[-1]+duration)
        return result
    word_edges,note_edges=edges(data['word_dur']),edges(data['pitch_dur'])
    if abs(word_edges[-1]-note_edges[-1])>.001:
        raise ValueError('Word and note timelines differ')
    # ROSVOT serializes float32 endpoints while TiFA serializes decimal word
    # times. Their shared boundaries can differ by fractions of a microsecond.
    # Snap only numerically coincident boundaries, before taking intersections:
    # a nonzero overlap must not turn such drift into an extra NOTE_32 token.
    # A real 10 ms mora or unvoiced interval is three orders of magnitude larger
    # than this tolerance and is retained, regardless of its musical duration.
    boundary_adjustments=[]
    for ni,t in enumerate(note_edges[1:-1],1):
        position=bisect_left(word_edges,t)
        candidates=word_edges[max(0,position-1):min(len(word_edges),position+1)]
        closest=min(candidates,key=lambda value:abs(value-t))
        if abs(closest-t)<=boundary_tolerance:
            note_edges[ni]=closest
            if closest!=t:
                boundary_adjustments.append(dict(note_boundary=ni,original=t,snapped=closest,delta=closest-t))
    # Only sub-millisecond serialization drift may affect the final endpoint.
    note_edges[-1]=word_edges[-1]
    if any(a>b for a,b in zip(note_edges,note_edges[1:])):
        raise ValueError('Numerical boundary correction would reverse a note interval')
    pitches=[];durations=[];owners=[];unvoiced=[]
    for wi,(word,a,b) in enumerate(zip(data['word'],word_edges[:-1],word_edges[1:])):
        indices=[]
        for ni,pitch in enumerate(data['pitch']):
            overlap=min(b,note_edges[ni+1])-max(a,note_edges[ni])
            if overlap<=1e-12:continue
            pitch=0 if word in ['SP','AP','<SP>','<AP>'] else int(pitch)
            if owners and owners[-1]==wi and pitches[-1]==pitch:
                durations[-1]+=overlap
            else:
                pitches.append(pitch);durations.append(overlap);owners.append(wi)
            if not indices or indices[-1]!=len(owners)-1:indices.append(len(owners)-1)
        if not indices:raise ValueError('A unit has no overlap with the measured note timeline')
        largest=max(indices,key=lambda j:durations[j])
        durations[largest]+=(b-a)-sum(durations[j] for j in indices)
        if word not in ['SP','AP','<SP>','<AP>'] and all(pitches[j]==0 for j in indices):
            unvoiced.append(dict(index=wi,word=word,start=a,end=b))
    result=deepcopy(data)
    result.pop('note',None)
    result.update(pitch=pitches,pitch_dur=durations,pitch2word=owners)
    return result,dict(method='intersect_native_note_and_original_unit_intervals',
        boundary_tolerance_seconds=boundary_tolerance,boundary_adjustments=boundary_adjustments,
        missing_unit_indices=[],unvoiced_lyric_units=unvoiced,pitch_imputation=False,
        original_missing_unit_indices=[i for i in range(len(data['word'])) if i not in data['pitch2word']])
