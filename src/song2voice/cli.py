import argparse
import json
from pathlib import Path
import sys
from contextlib import redirect_stdout, nullcontext

from .score import import_legacy, save, validate


def main():
    parser = argparse.ArgumentParser(description='UtaWeave: audio to score to singing; Japanese experimental baseline.')
    commands = parser.add_subparsers(dest='command', required=True)
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
    separate = commands.add_parser('separate', help='Prepare target-song vocal and accompaniment stems')
    separate.add_argument('--audio', type=Path, required=True)
    separate.add_argument('--runtime-config', type=Path, required=True)
    separate.add_argument('--out', type=Path, required=True)
    separate.add_argument('--no-dereverb', action='store_true')
    reference = commands.add_parser('prepare-reference', help='Separate, cut and score a song for human reference selection')
    reference.add_argument('--audio', type=Path, required=True)
    reference.add_argument('--runtime-config', type=Path, required=True)
    reference.add_argument('--metadata', type=Path)
    reference.add_argument('--out', type=Path, required=True)
    reference.add_argument('--top-count', type=int, default=8)
    reference.add_argument('--no-dereverb', action='store_true')
    reference.add_argument('--candidate', help='Explicit candidate number/id; bypass interactive input')
    reference.add_argument('--no-prompt', action='store_true', help='Print candidates and leave an unselected task waiting')
    reference.add_argument('--json', action='store_true', help='Machine-readable output, without interactive input')
    choose = commands.add_parser('select-reference', help='Persist a reference explicitly chosen by the user')
    choose.add_argument('--run', type=Path, required=True)
    choose.add_argument('--candidate', required=True)
    choose.add_argument('--manifest-sha256', help='Reject a selection from a different shortlist version')
    listing = commands.add_parser('list-references', help='Print candidate source times, duration and scores')
    listing.add_argument('--run', type=Path, required=True)
    review = commands.add_parser('review-reference', help='Choose a reference in an interactive terminal')
    review.add_argument('--run', type=Path, required=True)
    state = commands.add_parser('reference-status', help='Report the persistent reference-selection gate')
    state.add_argument('--run', type=Path, required=True)
    waiting = commands.add_parser('wait-reference', help='Wait for explicit selection in another terminal')
    waiting.add_argument('--run', type=Path, required=True)
    waiting.add_argument('--timeout',type=float)
    export = commands.add_parser('export-references', help='Export only shortlisted WAVs for local listening')
    export.add_argument('--run',type=Path,required=True)
    export.add_argument('--out',type=Path,required=True)
    fetch = commands.add_parser('fetch-netease', help='Import a requested song through a configured local API bridge')
    fetch.add_argument('--url', required=True)
    fetch.add_argument('--bridge-script', type=Path, required=True)
    fetch.add_argument('--credential-file', type=Path)
    fetch.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
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
            from .terminal_review import list_candidates, terminal_review, candidate_by_number
            from .selection import select_reference, reference_status
            with redirect_stdout(sys.stderr) if args.json else nullcontext():
                result = prepare_reference(args.audio,args.out,args.runtime_config,args.metadata,args.top_count,not args.no_dereverb)
            if not args.json:
                list_candidates(args.out)
            if args.candidate:
                row = candidate_by_number(args.out,args.candidate)
                select_reference(args.out,row['id'],method='cli')
            elif result['reference_state']!='ready' and not args.no_prompt and not args.json and sys.stdin.isatty():
                terminal_review(args.out,show_table=False)
            status = reference_status(args.out)
            if args.json:
                print(json.dumps(dict(**status,candidates=result['candidates']),ensure_ascii=False,indent=2))
            elif status['state']=='ready':
                print('已确认音色参考: '+status['selection']['candidate_id'])
                print('选择记录: '+str(args.out.resolve()/'reference.selected.json'))
            else:
                print('等待选择。稍后运行 select-reference --run RUN --candidate 编号，即可保存并续跑。')
            if status['state']!='ready':
                raise SystemExit(3 if status['state']=='awaiting_reference_selection' else 2)
            return
        if args.command == 'select-reference':
            from .selection import select_reference
            from .terminal_review import candidate_by_number
            row = candidate_by_number(args.run,args.candidate)
            print(json.dumps(select_reference(args.run,row['id'],expected_manifest_sha256=args.manifest_sha256),ensure_ascii=False,indent=2))
            return
        if args.command=='list-references':
            from .terminal_review import list_candidates
            list_candidates(args.run)
            return
        if args.command=='review-reference':
            from .terminal_review import terminal_review
            result = terminal_review(args.run)
            if result is None:
                raise SystemExit(3)
            print('已保存音色参考: '+result['candidate_id'])
            return
        if args.command in ['reference-status','wait-reference']:
            from .selection import reference_status, wait_reference
            result = wait_reference(args.run,args.timeout) if args.command=='wait-reference' else reference_status(args.run)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            if args.command=='reference-status' and result['state']!='ready':
                raise SystemExit(3 if result['state']=='awaiting_reference_selection' else 2)
            return
        if args.command=='export-references':
            from .terminal_review import export_candidates
            print(json.dumps(export_candidates(args.run,args.out),ensure_ascii=False,indent=2))
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
