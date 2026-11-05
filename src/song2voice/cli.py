import argparse
import json
from pathlib import Path
import sys

from .score import import_legacy, save, validate


def main():
    parser = argparse.ArgumentParser(description='UtaWeave: audio to score to singing; Japanese experimental baseline.')
    commands = parser.add_subparsers(dest='command', required=True)
    web = commands.add_parser('serve', help='Open the local MIDI/song-to-vocal workflow interface')
    web.add_argument('--config', type=Path, required=True)
    web.add_argument('--port', type=int, default=8770)
    web.add_argument('--bind', default='127.0.0.1')
    legacy = commands.add_parser('import-legacy', help='Import an existing full-song experiment without changing it')
    legacy.add_argument('--manifest', type=Path, required=True)
    legacy.add_argument('--timeline', choices=['source-seconds','source-samples'], required=True)
    legacy.add_argument('--out', type=Path, required=True)
    check = commands.add_parser('validate-score', help='Validate mora/note/phone ownership and the source clock')
    check.add_argument('score', type=Path)
    conditioning = commands.add_parser('validate-conditioning', help='Validate the native score.vr.json used by the renderer')
    conditioning.add_argument('score', type=Path)
    annotate = commands.add_parser('annotate', help='Audio-only Vocal2Midi labeling without reference lyrics or romaji')
    annotate.add_argument('--config', type=Path, required=True)
    annotate.add_argument('--python', default=sys.executable, help='Python executable with labeling dependencies')
    render = commands.add_parser('render', help='Render an annotated score with alignment, bounded retries and full-song assembly')
    render.add_argument('--config', type=Path, required=True)
    render.add_argument('--out', type=Path, required=True)
    render.add_argument('--python', default=sys.executable, help='Python with numpy and soundfile for the orchestrator')
    convert = commands.add_parser('convert', help='Convert existing VR guides with Seed-VC')
    convert.add_argument('--config', type=Path, required=True)
    convert.add_argument('--guide', type=Path, nargs='+', required=True)
    convert.add_argument('--reference-run', type=Path, required=True)
    convert.add_argument('--out', type=Path, required=True)
    report = commands.add_parser('report', help='Rebuild a full-song listening page without regenerating audio')
    report.add_argument('--manifest', type=Path, required=True)
    separate = commands.add_parser('separate', help='Prepare target-song vocal and accompaniment stems')
    separate.add_argument('--audio', type=Path, required=True)
    separate.add_argument('--runtime-config', type=Path, required=True)
    separate.add_argument('--out', type=Path, required=True)
    separate.add_argument('--no-dereverb', action='store_true')
    reference = commands.add_parser('prepare-reference', help='Provide a clip or automatically split, score and select from a song')
    inputs = reference.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--clip', type=Path, help='Already cut vocal clip; preserved unchanged')
    inputs.add_argument('--audio', type=Path, help='Full source audio for automatic separation and selection')
    inputs.add_argument('--from-run', type=Path, help='Reuse cached separation for automatic selection')
    reference.add_argument('--runtime-config', type=Path, help='Required with --audio')
    reference.add_argument('--policy', type=Path, help='Optional automatic quality/duration policy overrides')
    reference.add_argument('--out', type=Path, required=True)
    state = commands.add_parser('reference-status', help='Verify a frozen reference or its latest output pointer')
    state.add_argument('--run', type=Path, required=True)
    fetch = commands.add_parser('fetch-netease', help='Import a requested song through a configured local API bridge')
    fetch.add_argument('--url', required=True)
    fetch.add_argument('--bridge-script', type=Path, required=True)
    fetch.add_argument('--credential-file', type=Path)
    fetch.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'serve':
            from .web.server import serve
            serve(args.config, args.bind, args.port)
            return
        if args.command == 'report':
            from .rendering.report import build_report
            print(json.dumps(dict(report=str(build_report(args.manifest))), ensure_ascii=False))
            return
        if args.command == 'convert':
            from .voice_conversion.convert import convert
            run = convert(args.config, [dict(id=f'guide_{i:03d}', audio=str(path), title=path.stem)
                for i, path in enumerate(args.guide)], args.reference_run, args.out)
            print(json.dumps(dict(run=str(run), report=str(run/'index.html')), ensure_ascii=False))
            return
        if args.command == 'validate-conditioning':
            from .conditioning import require_conditioning
            print(json.dumps(require_conditioning(json.loads(args.score.read_text())),ensure_ascii=False,indent=2))
            return
        if args.command in ('render', 'separate'):
            import os
            import subprocess
            env = dict(os.environ)
            src = str(Path(__file__).resolve().parents[1])
            env['PYTHONPATH'] = src + os.pathsep + env.get('PYTHONPATH', '')
            if args.command == 'render':
                result = subprocess.run([args.python, '-u', '-m', 'song2voice.rendering.pipeline',
                    '--config', str(args.config.resolve()), '--out', str(args.out.resolve())], env=env)
            else:
                from .references import prepare_stems
                prepare_stems(args.audio,args.out,args.runtime_config,not args.no_dereverb)
                return
            raise SystemExit(result.returncode)
        if args.command == 'annotate':
            import os
            import subprocess
            from .vocal2midi_native_worker import validate_audio_only_config
            validate_audio_only_config(json.loads(args.config.read_text()))
            env = dict(os.environ)
            src = str(Path(__file__).resolve().parents[1])
            env['PYTHONPATH'] = src + os.pathsep + env.get('PYTHONPATH', '')
            result = subprocess.run([args.python, '-u', '-m', 'song2voice.vocal2midi_native_worker',
                                     '--config', str(args.config.resolve())], env=env)
            raise SystemExit(result.returncode)
        if args.command == 'prepare-reference':
            from .references import prepare_reference
            from .selection import require_reference
            run = prepare_reference(args.out, clip=args.clip, audio=args.audio, from_run=args.from_run,
                runtime_path=args.runtime_config, policy=json.loads(args.policy.read_text()) if args.policy else None)
            print(json.dumps(dict(run=str(run), selection=require_reference(run)), ensure_ascii=False, indent=2))
            return
        if args.command == 'reference-status':
            from .selection import reference_status
            result = reference_status(args.run)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result['state'] != 'ready':
                raise SystemExit(2)
            return
        if args.command == 'fetch-netease':
            from .netease import fetch_reference_song
            print(json.dumps(fetch_reference_song(args.url,args.bridge_script,args.credential_file,args.out),ensure_ascii=False,indent=2))
            return
        if args.command == 'import-legacy':
            if args.out.exists():
                parser.error('Output already exists; choose a new file to preserve the previous score.')
            score = import_legacy(args.manifest, args.timeline)
            save(args.out, score)
        else:
            score = json.loads(args.score.read_text())
        print(json.dumps(validate(score), ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, f'UtaWeave error: {error}\n')
