import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import gui_servicos as services
import menu_video as motor
import client_download
from test_client_download import fixture


def portable_pair(root):
    data, payload = fixture(root)
    opener = unittest.mock.Mock()
    opener.open.return_value = io.BytesIO(payload)
    with patch.object(client_download.urllib.request, 'build_opener', return_value=opener):
        result = client_download.ensure_tools(root, data, consent=True)
    return data, Path(result['directory'])


class RuntimeTests(unittest.TestCase):
    def test_frozen_inspect_ignores_path_and_uses_internal_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            source = base / 'synthetic.mp4'
            source.touch()
            data, tools = portable_pair(base)
            exe = tools / 'ffprobe.exe'
            before = os.environ.get('PATH')
            with patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, 'executable', str(base / 'Gerador-de-Menus.exe')), \
                    patch.object(client_download, 'load_manifest', return_value=data), \
                    patch('shutil.which', return_value='/host/ffprobe'), \
                    patch.object(motor, 'probe_media', return_value={'streams': [
                        {'codec_type': 'video', 'duration': '1'}]}) as probe:
                services.inspect_media(source)
            self.assertEqual(probe.call_args.args[0], str(exe))
            self.assertEqual(os.environ.get('PATH'), before)

    def test_frozen_missing_fails_closed_even_with_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'synthetic.mp4'
            source.touch()
            with patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, 'executable', str(Path(tmp) / 'Gerador-de-Menus.exe')), \
                    patch('shutil.which', return_value='/host/ffprobe'), \
                    patch.object(motor, 'probe_media', return_value={'streams': [
                        {'codec_type': 'video', 'duration': '1'}]}):
                with self.assertRaisesRegex((motor.ErroProjeto, RuntimeError), 'pacote|empacotad'):
                    services.inspect_media(source)

    def test_source_retains_path_resolution(self):
        resolver = getattr(motor, 'resolve_tool', lambda name: None)
        with patch.object(sys, 'frozen', False, create=True), \
                patch('shutil.which', return_value='/local/ffmpeg') as which:
            self.assertEqual(resolver('ffmpeg'), '/local/ffmpeg')
            which.assert_called_once_with('ffmpeg')

    def test_windows_subprocess_no_console_preserves_arguments(self):
        with patch.object(sys, 'platform', 'win32'), \
                patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
                patch.object(motor.subprocess, 'run') as run:
            motor.run_media(['bundled.exe', '-version'], timeout=17)
        self.assertEqual(run.call_args.kwargs.get('creationflags'), 0x08000000)
        self.assertEqual(run.call_args.kwargs['timeout'], 17)
        self.assertTrue(run.call_args.kwargs['capture_output'])

    def test_frozen_rejects_external_executable_even_low_level(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'executable', str(Path(tmp) / 'Gerador-de-Menus.exe')), \
                patch.object(motor.subprocess, 'run') as run:
            with self.assertRaisesRegex((RuntimeError, motor.ErroProjeto), 'pacote|empacotad'):
                motor.run_media(['/host/ffprobe', '-version'])
            run.assert_not_called()

class RuntimeHelperTests(unittest.TestCase):
    def test_synthetic_helpers_also_use_bundle_and_hide_windows_console(self):
        import gerar_fixtures
        import verificar_demo
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            data, tool_dir = portable_pair(root)
            with patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, 'executable', str(root / 'Gerador-de-Menus.exe')), \
                    patch.object(client_download, 'load_manifest', return_value=data), \
                    patch.object(sys, 'platform', 'win32'), \
                    patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
                    patch('shutil.which', return_value='/host/tool'), \
                    patch.object(subprocess, 'run', return_value=SimpleNamespace(
                        stdout=b'{"streams": [], "format": {}}')) as run:
                gerar_fixtures.generate(root / 'fixtures')
                verificar_demo.inspect(root / 'fixtures' / 'cores-com-audio.mp4')
            calls = run.call_args_list
            self.assertEqual(len(calls), 5)
            self.assertTrue(all(str(tool_dir) in call.args[0][0] for call in calls))
            self.assertTrue(all(call.kwargs.get('creationflags') == 0x08000000 for call in calls))

class AnimatedSegmentRuntimeTests(unittest.TestCase):
    def test_segment_probe_uses_bundle_and_missing_probe_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            data, tool_dir = portable_pair(root)
            probe = tool_dir / 'ffprobe.exe'
            work, delivery = root / 'work', root / 'delivery'
            work.mkdir()
            delivery.mkdir()
            source = root / "Vídeos com espaço" / "ação [x]; % ' .mp4"
            scenes = [{'source': source, 'nome': 'Sintético',
                       'previa': {'inicio': 0, 'duracao_efetiva': 0.2}}]
            settings = {'visual': {'fundo': '#101f33', 'texto': '#f3ead6', 'destaque': '#c7a362'},
                        'exibir_rodape': False, 'musica': None, 'segundos': 0.4}
            with patch.object(sys, 'frozen', True, create=True), \
                    patch.object(sys, 'executable', str(root / 'Gerador-de-Menus.exe')), \
                    patch.object(client_download, 'load_manifest', return_value=data), \
                    patch('shutil.which', return_value='/host/ffprobe'), \
                    patch.object(motor, 'measure_text_pixels', return_value=(24, 8)), \
                    patch.object(motor, 'run_media') as render, \
                    patch.object(motor, 'probe_media', return_value={
                        'streams': [{'nb_read_frames': '6', 'duration': '0.2'}]}) as inspect:
                motor.render_animated('unused', work, delivery, 'Smoke', scenes, 'prova', False, settings)
                command = render.call_args_list[0].args[0]
                self.assertEqual(command[command.index('-i') + 1], str(source))
                self.assertNotIn(str(source), command[command.index('-vf') + 1])
                self.assertEqual(inspect.call_args.args[0], str(probe))
                self.assertTrue(inspect.call_args.kwargs['count_frames'])
                probe.unlink()
                with self.assertRaisesRegex(motor.ErroProjeto, 'pacote'):
                    motor.render_animated('unused', work, delivery, 'Smoke', scenes, 'prova', False, settings)
