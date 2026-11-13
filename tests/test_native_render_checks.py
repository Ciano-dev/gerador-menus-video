"""Gates negativos de relatórios EOF, sem matriz lenta na descoberta."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_render_checks as checks


def complete_case(seconds=15):
    return {'seconds': seconds, 'scene_count': 2, 'preview_seconds': 3, 'profile': '1080p',
            'frames': seconds*30, 'full_decode': True, 'music_seconds': 6,
            'output_sha256': 'a'*64, 'decode_sha256': 'b'*64, 'audio_rms_start': 1000,
            'post_track_samples': 8000, 'post_track_rms': 0, 'non_repeating': True,
            'sources': [{'name': name, 'before': 'c'*64, 'after': 'c'*64, 'full_decode': True}
                        for name in ('a.mp4', 'b.mp4', 'c.mp3')],
            'streams': [{'codec_name': 'h264', 'width': 1920, 'height': 1080,
                         'pix_fmt': 'yuv420p', 'r_frame_rate': '30/1',
                         'nb_read_frames': str(seconds*30), 'duration': str(seconds),
                         'duration_ts': seconds*15360, 'time_base': '1/15360'},
                        {'codec_name': 'aac', 'channels': 2, 'duration': str(seconds),
                         'duration_ts': seconds*48000, 'time_base': '1/48000'}],
            'composition': {'exit_code': 0, 'timed_out': False, 'spontaneous_exit': True,
                            'progress_end': True, 'progress_frames': seconds*30,
                            'elapsed_seconds': 10, 'background_seconds': seconds,
                            'output_limit_seconds': seconds, 'preview_loop_count': 2, 'music_looped': False}}


class EOFReportTests(unittest.TestCase):
    def test_rejects_flags_only_and_missing_each_concrete_field(self):
        with self.assertRaises(ValueError):
            checks.validate_case({'verified': True, 'full_decode': True}, seconds=15, scenes=2, preview=3)
        valid = complete_case()
        checks.validate_case(valid, seconds=15, scenes=2, preview=3)
        for key in valid:
            invalid = copy.deepcopy(valid)
            del invalid[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                checks.validate_case(invalid, seconds=15, scenes=2, preview=3)
        for key in valid['composition']:
            invalid = copy.deepcopy(valid)
            del invalid['composition'][key]
            with self.subTest(composition=key), self.assertRaises(ValueError):
                checks.validate_case(invalid, seconds=15, scenes=2, preview=3)

    def test_rejects_terminated_output_wrong_scale_audio_and_changed_sources(self):
        cases = []
        for key, value in (('exit_code', 255), ('timed_out', True), ('spontaneous_exit', False),
                           ('progress_frames', 449), ('background_seconds', 30), ('music_looped', True)):
            row = complete_case(); row['composition'][key] = value; cases.append(row)
        for key, value in (('post_track_rms', 1000), ('post_track_samples', 0), ('frames', 449),
                           ('seconds', 30), ('decode_sha256', ''), ('non_repeating', False)):
            row = complete_case(); row[key] = value; cases.append(row)
        row = complete_case(); row['sources'][0]['after'] = 'd'*64; cases.append(row)
        row = complete_case(); row['streams'][0]['duration_ts'] -= 512; cases.append(row)
        for row in cases:
            with self.subTest(row=row), self.assertRaises(ValueError):
                checks.validate_case(row, seconds=15, scenes=2, preview=3)

    def test_native_identity_rejects_unverified_cache_before_render(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(checks.client_download, 'verified_cache', return_value=None), \
                patch.object(checks.motor, 'resolve_tool', return_value=__file__):
            with self.assertRaisesRegex(ValueError, 'cache'):
                checks.tool_identity(Path(directory))

    def test_matrix_is_separate_and_new_step_keeps_native_budgets(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / '.github/workflows/windows-portable.yml').read_text()
        self.assertIn('timeout-minutes: 45', workflow)
        self.assertIn('dump_traceback_later(600, exit=True)', workflow)
        self.assertIn('timeout-minutes: 4', workflow)
        self.assertIn('tests/render_eof_matrix.py --portable-root', workflow)
        self.assertLess(workflow.index('tests/render_eof_matrix.py'), workflow.index('python -m PyInstaller'))
        self.assertNotIn('test_render_eof_matrix.py', workflow)
        source_step = workflow.split('- name: Explicit tool download outside dist for source tests only', 1)[1].split('- name:', 1)[0]
        self.assertIn('          MENU_VIDEO_NATIVE_EVIDENCE_DIR: ci-logs/source-native-ui\n', source_step.split('        run:', 1)[0])


    def test_runner_preserves_failure_json(self):
        import importlib.util
        import json
        path = Path(__file__).with_name('render_eof_matrix.py')
        spec = importlib.util.spec_from_file_location('eof_matrix_runner', path)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'new-run'
            with patch.object(runner, 'tool_identity', return_value={}), \
                    patch.object(runner, 'create_fixture', side_effect=ValueError('fixture failure')):
                result = runner.main(['--output', str(output), '--portable-root', temporary])
            self.assertEqual(result, 1)
            report = json.loads((output / 'matrix.json').read_text())
            self.assertFalse(report['success'])
            self.assertEqual(report['cases'], [])
            self.assertIn('fixture failure', report['errors'][0])
            self.assertEqual(report['current_case'], 'six-0')
