"""Runner lento dedicado: não participa da descoberta unittest."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_render_checks import create_fixture, run_case, tool_identity


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--portable-root', type=Path)
    parser.add_argument('--six-only', action='store_true')
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'success': False, 'cases': [], 'errors': [], 'platform': sys.platform}
    started = time.monotonic()
    (args.output / 'matrix.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    try:
        if sys.platform == 'win32' and args.portable_root is None:
            raise ValueError('Windows exige --portable-root para provar identidade do binário fixado.')
        report['identity'] = tool_identity(args.portable_root)
        cases = [(f'six-{i}', 8, 6, 1, 8) for i in range(3)]
        if not args.six_only:
            cases += [(f'two-{seconds}-{relation}', seconds, 2, 3, track)
                      for seconds in (15, 30)
                      for relation, track in (('short', 6), ('equal', seconds), ('long', seconds+6))]
        fixtures = {}
        for name, seconds, scenes, preview, track in cases:
            report['current_case'] = name
            (args.output / 'matrix.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            if track not in fixtures:
                fixtures[track] = create_fixture(args.output / f'fixture-{track}', track)
            sources, music = fixtures[track]
            row = run_case(args.output / name, sources, music, seconds=seconds,
                           scenes=scenes, preview=preview, progress=lambda msg: print(msg, flush=True))
            if abs(row['music_seconds'] - track) >= 1e-6:
                raise ValueError('Duração real do MP3 diverge da linha da matriz.')
            row['name'] = name
            row['audio_relation'] = 'short' if track < seconds else 'equal' if track == seconds else 'long'
            report['cases'].append(row)
            (args.output / 'matrix.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        report['current_case'] = None
        report['success'] = True
    except Exception:
        report['errors'].append(traceback.format_exc())
    finally:
        report['elapsed_seconds'] = time.monotonic() - started
        (args.output / 'matrix.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
