"""Conservative pitch recovery with measured and inferred results distinguished."""
from copy import deepcopy
import math
from statistics import median

SPECIAL={'SP','AP','<SP>','<AP>'}


def _points(measurement,key,start,end,minimum_probability=0.):
    result=[]
    for point in measurement[key]:
        hz=point['hz'];t=point['time']
        if start-1e-6<=t<=end+1e-6 and 55<=hz<=1100 and point.get('voiced_probability',1.)>=minimum_probability:
            result.append(dict(time=t,midi=69+12*math.log2(hz/440),probability=point.get('voiced_probability')))
    return result


def _stats(points):
    values=[p['midi'] for p in points]
    value=median(values) if values else None
    return dict(frames=len(values),median_midi=value,
        median_absolute_deviation=median(abs(v-value) for v in values) if values else None)


def _paired(primary,secondary):
    result=[]
    for point in primary:
        near=[p for p in secondary if abs(p['time']-point['time'])<=.005001 and abs(p['midi']-point['midi'])<=.75]
        if near:
            match=min(near,key=lambda p:abs(p['time']-point['time']))
            result.append(dict(time=point['time'],midi=(point['midi']+match['midi'])/2))
    return result


def _crosses_rest(entry,a,b):
    a,b=sorted((a,b));cursor=0.
    for word,duration in zip(entry['word'],entry['word_dur']):
        end=cursor+duration
        if word in SPECIAL and min(b,end)-max(a,cursor)>1e-5:return True
        cursor=end
    return False


def _anchor(entry,measurement,direction):
    boundary=measurement['start'] if direction=='left' else measurement['end']
    a,b=(boundary-.12,boundary-1e-5) if direction=='left' else (boundary+1e-5,boundary+.12)
    pairs=_paired(_points(measurement,'primary',a,b),_points(measurement,'secondary',a,b,.3))
    pairs=sorted(pairs,key=lambda p:abs(p['time']-boundary))[:5]
    if len(pairs)<3:return None
    stats=_stats(pairs);nearest=min(pairs,key=lambda p:abs(p['time']-boundary))
    if stats['median_absolute_deviation']>.6 or abs(nearest['time']-boundary)>.08:return None
    if any(_crosses_rest(entry,boundary,p['time']) for p in pairs):return None
    return dict(direction=direction,time=nearest['time'],midi=stats['median_midi'],frames=len(pairs))


def _integer_pitch(value,anchor=None):
    # A value near the half-semitone boundary is ambiguous. A consistent
    # immediately adjacent anchor breaks the tie without choosing an octave.
    if anchor is not None:
        preferred=int(math.floor(anchor['midi']+.5))
        if abs((value-math.floor(value))-.5)<.1 and abs(preferred-value)<=.65:return preferred
    return int(math.floor(value+.5))


def propose_pitch(entry,measurement):
    wi=measurement['unit_index'];a,b=measurement['start'],measurement['end'];duration=b-a
    expected_start=sum(entry['word_dur'][:wi]);expected_end=expected_start+entry['word_dur'][wi]
    if abs(a-expected_start)>1e-5 or abs(b-expected_end)>1e-5:
        raise ValueError('Pitch measurement times differ from the original lyric unit')
    if entry['word'][wi] in SPECIAL:raise ValueError('Silence and breath are never pitch-repair targets')
    indices=[i for i,owner in enumerate(entry['pitch2word']) if owner==wi]
    if not indices or any(entry['pitch'][i] for i in indices):raise ValueError('Only fully unvoiced, mapped lyric units may be repaired')
    primary=_points(measurement,'primary',a,b)
    secondary=_points(measurement,'secondary',a,b,.3)
    strong_secondary=_points(measurement,'secondary',a,b,.7)
    paired=_paired(primary,secondary)
    ps,ss,cs=_stats(primary),_stats(strong_secondary),_stats(paired)
    result=dict(line_index=measurement['line_index'],unit_index=wi,word=entry['word'][wi],start=a,end=b,
        original_pitch=0,status='unresolved',pitch=None,kind=None,confidence='unresolved',
        primary_support=ps,secondary_strong_support=ss,paired_support=cs,
        confidence_is_calibrated_probability=False)
    def accepted(value,kind,confidence,method,anchor=None):
        result.update(status='recovered',pitch=_integer_pitch(value,anchor),kind=kind,confidence=confidence,method=method)
        if anchor is not None:result['anchor']=anchor
        return result
    rms=measurement['unit_rms'];reference=measurement['phrase_reference_rms']
    if rms<max(.0001,reference*.01):
        result['reason']='aligned_unit_is_near_silence; possible_alignment_error'
        return result
    if cs['frames']>=2 and cs['median_absolute_deviation']<=.6:
        return accepted(cs['median_midi'],'measured','high' if cs['frames']>=4 else 'limited','two_F0_tracks_agree_inside_unit')
    if cs['frames']==1 and duration<=.08:
        return accepted(cs['median_midi'],'measured','limited','single_frame_agreement_inside_short_unit')
    conflict=(ps['frames']>=2 and ss['frames']>=2 and abs(ps['median_midi']-ss['median_midi'])>1.)
    if ps['frames']>=3 and ps['median_absolute_deviation']<=.6 and not conflict:
        return accepted(ps['median_midi'],'measured','medium','stable_RMVPE_frames_inside_unit')
    if ss['frames']>=3 and ss['median_absolute_deviation']<=.6 and not conflict:
        return accepted(ss['median_midi'],'measured','medium','stable_high_probability_pYIN_frames_inside_unit')
    left,right=_anchor(entry,measurement,'left'),_anchor(entry,measurement,'right')
    if ps['frames'] and duration<=.03:
        close=[v for v in [left,right] if v and abs(v['midi']-ps['median_midi'])<=.75]
        if close:
            anchor=min(close,key=lambda v:abs(v['midi']-ps['median_midi']))
            return accepted(ps['median_midi'],'measured','limited','short_unit_F0_confirmed_by_adjacent_voicing',anchor)
    if conflict:
        result['reason']='F0_estimators_disagree; leave_pitch_unresolved'
        return result
    if duration<=.12 and left and right and abs(left['midi']-right['midi'])<=.75:
        result['other_anchor']=right
        return accepted((left['midi']+right['midi'])/2,'inferred','low','bridge_between_agreeing_adjacent_voicing',left)
    phones=[p.split('/')[-1] for p,owner in zip(entry['ph'],entry['ph2word']) if owner==wi and p!='SP']
    consonant_onset=bool(phones and phones[0] not in ['a','i','u','e','o','aa','ae','ah','ao','aw','ax','ay','eh','er','ey','ih','iy','ow','oy','uh','uw'])
    if consonant_onset and duration<=.2 and right:
        return accepted(right['midi'],'inferred','low','short_consonant_or_nasal_inherits_immediate_following_pitch',right)
    if not consonant_onset and duration<=.08 and left:
        return accepted(left['midi'],'inferred','low','short_vowel_tail_inherits_immediate_preceding_pitch',left)
    result['reason']='insufficient_local_evidence_or_alignment_gap; no_fill_across_rest'
    return result


def repair_annotations(entries,measurements):
    output=deepcopy(entries);by_line={row['line_index']:row for row in output};audit=[]
    seen=set()
    for measurement in measurements:
        key=(measurement['line_index'],measurement['unit_index'])
        if key in seen:raise ValueError('Duplicate pitch-repair target')
        seen.add(key);entry=by_line[key[0]]
        if entry['word'][key[1]]!=measurement['word']:raise ValueError('Pitch measurements refer to different lyric units')
        decision=propose_pitch(entry,measurement);audit.append(decision)
        if decision['status']=='recovered':
            for index,owner in enumerate(entry['pitch2word']):
                if owner==key[1]:entry['pitch'][index]=decision['pitch']
        entry.setdefault('pitch_repair_provenance',[]).append(decision)
    return output,audit
