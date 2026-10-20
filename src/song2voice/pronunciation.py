"""Japanese-style readings for English lyrics, with explicit source-word ownership."""
from copy import deepcopy
import math
import re

from .annotation import intersect_notes_with_units
from .conditioning import quantize_annotation,require_conditioning,REST_WORDS

VOWELS=set('aiueo')
EN_TO_JA={
    'aa':['a'],'ae':['a'],'ah':['a'],'ax':['a'],'ao':['o'],'aw':['a','u'],
    'ay':['a','i'],'eh':['e'],'er':['a'],'ey':['e','i'],'ih':['i'],'iy':['i'],
    'ow':['o','u'],'oy':['o','i'],'uh':['u'],'uw':['u'],
    'b':['b'],'ch':['ch'],'d':['d'],'dh':['z'],'f':['f'],'g':['g'],'hh':['h'],
    'jh':['j'],'k':['k'],'l':['r'],'m':['m'],'n':['n'],'ng':['N'],'p':['p'],
    'r':['r'],'s':['s'],'sh':['sh'],'t':['t'],'th':['s'],'v':['v'],'w':['w'],
    'y':['y'],'z':['z'],'zh':['j'],
}


def english_phones_to_romaji(phones):
    """Offer a phonetic Japanese candidate from the existing English phone path.

    This is deliberately independent of an English spelling dictionary. A
    source-audio scorer can compare it with conventional loanword readings.
    """
    vowels=dict(aa='a',ae='a',ah='a',ax='a',ao='o',aw='au',ay='ai',eh='e',er='aa',
        ey='ei',ih='i',iy='ii',ow='ou',oy='oi',uh='u',uw='uu')
    onsets=dict(b='b',ch='ch',d='d',dh='z',f='f',g='g',hh='h',jh='j',k='k',l='r',m='m',
        n='n',ng='n',p='p',r='r',s='s',sh='sh',t='t',th='s',v='v',w='w',y='y',z='z',zh='j')
    codas=dict(b='bu',ch='chi',d='do',dh='zu',f='fu',g='gu',hh='fu',jh='ji',k='ku',l='ru',m='n',
        n='n',ng='n',p='pu',r='ru',s='su',sh='shi',t='to',th='su',v='vu',w='u',y='i',z='zu',zh='ji')
    names=[re.sub(r'\d+$','',p.split('/')[-1].lower()) for p in phones]
    output=[]
    for i,name in enumerate(names):
        if name in vowels:output.append(vowels[name])
        elif name not in onsets:raise ValueError('Unsupported English phonetic reading: '+name)
        elif i+1<len(names) and names[i+1] in vowels:output.append(onsets[name])
        else:output.append(codas[name])
    return ''.join(output)


def phone_distance(source,target):
    if source==target:return 0.
    if source in VOWELS and target in VOWELS:return .8
    if target=='cl' and source in {'p','t','k','b','d','g'}:return .25
    if source in {'n','N','m'} and target in {'n','N','m'}:return .4
    if source not in VOWELS and target not in VOWELS:return 1.2
    return 3.


def project_phone_durations(source_phones,source_durations,target_phones):
    """Monotonic DTW allocation; times are projected, never claimed as measured.

    One source phone may cover repeated Japanese vowels or an inserted vowel;
    its measured duration is divided among those phones. All source time is
    accounted for, and no target phone has a zero-width interval.
    """
    if not source_phones or len(source_phones)!=len(source_durations) or not target_phones:
        raise ValueError('Invalid phone projection arrays')
    if any(not math.isfinite(d) or d<=0 for d in source_durations):raise ValueError('Invalid source phone duration')
    expanded=[];durations=[]
    for phone,duration in zip(source_phones,source_durations):
        name=re.sub(r'\d+$','',phone.split('/')[-1].lower())
        if name not in EN_TO_JA:raise ValueError('Unsupported English phone: '+phone)
        values=EN_TO_JA[name];expanded.extend(values);durations.extend([duration/len(values)]*len(values))
    n,m=len(expanded),len(target_phones);cost={(0,0):0.};previous={}
    for i in range(1,n+1):
        for j in range(1,m+1):
            options=[]
            for a,b,penalty in [(i-1,j-1,0.),(i-1,j,.15),(i,j-1,.15)]:
                if (a,b) in cost:options.append((cost[a,b]+penalty,(a,b)))
            if not options:continue
            value,origin=min(options,key=lambda item:item[0])
            cost[i,j]=value+phone_distance(expanded[i-1],target_phones[j-1].split('/')[-1])
            previous[i,j]=origin
    pairs=[];position=(n,m)
    while position!=(0,0):
        i,j=position;pairs.append((i-1,j-1));position=previous[position]
    by_source={i:[] for i in range(n)}
    for i,j in pairs:
        if j not in by_source[i]:by_source[i].append(j)
    output=[0.]*m
    for i,targets in by_source.items():
        for j in targets:output[j]+=durations[i]/len(targets)
    if any(v<=0 for v in output) or abs(sum(output)-sum(source_durations))>1e-8:
        raise ValueError('Phone projection lost time or a pronunciation unit')
    return output,dict(method='duration_conserving_phone_DTW_projection',cost=cost[n,m],
        source_phones=source_phones,target_phones=target_phones,measured_target_boundaries=False)


def adapt_english_annotation(entry,reading_provider):
    """Derive a model score while keeping the original annotation immutable.

    reading_provider(word, phones) returns Japanese mora text/phones, their
    owner mapping, and a romaji display. Dictionary/runtime dependencies stay
    outside the portable score code.
    """
    require_conditioning([entry])
    result=deepcopy(entry)
    words=[];word_durations=[];phones=[];phone_durations=[];phone_owners=[];source_map=[];audits=[]
    for wi,(word,seconds) in enumerate(zip(entry['word'],entry['word_dur'])):
        ids=[j for j,owner in enumerate(entry['ph2word']) if owner==wi]
        original_phones=[entry['ph'][j] for j in ids];original_durations=[entry['ph_dur'][j] for j in ids]
        if not ids or abs(sum(original_durations)-seconds)>1e-5:
            raise ValueError('Phone and word times differ before pronunciation adaptation')
        convert=bool(re.search('[A-Za-z]',word)) and word not in REST_WORDS
        if not convert:
            owner=len(words);words.append(word);word_durations.append(seconds)
            phones.extend(original_phones);phone_durations.extend(original_durations);phone_owners.extend([owner]*len(ids))
            source_map.append(dict(source_word_index=wi,source_word=word,adapted=False))
            continue
        if any(not p.startswith('en/') for p in original_phones):
            raise ValueError('Latin lyric unit lacks an English pronunciation path: '+word)
        context=dict(source_word_index=wi,start_seconds=sum(entry['word_dur'][:wi]),
            end_seconds=sum(entry['word_dur'][:wi+1]))
        reading=reading_provider(word,original_phones,context)
        target_words=reading['word'];target_phones=reading['ph'];owners=reading['ph2word']
        if not target_words or len(target_phones)!=len(owners) or set(owners)!=set(range(len(target_words))):
            raise ValueError('Japanese pronunciation has missing mora mappings')
        if any(re.search('[A-Za-z]',w) for w in target_words) or any(p.startswith('en/') for p in target_phones):
            raise ValueError('Japanese pronunciation still contains English model units')
        projected,projection=project_phone_durations(original_phones,original_durations,target_phones)
        # Scale only the tiny accumulated decimal serialization difference.
        projected=[v*seconds/sum(projected) for v in projected]
        start=len(words)
        for i,text in enumerate(target_words):
            positions=[j for j,owner in enumerate(owners) if owner==i]
            words.append(text);word_durations.append(sum(projected[j] for j in positions))
            source_map.append(dict(source_word_index=wi,source_word=word,adapted=True,
                japanese_mora_index=i,romaji=reading['romaji_units'][i]))
        phones.extend(target_phones);phone_durations.extend(projected);phone_owners.extend(start+i for i in owners)
        audits.append(dict(source_word_index=wi,source_word=word,source_seconds=seconds,
            kana=reading['reading'],romaji=reading['romaji'],romaji_units=reading['romaji_units'],
            model_units=target_words,model_durations=word_durations[start:],reading_source=reading['source'],
            projection=projection))
        if 'selection' in reading:audits[-1]['selection']=reading['selection']
    if not audits:return result,[]
    result.update(word=words,word_dur=word_durations,ph=phones,ph_dur=phone_durations,ph2word=phone_owners)
    result,mapping=intersect_notes_with_units(result)
    result=quantize_annotation(result)
    result.update(source_word_map=source_map,source_word_count=len(entry['word']),
        source_lyric_unit_count=sum(w not in REST_WORDS for w in entry['word']),
        source_languages=entry.get('languages',[]),languages=['ja'],
        pronunciation_adaptation=dict(policy='english-as-japanese-romaji-v1',words=audits,
            note_mapping=mapping,word_time_budget_preserved=True,source_annotation_unchanged=True))
    if abs(sum(result['word_dur'])-sum(entry['word_dur']))>1e-8:raise ValueError('Pronunciation adaptation changed phrase duration')
    if set(v['source_word_index'] for v in source_map)!=set(range(len(entry['word']))):
        raise ValueError('Pronunciation adaptation omitted a source word')
    require_conditioning([result])
    return result,audits
