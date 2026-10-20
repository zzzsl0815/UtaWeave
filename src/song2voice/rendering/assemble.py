"""Assemble native EOS audio with bounded tempo correction and protected joins."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import numpy as np
import soundfile as sf

from .generate import read, write, sha, progress
from .audio import smooth_edges
from .runtime import ffmpeg_binary

SR=48000


def ffmpeg(args):
    subprocess.run([ffmpeg_binary(),'-hide_banner','-loglevel','error','-y','-threads','2',*map(str,args)],check=True)


def main(out):
    manifest=read(out/'manifest.json');alignments=read(out/'generated_alignment.json')
    assert manifest['status']=='generated' and len(alignments)==len(manifest['results'])
    progress(out,'assembling')
    folder=out/'processed';folder.mkdir(exist_ok=True)
    audio_dir=out/'audio';audio_dir.mkdir(exist_ok=True)
    jobs=[];prepared=[];natural=[]
    for chunk,result,align in zip(manifest['plan']['chunks'],manifest['results'],alignments):
        assert chunk['id']==result['id']==align['id']
        if result['hit_length_cap'] or not .8<align['tempo_ratio']<1.25:
            raise ValueError(f"Unresolved generation/tempo issue: {chunk['id']}")
        raw,sr=sf.read(out/result['path'],dtype='float32');assert sr==SR
        natural.extend([raw,np.zeros(round(.4*SR),dtype='float32')])
        signal,edge=smooth_edges(raw,align['first_lyric_start'],align['last_lyric_end'])
        path=folder/(chunk['id']+'_edges.wav');target=folder/(chunk['id']+'_aligned.wav')
        sf.write(path,signal,SR,subtype='FLOAT')
        jobs.append((path,target,align['tempo_ratio']))
        prepared.append(dict(id=chunk['id'],path=target,alignment=align,edge_audit=edge))
    def stretch(job):
        src,dst,ratio=job
        ffmpeg(['-i',src,'-af',f'rubberband=tempo={ratio:.10f}:pitch=1','-ar',SR,'-c:a','pcm_f32le',dst])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(stretch,jobs))
    inst_file=out/'instrumental48.wav';human_file=out/'human48.wav'
    ffmpeg(['-i',Path(manifest['stems'])/'instrumental.wav','-ar',SR,'-c:a','pcm_f32le',inst_file])
    ffmpeg(['-i',Path(manifest['stems'])/'vocal_native.wav','-ar',SR,'-ac','1','-c:a','pcm_f32le',human_file])
    inst,sr=sf.read(inst_file,dtype='float32',always_2d=True);assert sr==SR
    human,_=sf.read(human_file,dtype='float32');assert len(human)==len(inst)
    for clip in prepared:
        wave,sr=sf.read(clip['path'],dtype='float32');assert sr==SR and np.isfinite(wave).all()
        a=clip['alignment'];clip.update(wave=wave,weights=np.ones(len(wave),dtype='float32'),
            start=round(a['placement_seconds']*SR),
            first=a['placement_seconds']+a['first_lyric_start']/a['tempo_ratio'],
            last=a['placement_seconds']+a['last_lyric_end']/a['tempo_ratio'])
        assert clip['start']>=0 and clip['start']+len(wave)<=len(inst)
    joins=[]
    for left,right in zip(prepared,prepared[1:]):
        left_end=left['start']+len(left['wave']);right_start=right['start']
        if left_end<=right_start:
            joins.append(dict(left=left['id'],right=right['id'],overlap=False,
                generated_quiet_gap_seconds=(right_start-left_end)/SR,removed_energy_fraction=0.))
            continue
        lo=max(right_start,round((left['last']+.025)*SR))
        hi=min(left_end,round((right['first']-.025)*SR))
        if hi-lo<round(.012*SR):
            raise ValueError(f"No phone-safe join between {left['id']} and {right['id']}")
        candidates=np.arange(lo+240,hi-240,240,dtype=int)
        if not len(candidates): candidates=np.array([(lo+hi)//2])
        costs=[]
        for t in candidates:
            li=t-left['start'];ri=t-right['start']
            costs.append(float(np.mean(left['wave'][max(0,li-240):li+240]**2))+
                float(np.mean(right['wave'][max(0,ri-240):ri+240]**2)))
        cut=int(candidates[int(np.argmin(costs))]);fade=min(240,(hi-lo)//4)
        li=cut-left['start'];ri=cut-right['start']
        left['weights'][li+fade:]=0.;right['weights'][:ri-fade]=0.
        left['weights'][li-fade:li+fade]=np.cos(np.linspace(0,np.pi/2,2*fade))**2
        right['weights'][ri-fade:ri+fade]=np.sin(np.linspace(0,np.pi/2,2*fade))**2
        joins.append(dict(left=left['id'],right=right['id'],overlap=True,join_seconds=cut/SR,
            protected_lyric_gap=[left['last'],right['first']],fade_seconds=fade/SR))
    track=np.zeros(len(inst),dtype='float32');edge_audits=[]
    for clip in prepared:
        wave=clip['wave'];weights=clip['weights']
        a=max(0,round(clip['first']*SR)-clip['start']);b=min(len(wave),round(clip['last']*SR)-clip['start'])
        assert np.all(weights[a:b]==1.),(clip['id'],'join changes aligned lyric body')
        lost=float(np.sum(wave.astype(float)**2*(1-weights**2))/max(1e-12,np.sum(wave.astype(float)**2)))
        track[clip['start']:clip['start']+len(wave)]+=wave*weights
        edge_audits.append(dict(id=clip['id'],**clip['edge_audit'],join_removed_energy_fraction=lost,
            aligned_lyric_body_unchanged_by_joins=True))
    mask=np.zeros(len(track),dtype=bool)
    for c in manifest['plan']['chunks']:
        for w in c['words']:
            if any(p>0 for p in w['pitch']):mask[round(w['start']*SR):round(w['end']*SR)]=True
    dry_gain=min(1.,.98/max(float(np.max(abs(track))),1e-8));track*=dry_gain
    vocal_gain=float(np.sqrt(np.mean(human[mask]**2))/max(np.sqrt(np.mean(track[mask]**2)),1e-8))
    mix=inst+track[:,None]*vocal_gain
    mix_gain=min(1.,.98/max(float(np.max(abs(mix))),1e-8));mix*=mix_gain
    natural_audio=np.concatenate(natural);natural_audio*=min(1.,.98/max(float(np.max(abs(natural_audio))),1e-8))
    outputs={};encode_jobs=[]
    for key,wave in [('mix',mix),('vocal',track),('natural',natural_audio)]:
        assert np.isfinite(wave).all() and float(np.max(abs(wave)))<.999
        path=audio_dir/(key+'.wav');sf.write(path,wave,SR,subtype='PCM_24')
        encode_jobs.append((path,path.with_suffix('.mp3')))
        outputs[key]=dict(wav=str(path),mp3=str(path.with_suffix('.mp3')),seconds=len(wave)/SR,
            peak=float(np.max(abs(wave))),wav_sha256=sha(path))
    def encode(job):
        src,dst=job
        ffmpeg(['-i',src,'-map_metadata','-1','-c:a','libmp3lame','-b:a','320k','-write_xing','1',dst])
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(encode,encode_jobs))
    for output in outputs.values():output['mp3_sha256']=sha(output['mp3'])
    verification=dict(all_score_units_submitted_once=True,lyric_units=manifest['plan']['lyric_unit_count'],
        original_lyric_lines=manifest['plan']['original_phrase_count'],chunks=len(prepared),
        no_generation_length_caps=True,no_nonfinite_or_clipped_samples=True,
        source_clock_length_preserved=abs(outputs['mix']['seconds']-manifest['plan']['source_seconds'])<1/SR,
        lyric_body_unchanged_by_joins=True,
        tempo_ratio_range=[min(a['tempo_ratio'] for a in alignments),max(a['tempo_ratio'] for a in alignments)],
        unresolved_source_pitch_units=manifest['protocol']['known_unresolved_pitch_units'],
        alignment_flags=read(out/'alignment_verification.json')['warnings'],
        limitation='Forced alignment and complete input coverage do not certify all lyrics were sung correctly.')
    manifest.update(status='complete',completed_at=datetime.now(timezone.utc).isoformat(),outputs=outputs,
        mixing=dict(dry_gain=dry_gain,vocal_gain=vocal_gain,mix_gain=mix_gain),verification=verification)
    write(out/'join_audit.json',joins);write(out/'edge_audit.json',edge_audits)
    write(out/'verification.json',verification);write(out/'manifest.json',manifest)
    progress(out,'complete',seconds=outputs['mix']['seconds'],chunks=len(prepared),outputs=outputs)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);main(p.parse_args().out)
