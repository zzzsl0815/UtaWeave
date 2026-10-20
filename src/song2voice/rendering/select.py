"""Choose bounded retries by the same alignment checks, retaining all inputs."""
import argparse
import copy
import math
from pathlib import Path
import shutil
import sys

from .generate import read, write, sha


def penalty(a):
    # The score never changes. This chooses generated audio, not a reading.
    tiny_voiced=sum(a['units'][i]['has_measured_or_inferred_pitch'] for i in a['tiny_units'])
    return (10000*int(a['warning_length_cap'] or a['warning_tempo_outside_limit'])+
        20*len(a['compressed_units'])+10*tiny_voiced+
        5*int(a['warning_tempo_outside_preferred'])+5*abs(math.log(a['tempo_ratio']))+
        2*(1-a['agreement']))


def main(parent,retries,out):
    base=read(parent/'manifest.json');candidate_manifest=read(retries/'manifest.json')
    assert base['status'] in ['complete','generated'] and candidate_manifest['status']=='generated'
    alignments=read(parent/'generated_alignment.json');candidates=read(retries/'generated_alignment.json')
    assert base['score_sha256']==candidate_manifest['score_sha256']==sha(base['target']['score'])
    final=copy.deepcopy(base);final.update(status='generated',selection_parent=str(parent),retry_candidates=str(retries))
    for key in ['outputs','completed_at','mixing','verification']:final.pop(key,None)
    audits=[];selected_alignments=[];selected_results=[]
    for old_result,old_alignment in zip(base['results'],alignments):
        options=[(penalty(old_alignment),parent,old_result,old_alignment)]
        for result,align in zip(candidate_manifest['results'],candidates):
            if result['original_chunk_id']==old_result['id']:
                options.append((penalty(align),retries,result,align))
        chosen=min(options,key=lambda v:v[0])
        _,run,result,align=chosen
        result=copy.deepcopy(result);align=copy.deepcopy(align)
        original_id=old_result['id'];candidate_id=result['id']
        result.update(id=original_id,path=str((run/result['path']).resolve()),selected_candidate_id=candidate_id)
        align.update(id=original_id,selected_candidate_id=candidate_id)
        assert sha(result['path'])==result['sha256']
        selected_results.append(result);selected_alignments.append(align)
        audits.append(dict(id=original_id,chosen=candidate_id,
            candidates=[dict(id=r['id'],run=str(p),penalty=s,compressed=a['compressed_units'],
                tiny=a['tiny_units'],tempo_ratio=a['tempo_ratio'],agreement=a['agreement']) for s,p,r,a in options]))
    final['results']=selected_results
    out.mkdir(parents=True,exist_ok=True)
    write(out/'manifest.json',final);write(out/'chunk_plan.json',final['plan'])
    write(out/'generated_alignment.json',selected_alignments);write(out/'retry_selection.json',audits)
    write(out/'alignment_verification.json',dict(status='complete',all_phone_sequences_from_frozen_score=True,
        lyric_units=sum(len(r['units']) for r in selected_alignments),chunks=len(selected_alignments),
        english_phone_count=sum(r['english_phones'] for r in selected_alignments),
        warnings=[dict(chunk=r['id'],tempo_ratio=r['tempo_ratio'],tiny_units=r['tiny_units'],
            compressed_units=r['compressed_units'],length_cap=r['warning_length_cap']) for r in selected_alignments
            if r['warning_tempo_outside_preferred'] or r['tiny_units'] or r['compressed_units'] or r['warning_length_cap']]))
    shutil.copyfile(parent/'inputs.pt',out/'inputs.pt')
    for a in audits:
        if len(a['candidates'])>1:print(a,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--parent',type=Path,required=True)
    p.add_argument('--retries',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();main(a.parent,a.retries,a.out)
