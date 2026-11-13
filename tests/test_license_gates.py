"""Rotas públicas independem dos módulos de ativação excluídos da seleção.

O guard é exclusivo dos testes: nenhuma implementação legada é recriada.
"""
import builtins
from contextlib import contextmanager
import importlib.abc
import importlib.util
import shutil
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import windows_entry


LEGACY_MODULES = ('licensing', 'license_native', 'windows_build.ci_activation')


@contextmanager
def deny_legacy_licensing():
    """Falha até se a aplicação capturar a tentativa de importar o legado."""
    attempted = Mock(name='legacy_import')
    original_import = builtins.__import__

    def check(name):
        if any(name == module or name.startswith(module + '.') for module in LEGACY_MODULES):
            attempted(name)
            raise AssertionError('Consulta ao licenciamento legado: ' + name)

    class DenyLegacy(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            check(fullname)

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        absolute = name
        if level:
            absolute = importlib.util.resolve_name('.' * level + name, (globals or {})['__package__'])
        check(absolute)
        for item in fromlist or ():
            check(absolute + '.' + item)
        return original_import(name, globals, locals, fromlist, level)

    # Não deixa um módulo legado previamente carregado contornar o finder.
    for name in tuple(sys.modules):
        check(name)
    finder = DenyLegacy()
    sys.meta_path.insert(0, finder)
    try:
        with patch.object(builtins, '__import__', guarded_import):
            yield attempted
    finally:
        sys.meta_path.remove(finder)
        attempted.assert_not_called()


class LegacyImportGuardTests(unittest.TestCase):
    def test_guard_detects_imports_even_when_application_swallows_them_and_restores_scope(self):
        original_import = builtins.__import__
        original_finders = list(sys.meta_path)
        before = {name: sys.modules.get(name) for name in LEGACY_MODULES}
        for name in LEGACY_MODULES:
            for route in ('statement', 'dynamic'):
                with self.subTest(module=name, route=route):
                    with self.assertRaisesRegex(AssertionError, 'legacy_import'):
                        with deny_legacy_licensing() as attempted:
                            with self.assertRaisesRegex(AssertionError, 'licenciamento legado'):
                                if route == 'statement':
                                    __import__(name)
                                else:
                                    importlib.import_module(name)
                            attempted.assert_called_once_with(name)
                    self.assertIs(builtins.__import__, original_import)
                    self.assertEqual(sys.meta_path, original_finders)
                    self.assertEqual({name: sys.modules.get(name) for name in LEGACY_MODULES}, before)
        with self.assertRaisesRegex(AssertionError, 'legacy_import'):
            with deny_legacy_licensing():
                with self.assertRaisesRegex(AssertionError, 'licenciamento legado'):
                    __import__('windows_build', fromlist=('ci_activation',))


class NoActivationRuntimeTests(unittest.TestCase):
    def test_source_and_frozen_start_and_smoke_reach_their_real_routes(self):
        for frozen in (False, True):
            for argv in ([], ['--smoke-test', '--report', 'unused.json']):
                with self.subTest(frozen=frozen, argv=argv), \
                        patch.object(sys, 'frozen', frozen, create=True), \
                        deny_legacy_licensing() as imports, \
                        patch('bootstrap_gui.prepare_tools', return_value={'cache_verified': True}) as tools, \
                        patch('smoke_windows.run_smoke', return_value=0) as smoke, \
                        patch('menu_gui.run_app', return_value=0) as gui, \
                        patch.object(windows_entry, 'Diagnostics', return_value=Mock(failures=0)):
                    self.assertEqual(windows_entry.main(argv), 0)
                    imports.assert_not_called()
                    if argv:
                        smoke.assert_called_once(); gui.assert_not_called()
                    else:
                        gui.assert_called_once()
                        self.assertEqual(tools.call_count, int(frozen))

    def test_current_product_imports_without_legacy_modules_or_public_resource(self):
        root = Path(__file__).resolve().parents[1]
        script = r'''
import importlib.abc, sys
from pathlib import Path
assert not Path('issuer-public-key.json').exists()
assert not Path('licensing.py').exists()
class DenyLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('licensing', 'license_native', 'windows_build.ci_activation'):
            raise AssertionError('current product imports '+fullname)
sys.meta_path.insert(0, DenyLegacy())
import menu_gui, menu_video, gui_servicos, windows_entry, bootstrap_gui, smoke_windows
assert not any(n in sys.modules for n in ('licensing', 'license_native'))
assert menu_video.main(sys.argv[1:]) == 0
print('current runtime imports and real CLI export independent')
'''
        from gui_autoria import Project
        import gui_servicos as services
        import menu_video as motor
        with tempfile.TemporaryDirectory(prefix='isolated-current-product-') as temporary:
            isolated = Path(temporary)
            for source in root.glob('*.py'):
                if source.name not in ('licensing.py', 'license_native.py'):
                    shutil.copyfile(source, isolated/source.name)
            (isolated/'windows_build').mkdir()
            shutil.copyfile(root/'windows_build/client-download-manifest.json',
                            isolated/'windows_build/client-download-manifest.json')
            source = isolated/'source.mp4'
            motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg')) + [
                '-f', 'lavfi', '-i', 'testsrc2=s=64x36:r=30:d=0.2',
                '-c:v', 'libx264', '-threads', '1', str(source)])
            project = Project(title='Sem dependência legada', duration=.1)
            project.add_video(services.inspect_media(source))
            project.edit_scene(0, 'Cena', 0, .1)
            saved = services.save_project(project, isolated/'project.menu')
            local = isolated/'localappdata'
            old = local/'LegacyFixture/activation/activation.bin'
            old.parent.mkdir(parents=True)
            previous = b'\x00\xffprevious opaque bytes remain intact'
            old.write_bytes(previous)
            result = subprocess.run([sys.executable, '-B', '-c', script, str(saved),
                '--saida', str(isolated/'menu.mp4'), '--perfil', 'prova'], cwd=isolated,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'LOCALAPPDATA': str(local)})
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))
            self.assertEqual(old.read_bytes(), previous)
            motor.check_decode(motor.resolve_tool('ffmpeg'), isolated/'menu.mp4')

    def test_absent_corrupt_expired_machine_fault_and_clock_rollback_export_preserves_old_bytes(self):
        from gui_autoria import Project
        import gui_servicos as services
        import menu_video as motor
        with tempfile.TemporaryDirectory(prefix='no-activation-') as temporary:
            base = Path(temporary)
            source = base / 'source.mp4'
            motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg')) + [
                '-f', 'lavfi', '-i', 'testsrc2=s=64x36:r=30:d=0.2',
                '-c:v', 'libx264', '-threads', '1', str(source)])
            source_hash = motor.sha256(source)
            project = Project(title='Menu', duration=.1)
            project.add_video(services.inspect_media(source))
            project.edit_scene(0, 'Cena', 0, .1)
            # Os bytes antigos são opacos: o guard impede chegar ao legado,
            # inclusive às consultas de máquina/relógio, sem recriá-lo nos testes.
            states = [('absent', None), ('corrupt', b'\x00\xffcorrupt previous state'),
                ('expired', b'{"expires_at":"2000-01-01T00:00:00+00:00"}'),
                ('machine_fault', b'previous opaque machine-bound bytes'),
                ('clock_rollback', b'previous opaque high-water bytes')]
            for name, old in states:
                local = base / name
                state = local / 'LegacyFixture/activation/activation.bin'
                if old is not None:
                    state.parent.mkdir(parents=True)
                    state.write_bytes(old)
                before = {p.relative_to(local).as_posix(): p.read_bytes() for p in local.rglob('*') if p.is_file()}
                with self.subTest(state=name), patch.dict(os.environ, {'LOCALAPPDATA': str(local)}), \
                        deny_legacy_licensing() as imports:
                    output = services.render_project(project, None, base / (name+'.mp4'), profile='prova')
                    self.assertTrue(output.is_file())
                    motor.check_decode(motor.resolve_tool('ffmpeg'), output)
                    imports.assert_not_called()
                self.assertEqual(before, {p.relative_to(local).as_posix(): p.read_bytes() for p in local.rglob('*') if p.is_file()})
            self.assertEqual(motor.sha256(source), source_hash)

    def test_gui_jobs_and_direct_frozen_smoke_have_no_legacy_gate(self):
        import inspect
        import menu_gui, menu_video, gui_servicos, smoke_windows
        for callback in (menu_gui.App.make_preview, menu_gui.App.make_export,
                         menu_video.export_project, menu_video.export_media,
                         gui_servicos.preview_project, gui_servicos.render_project, smoke_windows.run_smoke):
            with self.subTest(route=callback.__qualname__):
                self.assertNotIn('licensing', inspect.getsource(callback))
                self.assertNotIn('require_for_job', inspect.getsource(callback))
