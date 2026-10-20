"""Audio boundary helpers preserved from the working baseline."""
import numpy as np
SR=48000

def intervals(mask, step=.01, minimum=.25):
    edges=np.diff(np.r_[False,mask,False].astype(np.int8))
    return [(a*step,b*step) for a,b in zip(np.flatnonzero(edges==1),np.flatnonzero(edges==-1)) if (b-a)*step>=minimum]

def activity(audio,sr=SR):
    hop=round(.01*sr);count=len(audio)//hop
    rms=np.sqrt(np.mean(audio[:count*hop].reshape(count,hop)**2,axis=1))
    threshold=max(.0015,float(np.quantile(rms,.9))*.025)
    active=rms>threshold
    # Isolated codec/model clicks should not define an entire vocal span.
    stable=np.convolve(active.astype(float),np.ones(4),mode='same')>=3
    indices=np.flatnonzero(stable)
    if not len(indices):raise ValueError('No active singing in generated chunk')
    start=float(indices[0]*.01);end=float(min(len(audio)/sr,(indices[-1]+1)*.01))
    return dict(active_start=start,active_end=end,active_seconds=end-start,
        trailing_quiet_seconds=len(audio)/sr-end,threshold=threshold,
        tail_100ms_rms=float(np.sqrt(np.mean(audio[-round(.1*sr):]**2))),
        seconds=len(audio)/sr,peak=float(np.max(np.abs(audio))))

def runs(mask):
    edges = np.diff(np.r_[False, mask, False].astype(np.int8))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))

def smooth_edges(audio, first, last):
    """Keep syllables intact; taper only the non-lyric edges of native output.

    Quiet breaths can be much weaker than vowels. The threshold here follows
    the codec floor, rather than a percentage of loud singing. An isolated
    late noise after a clear post-lyric pause belongs to the following phrase;
    remove that entire candidate, never half of it. Continuous vowel decay
    remains attached to the preceding phrase.
    """
    hop = 240
    rms = np.sqrt(np.mean(audio[:len(audio)//hop*hop].reshape(-1, hop)**2, axis=1))
    floor = float(np.quantile(rms, .05))
    threshold = float(np.clip(floor * 5, .00025, .0008))
    cut = len(audio)
    detached = None
    for a, b in runs(rms < threshold):
        start = max(a * .005, last + .10)
        end = b * .005
        if end - start < .06:
            continue
        # Require a separate audible event after this pause, not just a few
        # fluctuating codec-floor samples in the last frame.
        if any(v-u >= 6 for u, v in runs(rms[b:] >= threshold)):
            cut = round((start + min(.035, (end-start)/2)) * SR)
            detached = dict(quiet=[a*.005, b*.005], cut_seconds=cut/SR,
                            policy='remove whole isolated post-lyric noise candidate')
            break
    signal = audio[:cut].copy()
    weights = np.ones(len(signal), dtype=np.float32)
    # A fragment already active at sample zero needs a natural, short attack.
    # All attack shaping ends at least 60 ms before the first aligned phone.
    head_rms = float(np.sqrt(np.mean(signal[:960]**2)))
    attack = min(.08 if head_rms > threshold else .003, max(0., first-.06))
    n_attack = round(attack * SR)
    if n_attack:
        weights[:n_attack] *= np.sin(np.linspace(0, np.pi/2, n_attack))**2
    tail_rms = float(np.sqrt(np.mean(signal[-960:]**2)))
    available = len(signal)/SR - last - .08
    release = min(.08 if tail_rms > threshold else .005, max(0., available))
    # TiFA can assign the final frame to the last mora even when the native
    # waveform is already quiet. Only a 3 ms anti-click ramp is allowed there.
    quiet_endpoint_override = False
    if release < .003 and tail_rms < threshold:
        release = .003
        quiet_endpoint_override = True
    n_release = round(release * SR)
    if n_release:
        weights[-n_release:] *= np.cos(np.linspace(0, np.pi/2, n_release))**2
    result = signal * weights
    # Audible syllables must remain bit-identical before time alignment.
    body_a = round(first * SR)
    body_b = min(len(result), round(last * SR))
    if quiet_endpoint_override:
        body_b = min(body_b, len(result)-n_release)
    assert body_b >= body_a
    assert np.array_equal(result[body_a:body_b], audio[body_a:body_b])
    return result, dict(threshold=threshold, native_seconds=len(audio)/SR,
        selected_seconds=len(result)/SR, attack_seconds=attack, release_seconds=release,
        head_20ms_rms=head_rms, tail_20ms_rms=tail_rms,
        detached_late_noise=detached, quiet_endpoint_override=quiet_endpoint_override,
        lyric_body_unchanged=True,
        removed_energy_fraction=float((np.sum(audio.astype(float)**2)-np.sum(result.astype(float)**2)) /
                                      max(1e-12, np.sum(audio.astype(float)**2))))
