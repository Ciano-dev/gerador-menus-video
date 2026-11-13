import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]


def duration_report_fixture():
    # Double explícito de relatório, somente para os testes negativos do gate.
    inputs = []
    for live in (False, True):
        inputs.append({'origin': 'EXE', 'method': 'timestamps.frames' if live else 'tags.DURATION',
            'seconds': 1., 'stream_index': 1, 'frames': 30, 'first_timestamp': '0', 'end_timestamp': '1',
            'source_sha256_before': 'a'*64, 'source_sha256_after': 'a'*64, 'full_decode_exit': 0,
            'format': {} if live else {'duration': '1.5'},
            'stream': {'codec_type': 'video', 'tags': {} if live else {'DURATION': '00:00:01.000000000'}}})
    outputs = []
    for avi in (False, True):
        outputs.append({'origin': 'EXE', 'format': 'AVI' if avi else 'MP4',
            'container': 'avi' if avi else 'mov,mp4', 'decode_exit': 0,
            'sample': 'EXE-menu.avi' if avi else 'EXE-menu.mp4', 'frame': 'EXE-frame.png',
            'sample_sha256': 'b'*64, 'frame_sha256': 'c'*64,
            'streams': [{'codec_name': 'mpeg4' if avi else 'h264', 'width': 1920, 'height': 1080,
                'pix_fmt': 'yuv420p', 'r_frame_rate': '30/1', 'nb_read_frames': '12', 'duration': '.4'},
                {'codec_name': 'mp3' if avi else 'aac', 'channels': 2}],
            'timestamps': {'method': 'timestamps.frames', 'frames': 12, 'seconds': .4,
                           'first_timestamp': '0', 'end_timestamp': '2/5'}})
    return {'success': True, 'errors': [], 'save_reopen_equal': True,
        'origin': 'EXE', 'frozen': True, 'platform': 'win32', 'music_seconds': 1.5,
        'project_sha256_before': 'd'*64, 'project_sha256_after': 'd'*64,
        'inputs': inputs, 'outputs': outputs}



class NativeSmokeContractTests(unittest.TestCase):
    def test_smoke_without_display_fails_with_real_json_report(self):
        if sys.platform == 'win32' or os.environ.get('DISPLAY'):
            self.skipTest('Teste negativo específico do host Linux sem display; CI executa smoke nativo positivo.')
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / 'report.json'
            result = subprocess.run([sys.executable, str(ROOT / 'windows_entry.py'),
                                     '--smoke-test', '--report', str(report)],
                                    env=dict(os.environ, LOCALAPPDATA=tmp), capture_output=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(report.is_file(), 'smoke deve escrever relatório verdadeiro também na falha')
            data = json.loads(report.read_text(encoding='utf-8'))
            self.assertFalse(data['success'])
            self.assertTrue(data['errors'])
            self.assertIn('Traceback', data['errors'][0])

    def test_report_gate_rejects_worker_callback_and_no_heartbeat(self):
        spec = importlib.util.find_spec('smoke_windows')
        module = __import__('smoke_windows') if spec else types.SimpleNamespace()
        validate = getattr(module, 'validate_result', None)
        self.assertTrue(callable(validate), 'relatório exige validação de eventos e resultados reais')
        valid = self.valid_result()
        validate(valid)
        for key, value in (('worker_heartbeats', 0), ('worker_done', False),
                           ('errors', ['callback failure']), ('diagnostic_failures', 1),
                           ('outputs', []), ('close_requested', False)):
            with self.subTest(key=key):
                invalid = dict(valid, **{key: value})
                with self.assertRaises(ValueError):
                    validate(invalid)

    def valid_result(self):
        from test_gui_cores import report_fixture
        from test_etapa2 import interface_fixture
        from test_font_size import font_render_fixture, native_font_fixture
        from test_font_families import family_native_fixture, family_samples_fixture
        def output(profile, width, height, export_format='MP4'):
            avi = export_format == 'AVI'
            return {'profile': profile, 'format': export_format, 'container': 'avi' if avi else 'mov,mp4,m4a,3gp,3g2,mj2', 'verified': True, 'full_decode': True,
                    'font_render': font_render_fixture(profile, export_format),
                    'catalog_sha256': 'a' * 64,
                    'streams': [{'codec_name': 'mpeg4' if avi else 'h264', 'width': width, 'height': height,
                                 'pix_fmt': 'yuv420p', 'r_frame_rate': '30/1',
                                 'nb_read_frames': '12', 'duration': '0.400000'},
                                {'codec_name': 'mp3' if avi else 'aac', 'channels': 2}],
                    'output_files': ['previa.mp4'] if profile == 'prova' else ['menu.avi' if avi else 'menu.mp4'], 'sources': [{'full_decode': True, 'source_sha256_before': 'b' * 64,
                                'source_sha256_after': 'b' * 64}
                               for _ in range(2)]}
        return {'input_duration': duration_report_fixture(),
                'font_sizes': native_font_fixture(captures=True),
                'font_families': family_native_fixture(captures=True),
                'family_samples': family_samples_fixture(),
                'current_font_render': font_render_fixture('prova','MP4',current=True),
                'color_preview': report_fixture(), 'interface': interface_fixture(),
                'cancellation': {'button_main_thread': True, 'composition_started': True,
                                 'children_reaped': True, 'no_partials': True, 'sources_intact': True,
                                 'retry_verified': True, 'heartbeats': 4, 'seconds': .5},
                'window': {'viewable': True, 'width': 1040, 'height': 800,
                            'map_events': 1, 'destroy_event': True},
                 'worker_heartbeats': 3, 'worker_done': True, 'close_requested': True,
                 'errors': [], 'diagnostic_failures': 0,
                 'outputs': [output('prova', 640, 360), output('1080p', 1920, 1080), output('1080p', 1920, 1080, 'AVI')]}

    def test_report_gate_checks_streams_and_hashes_even_when_verified_flag_is_true(self):
        import smoke_windows
        cases = []
        wrong_stream = self.valid_result()
        wrong_stream['outputs'][1]['streams'][0]['width'] = 640
        cases.append(wrong_stream)
        wrong_hash = self.valid_result()
        wrong_hash['outputs'][0]['sources'][0]['source_sha256_after'] = 'c' * 64
        cases.append(wrong_hash)
        wrong_frames = self.valid_result()
        wrong_frames['outputs'][0]['streams'][0]['nb_read_frames'] = '0'
        cases.append(wrong_frames)
        duplicate = self.valid_result()
        duplicate['outputs'].append(copy.deepcopy(duplicate['outputs'][0]))
        cases.append(duplicate)
        for invalid in cases:
            with self.subTest(outputs=invalid['outputs']), self.assertRaises(ValueError):
                smoke_windows.validate_result(invalid)

    def test_synthetic_services_really_render_both_profiles_without_display(self):
        import smoke_windows
        with tempfile.TemporaryDirectory() as tmp:
            result = smoke_windows.synthetic_services(Path(tmp), lambda message: None)
            report = self.valid_result()
            report['outputs'] = result['outputs']
            report['input_duration'] = result['input_duration']
            report['family_samples'] = result['family_samples']
            report['current_font_render'] = result['current_font_render']
            smoke_windows.validate_result(report)
            for row in result['outputs']:
                factor = 1 if row['profile'] == '1080p' else 1/3
                self.assertEqual(row['font_render']['sizes'],
                                 {key: round(size*factor) for key, size in (('title', 60), ('scene', 36), ('number', 32))})
                self.assertTrue(Path(row['font_render']['frame']).is_file())
                self.assertTrue(Path(row['font_render']['sample']).is_file())
            self.assertEqual(len(result['family_samples']['samples']), 9)
            self.assertTrue(Path(result['current_font_render']['frame']).is_file())
            self.assertEqual({r['id'] for r in result['current_font_render']['font_roles'].values()}, {None})
            self.assertEqual(result['services'], ['inspect_media', 'save_project', 'open_project',
                                                  'preview_project', 'render_project'])

    def test_native_smoke_also_exercises_real_mkv_tag_and_timestamp_inputs(self):
        import smoke_windows
        with tempfile.TemporaryDirectory() as tmp:
            result = smoke_windows.synthetic_services(Path(tmp), lambda message: None)
            self.assertIn('input_duration', result, 'Smoke atual precisa reproduzir o bug MKV no executável')
            row = result['input_duration']
            self.assertEqual([s['method'] for s in row['inputs']], ['tags.DURATION', 'timestamps.frames'])
            self.assertTrue(row['save_reopen_equal'])
            self.assertEqual({s['format'] for s in row['outputs']}, {'MP4', 'AVI'})
            for output in row['outputs']:
                self.assertTrue(Path(output['sample']).is_file())
                self.assertTrue(Path(output['frame']).is_file())
                self.assertEqual(output['decode_exit'], 0)
            self.assertTrue(Path(row['report']).is_file())

    def test_report_gate_requires_duration_regression_proof(self):
        import smoke_windows
        invalid = self.valid_result()
        invalid.pop('input_duration', None)
        with self.assertRaisesRegex(ValueError, 'duração'):
            smoke_windows.validate_result(invalid)

    def test_duration_report_rejects_incomplete_mixed_origin_or_false_hashes(self):
        from native_render_checks import validate_input_duration_case
        valid = duration_report_fixture()
        validate_input_duration_case(valid, frozen=True)
        for key in valid:
            row = copy.deepcopy(valid)
            del row[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                validate_input_duration_case(row, frozen=True)
        for field, value in (('origin', 'SOURCE-HOST'), ('sample_sha256', ''), ('decode_exit', 1)):
            row = copy.deepcopy(valid)
            row['outputs'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_input_duration_case(row, frozen=True)
        row = copy.deepcopy(valid)
        row['inputs'][0]['source_sha256_after'] = 'f'*64
        with self.assertRaises(ValueError):
            validate_input_duration_case(row, frozen=True)

    def test_duration_helper_preserves_last_proof_if_next_export_fails(self):
        import native_render_checks as checks
        import gui_servicos as services
        from unittest.mock import patch
        original = services.render_project
        def render(project, *args, **kwargs):
            if project.export_format == 'AVI':
                raise ValueError('falha controlada posterior')
            return original(project, *args, **kwargs)
        with tempfile.TemporaryDirectory() as tmp, patch.object(services, 'render_project', side_effect=render):
            directory = Path(tmp) / 'duration'
            with self.assertRaisesRegex(ValueError, 'posterior'):
                checks.run_input_duration_case(directory)
            report = json.loads((directory / 'SOURCE-HOST-duration-report.json').read_text())
            self.assertFalse(report['success'])
            self.assertEqual(len(report['outputs']), 1)
            self.assertTrue(Path(report['outputs'][0]['sample']).is_file())
            self.assertTrue(Path(report['outputs'][0]['frame']).is_file())
            self.assertEqual(report['outputs'][0]['origin'], 'SOURCE-HOST')

    def test_report_gate_requires_real_cancel_then_success(self):
        import smoke_windows
        valid = self.valid_result()
        valid.pop('cancellation', None)
        with self.assertRaisesRegex(ValueError, 'cancelamento'):
            smoke_windows.validate_result(valid)

    def test_report_gate_rejects_each_incomplete_cancellation_proof(self):
        import smoke_windows
        for key in ('button_main_thread', 'composition_started', 'children_reaped',
                    'no_partials', 'sources_intact', 'retry_verified', 'heartbeats', 'seconds'):
            invalid = self.valid_result()
            invalid['cancellation'][key] = False
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'cancelamento'):
                smoke_windows.validate_result(invalid)
        content = (ROOT / 'windows_build/smoke_windows.ps1').read_text()
        for field in ('button_main_thread', 'composition_started', 'children_reaped', 'no_partials',
                      'sources_intact', 'retry_verified', 'heartbeats', 'seconds'):
            self.assertIn('$cancel.' + field, content)

    def test_report_records_actual_output_names(self):
        import smoke_windows
        with tempfile.TemporaryDirectory() as tmp:
            result = smoke_windows.synthetic_services(Path(tmp), lambda message: None)
            self.assertEqual(result['outputs'][0]['output_files'], ['previa.mp4'])
            self.assertEqual(result['outputs'][1]['output_files'], ['menu.mp4'])

    def test_orchestrator_uses_two_launches_minimal_child_environment_and_timeout(self):
        path = ROOT / 'windows_build' / 'smoke_windows.ps1'
        self.assertTrue(path.is_file(), 'orquestrador nativo deve existir')
        content = path.read_text()
        self.assertIn('1..2', content)
        self.assertIn('Expand-Archive', content)
        self.assertIn('ProcessStartInfo', content)
        self.assertIn('.Environment.Clear()', content)
        self.assertIn('System32', content)
        self.assertIn('WaitForExit', content)
        self.assertIn('Kill($true)', content)
        self.assertIn('--smoke-test', content)
        self.assertIn('ConvertFrom-Json', content)
        self.assertNotIn('python ', content.lower())

    def test_frozen_report_requires_actual_download_then_verified_reuse(self):
        import client_download
        import smoke_windows
        manifest = client_download.load_manifest()
        report = self.valid_result()
        report['frozen'] = True
        bootstrap = {'cache_verified': True, 'downloaded': True, 'download_requests': 1,
                     'download_bytes': 193978038, 'manifest_sha256': client_download.fingerprint(manifest),
                     'archive_sha256': manifest['binary']['sha256'], 'ui_heartbeats': 3, 'window_viewable': True}
        report.update(bootstrap=bootstrap, download_authorized=True, network_events=4)
        from test_native_render_checks import complete_case
        report.update(smoke_scale_seconds=15, scaled_render=complete_case(),
                      scale_identity={'frozen': True, 'platform': 'win32', 'portable_verified': True,
                                      'manifest_sha256': bootstrap['manifest_sha256'],
                                      'archive_sha256': bootstrap['archive_sha256'],
                                      'manifest_file_sha256': 'd'*64,
                                      'tools': {name: {'path': name, 'sha256': 'e'*64,
                                                       'version': name + ' version synthetic-test'}
                                                for name in ('ffmpeg', 'ffprobe')}},
                      tools={name: name for name in ('ffmpeg', 'ffprobe')})
        smoke_windows.validate_result(report)
        for key, value in (('cache_verified', False), ('download_requests', 2), ('download_bytes', 0),
                           ('archive_sha256', '0' * 64), ('ui_heartbeats', 0)):
            invalid = copy.deepcopy(report)
            invalid['bootstrap'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): smoke_windows.validate_result(invalid)
        for key in ('smoke_scale_seconds', 'scaled_render', 'scale_identity'):
            invalid = copy.deepcopy(report)
            del invalid[key]
            with self.subTest(missing_scale=key), self.assertRaises(ValueError):
                smoke_windows.validate_result(invalid)
        invalid = copy.deepcopy(report)
        invalid['scale_identity']['tools']['ffmpeg']['sha256'] = ''
        with self.assertRaises(ValueError): smoke_windows.validate_result(invalid)
        report.update(download_authorized=False, network_events=0)
        report['bootstrap'].update(downloaded=False, download_requests=0, download_bytes=0)
        smoke_windows.validate_result(report)
        report['network_events'] = 1
        with self.assertRaises(ValueError): smoke_windows.validate_result(report)

    def test_smoke_downloader_flag_only_first_launch_and_archive_is_immutable(self):
        content = (ROOT / 'windows_build/smoke_windows.ps1').read_text()
        self.assertIn("if ($launch -eq 1)", content)
        self.assertIn("$start.ArgumentList.Add('--smoke-download')", content)
        self.assertIn('900000', content)
        self.assertIn('download_requests', content)
        self.assertIn('cache_verified', content)
        self.assertIn('network_events', content)
        self.assertIn('Get-FileHash', content)
        self.assertLess(content.index('Compress-Archive'), content.index('foreach ($launch'))
        self.assertNotIn('_internal/ffmpeg/bin', content)


class FrozenScaleGateTests(unittest.TestCase):
    def test_scale_rejects_fake_incomplete_wrong_duration_and_missing_progress(self):
        import smoke_windows
        from test_native_render_checks import complete_case
        base = NativeSmokeContractTests().valid_result()
        base.update(smoke_scale_seconds=15, scaled_render=complete_case())
        smoke_windows.validate_result(base)
        for value in ({}, {'verified': True}, complete_case(30)):
            invalid = copy.deepcopy(base); invalid['scaled_render'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                smoke_windows.validate_result(invalid)
        for key in ('progress_end', 'spontaneous_exit'):
            invalid = copy.deepcopy(base); invalid['scaled_render']['composition'][key] = False
            with self.subTest(key=key), self.assertRaises(ValueError):
                smoke_windows.validate_result(invalid)
        ps = (ROOT / 'windows_build/smoke_windows.ps1').read_text()
        for field in ('--smoke-scale-seconds', '$scaleSeconds', '$scale.frames', '$scale.decode_sha256',
                      '$composition.spontaneous_exit', '$composition.progress_end', '$scale.post_track_rms'):
            self.assertIn(field, ps)
        self.assertIn('> 180', (ROOT / 'smoke_windows.py').read_text())
