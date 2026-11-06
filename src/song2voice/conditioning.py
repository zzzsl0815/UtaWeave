"""Numerical safety checks for VR conditions; these do not certify singing quality."""
from copy import deepcopy
import math

NOTE_UNITS={
    '<NOTE_32>':.125,'<NOTE_DOT_32>':.1875,'<NOTE_16>':.25,'<NOTE_DOT_16>':.375,
    '<NOTE_8>':.5,'<NOTE_DOT_8>':.75,'<NOTE_4>':1.,'<NOTE_DOT_4>':1.5,
    '<NOTE_2>':2.,'<NOTE_DOT_2>':3.,'<NOTE_1>':4.,'<NOTE_DOT_1>':6.,
}
REST_WORDS={'SP','AP','<SP>','<AP>'}
NUMERICAL_TOLERANCE=1e-5


def conditioning_bpm(bpm):
    """Use VR's integer 1..255 vocabulary without a silent 120 BPM fallback."""
    if not math.isfinite(bpm) or bpm<=0:
        raise ValueError('BPM must be finite and positive')
    while bpm>255:bpm/=2
    while bpm<1:bpm*=2
    return max(1,min(255,round(bpm)))


def compiled_duration(entry):
    bpm=entry['bpm']
    if bpm!=int(bpm) or not 1<=bpm<=255:
        raise ValueError('Compiled VR BPM must be an integer from 1 to 255')
    seconds=sum(NOTE_UNITS[n] for n in entry['note'])*60/bpm
    if seconds>=23.5:
        raise ValueError(f"{entry.get('item_name','chunk')}: VR 条件时长 {seconds:.2f} 秒超过短句上限，请检查节拍或切分")
    return seconds


def quantize_duration(seconds,bpm):
    if not math.isfinite(seconds) or seconds<=0 or not math.isfinite(bpm) or bpm<=0:
        raise ValueError('Duration and BPM must be finite and positive')
    units=seconds*bpm/60
    return min(NOTE_UNITS,key=lambda token:abs(math.log(units/NOTE_UNITS[token])))


def quantize_annotation(entry):
    result=deepcopy(entry)
    result['note']=[quantize_duration(d,result['bpm']) for d in result['pitch_dur']]
    return result


def check_conditioning(entry):
    """Block malformed numerical conditions, retain genuine short or uncertain units.

    Warnings describe uncertainty without removing any input. In particular,
    agreement from a forced aligner is deliberately not a pass criterion here.
    """
    errors=[];warnings=[];words=entry.get('word',[]);wd=entry.get('word_dur',[])
    pitches=entry.get('pitch',[]);pd=entry.get('pitch_dur',[])
    owners=entry.get('pitch2word',[]);notes=entry.get('note',[]);bpm=entry.get('bpm',0)
    if not words or len(words)!=len(wd):errors.append(dict(code='word_array_lengths'))
    if not pitches or not len(pitches)==len(pd)==len(owners)==len(notes):
        errors.append(dict(code='note_array_lengths'))
    if not isinstance(bpm,(int,float)) or not math.isfinite(bpm) or bpm<=0:
        errors.append(dict(code='invalid_bpm'))
    if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in wd+pd):
        errors.append(dict(code='invalid_durations'))
    if any(not isinstance(p,int) or isinstance(p,bool) or not 0<=p<=127 for p in pitches):
        errors.append(dict(code='invalid_pitch'))
    if any(not isinstance(i,int) or isinstance(i,bool) or not 0<=i<len(words) for i in owners):
        errors.append(dict(code='invalid_note_owner'))
    if any(token not in NOTE_UNITS for token in notes):errors.append(dict(code='unknown_note_token'))
    if errors:return dict(valid_for_render=False,errors=errors,warnings=warnings)
    mapped=set(owners);missing=[i for i in range(len(words)) if i not in mapped]
    if missing:errors.append(dict(code='missing_word_note_mapping',indices=missing))
    previous=-1
    for i in owners:
        if i<previous:
            errors.append(dict(code='non_monotonic_note_owners'));break
        previous=i
    lyric_actual=0.;lyric_conditioned=0.;ghosts=[];tiny_units=[];unknown=[]
    for i,(word,seconds) in enumerate(zip(words,wd)):
        ids=[j for j,owner in enumerate(owners) if owner==i]
        measured=sum(pd[j] for j in ids)
        if abs(measured-seconds)>NUMERICAL_TOLERANCE:
            errors.append(dict(code='word_note_duration_mismatch',word_index=i,word_seconds=seconds,note_seconds=measured))
        if seconds<NUMERICAL_TOLERANCE:
            errors.append(dict(code='sub_resolution_word',word_index=i,seconds=seconds))
        ghosts.extend(j for j in ids if pd[j]<NUMERICAL_TOLERANCE)
        if word in REST_WORDS:continue
        lyric_actual+=seconds
        lyric_conditioned+=sum(60/bpm*NOTE_UNITS[notes[j]] for j in ids)
        if seconds<.02:tiny_units.append(i)
        if ids and all(pitches[j]==0 for j in ids):unknown.append(i)
    if ghosts:errors.append(dict(code='sub_resolution_note_fragments',note_indices=ghosts))
    if abs(sum(wd)-sum(pd))>NUMERICAL_TOLERANCE:errors.append(dict(code='timeline_duration_mismatch'))
    if tiny_units:warnings.append(dict(code='short_source_lyric_units',word_indices=tiny_units))
    if unknown:warnings.append(dict(code='unknown_lyric_pitch',word_indices=unknown))
    ratio=lyric_conditioned/max(lyric_actual,1e-12)
    if lyric_actual and abs(lyric_conditioned-lyric_actual)>max(.5,.15*lyric_actual):
        warnings.append(dict(code='large_quantization_drift',actual_seconds=lyric_actual,conditioned_seconds=lyric_conditioned,ratio=ratio))
    return dict(valid_for_render=not errors,errors=errors,warnings=warnings,
        lyric_units=sum(w not in REST_WORDS for w in words),lyric_notes=sum(words[i] not in REST_WORDS for i in owners),
        source_lyric_seconds=lyric_actual,conditioned_lyric_seconds=lyric_conditioned,
        conditioned_to_source_ratio=ratio,quality_status='not_assessed')


def require_conditioning(entries):
    reports=[dict(item_name=e.get('item_name'),**check_conditioning(e)) for e in entries]
    if any(not r['valid_for_render'] for r in reports):
        failures=[dict(item_name=r['item_name'],errors=r['errors']) for r in reports if not r['valid_for_render']]
        raise ValueError('Malformed score conditioning: '+str(failures))
    return reports


def rest_tokens(seconds,bpm):
    tick=60/bpm/16
    target=max(2,round(seconds/tick))
    values={round(unit*16):token for token,unit in NOTE_UNITS.items()}
    best=[None]*(target+1);best[0]=[]
    for total in range(1,target+1):
        options=[best[total-size]+[token] for size,token in values.items()
            if size<=total and best[total-size] is not None]
        if options:best[total]=min(options,key=len)
    return best[target]


def compile_chunk_annotation(chunk,bpm):
    """Compile the score clock; optionally turn short automatic MIDI rests into sustains."""
    source_bpm=bpm;bpm=conditioning_bpm(bpm)
    entry=dict(item_name=chunk['id'],word=[],word_dur=[],pitch=[],pitch_dur=[],note=[],pitch2word=[],bpm=bpm,source_bpm=source_bpm,has_score=True)
    def rest(seconds):
        tokens=rest_tokens(seconds,bpm);owner=len(entry['word'])
        entry['word'].append('SP');entry['word_dur'].append(seconds)
        total=sum(NOTE_UNITS[t] for t in tokens)
        for token in tokens:
            entry['pitch'].append(0);entry['note'].append(token);entry['pitch2word'].append(owner)
            entry['pitch_dur'].append(seconds*NOTE_UNITS[token]/total)
    connected=[]
    def sustain(seconds):
        # Reassign the rest's exact token budget to the preceding sung pitch.
        # Coalesce equal-pitch tokens without changing total conditioned time.
        owner=len(entry['word'])-1
        pitch=entry['pitch'].pop();duration=entry['pitch_dur'].pop()+seconds
        units=NOTE_UNITS[entry['note'].pop()]+sum(NOTE_UNITS[t] for t in rest_tokens(seconds,bpm))
        entry['pitch2word'].pop()
        tokens=rest_tokens(units*60/bpm,bpm)
        if not math.isclose(sum(NOTE_UNITS[t] for t in tokens),units,abs_tol=1e-9):
            raise ValueError('Connected MIDI rest changed the conditioning clock')
        entry['word_dur'][owner]+=seconds
        for token in tokens:
            entry['pitch'].append(pitch);entry['note'].append(token);entry['pitch2word'].append(owner)
            entry['pitch_dur'].append(duration*NOTE_UNITS[token]/units)
    rest(.25);cursor=chunk['content_start'];previous=None
    for word in chunk['words']:
        gap=word['start']-cursor
        if gap>=.075:
            connect=(chunk.get('connect_short_rests') is True and gap<=.3 and previous is not None
                and previous.get('phrase_id') is not None and previous.get('phrase_id')==word.get('phrase_id')
                and previous.get('midi_note_indices') and word.get('midi_note_indices')
                and previous['midi_note_indices'][-1]+1==word['midi_note_indices'][0]
                and previous['pitch'][-1]>0)
            if connect:
                sustain(gap)
                connected.append(dict(after_note=previous['midi_note_indices'][-1],before_note=word['midi_note_indices'][0],seconds=gap))
            else:rest(gap)
        owner=len(entry['word']);entry['word'].append(word['word'])
        entry['word_dur'].append(word['end']-word['start'])
        entry['pitch'].extend(word['pitch'])
        entry['note'].extend(word['note'] if bpm==source_bpm else [quantize_duration(d,bpm) for d in word['pitch_dur']])
        entry['pitch_dur'].extend(word['pitch_dur']);entry['pitch2word'].extend([owner]*len(word['pitch']))
        cursor=word['end'];previous=word
    rest(.4)
    if chunk.get('connect_short_rests') is True:
        entry['connected_short_rests']=connected
    expected=[w['word'] for w in chunk['words']]
    if [w for w in entry['word'] if w not in REST_WORDS]!=expected:
        raise ValueError('Chunk compilation changed lyric unit identity or order')
    require_conditioning([entry])
    return entry
