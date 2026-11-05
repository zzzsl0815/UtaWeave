"""Apply Seed-VC to selected VR phrases, then expose a manifest for realignment."""
import argparse
from copy import deepcopy
from pathlib import Path

from .generate import read, write, sha
from .runtime import runtime
from .audio import activity
from ..selection import require_reference
from ..voice_conversion.convert import convert, digest


def main(parent, out):
    import librosa
    import soundfile as sf
    manifest = read(parent/'manifest.json')
    config = runtime()
    sources = []
    for result in manifest['results']:
        source = parent/result['path']
        if sha(source) != result['sha256']:
            raise ValueError('VR guide changed: '+result['id'])
        sources.append(dict(id=result['id'], audio=str(source.resolve())))
    converted = convert(config['seed_vc_config'], sources, config['reference_run'], out/'conversion')
    recipe = dict(parent_manifest_sha256=sha(parent/'manifest.json'), conversion=str(converted),
                  implementation=sha(Path(__file__)))
    marker = out/'manifest.json'
    if marker.exists():
        old = read(marker)
        if old.get('conversion_recipe') != recipe:
            raise ValueError('Converted manifest inputs changed; use a new run directory')
        for row in old['results']:
            if sha(out/row['path']) != row['sha256']:
                raise ValueError('Converted phrase changed: '+row['id'])
        return
    out.mkdir(parents=True, exist_ok=True)
    folder = out/'chunks'; folder.mkdir(exist_ok=True)
    result = deepcopy(manifest)
    result.update(status='generated', guide_reference=manifest['reference'],
                  reference=require_reference(config['reference_run']), conversion_recipe=recipe)
    rows = []
    for guide, native in zip(manifest['results'], read(converted/'status.json')['results']):
        if guide['id'] != native['id']:
            raise ValueError('Converted phrase order changed')
        wave, sr = sf.read(native['audio'], dtype='float32')
        # Assembly is 48 kHz. Keep the native 44.1 kHz file in conversion/.
        wave = librosa.resample(wave, orig_sr=sr, target_sr=48000)
        path = folder/(guide['id']+'.wav')
        sf.write(path, wave, 48000, subtype='FLOAT')
        rows.append(dict(guide, path=str(path.relative_to(out)), sha256=sha(path),
                         guide_audio=str((parent/guide['path']).resolve()), guide_sha256=guide['sha256'],
                         guide_activity=guide.get('activity'), activity=activity(wave),
                         conversion=native, recipe_sha256=digest(recipe)))
    result['results'] = rows
    result['protocol'].update(voice_renderer='Seed-VC', realign_after_conversion=True)
    write(marker, result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    main(args.parent, args.out)
