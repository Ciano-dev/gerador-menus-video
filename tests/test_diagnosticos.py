import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import Mock, patch


def load_module(name):
    spec = importlib.util.find_spec(name)
    return __import__(name) if spec else types.SimpleNamespace()


class DiagnosticsTests(unittest.TestCase):
    def api(self):
        module = load_module('diagnosticos')
        self.assertTrue(callable(getattr(module, 'Diagnostics', None)),
                        'diagnósticos devem existir antes de importar a aplicação')
        return module

    def test_full_chained_traceback_and_rotation(self):
        module = self.api()
        with tempfile.TemporaryDirectory() as tmp:
            diag = module.Diagnostics(Path(tmp), max_bytes=1600, backups=2)
            try:
                try:
                    raise KeyError('inner-synthetic')
                except KeyError as exc:
                    raise RuntimeError('outer-synthetic') from exc
            except RuntimeError:
                diag.write('startup', *sys.exc_info())
            content = diag.path.read_text(encoding='utf-8')
            self.assertIn('inner-synthetic', content)
            self.assertIn('direct cause', content)
            self.assertIn('raise RuntimeError', content)
            for _ in range(15):
                diag.write('startup', RuntimeError, RuntimeError('x' * 200), None)
            self.assertLessEqual(len(list(Path(tmp).iterdir())), 3)
            self.assertLessEqual(sum(p.stat().st_size for p in Path(tmp).iterdir()), 4800)

    def test_tk_callback_and_thread_hooks_log_complete_exception(self):
        module = self.api()
        with tempfile.TemporaryDirectory() as tmp:
            diag = module.Diagnostics(Path(tmp))
            messages = []
            diag.notify = messages.append
            root = types.SimpleNamespace()
            old_sys, old_thread = sys.excepthook, threading.excepthook
            try:
                diag.install()
                diag.attach_tk(root)
                try:
                    raise ValueError('callback-synthetic')
                except ValueError:
                    root.report_callback_exception(*sys.exc_info())
                thread = threading.Thread(target=lambda: (_ for _ in ()).throw(
                    RuntimeError('thread-synthetic')))
                thread.start()
                thread.join()
            finally:
                sys.excepthook, threading.excepthook = old_sys, old_thread
                module.active = None
            content = diag.path.read_text(encoding='utf-8')
            self.assertIn('callback-synthetic', content)
            self.assertIn('thread-synthetic', content)
            self.assertIn('Traceback', content)
            self.assertEqual(diag.failures, 2)
            self.assertTrue(all(str(diag.path) in message for message in messages))

    def test_log_write_failure_is_explicit_and_preserves_original(self):
        module = self.api()
        with tempfile.TemporaryDirectory() as tmp:
            diag = module.Diagnostics(Path(tmp))
            messages = []
            diag.notify = messages.append
            with patch.object(diag, 'write', side_effect=OSError('disk-denied')), \
                    patch('sys.stderr') as stderr:
                diag.report('startup', RuntimeError, RuntimeError('original-error'), None)
            self.assertIn('Falha ao gravar', messages[0])
            self.assertIn('disk-denied', messages[0])
            self.assertIn('original-error', messages[0])
            self.assertTrue(stderr.write.called)

    def test_import_time_failure_caught_before_tk_import(self):
        entry = Path('windows_entry.py')
        self.assertTrue(entry.is_file(), 'bootstrap deve capturar falhas de importação')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / 'check.py'
            script.write_text('''import builtins, sys
import windows_entry
import diagnosticos
diagnosticos.notify = lambda message: print(message, file=sys.stderr)
real = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'menu_gui':
        try:
            raise KeyError('inner-import')
        except KeyError as exc:
            raise ImportError('outer-import') from exc
    if name == 'tkinter':
        raise AssertionError('Tk imported too early')
    return real(name, *args, **kwargs)
builtins.__import__ = guarded
raise SystemExit(windows_entry.main([]))
''', encoding='utf-8')
            import os
            env = dict(os.environ, LOCALAPPDATA=str(root), PYTHONPATH=str(Path.cwd()),
                       PYTHONIOENCODING="utf-8")
            result = subprocess.run([sys.executable, str(script)], env=env,
                                    capture_output=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            log = root / 'SistemaMenuAnisio' / 'logs' / 'diagnostic.log'
            self.assertTrue(log.is_file(), result.stderr.decode())
            content = log.read_text(encoding='utf-8')
            self.assertIn('inner-import', content)
            self.assertIn('outer-import', content)
            self.assertIn('direct cause', content)

    def test_missing_localappdata_fallback_is_explicit(self):
        module = self.api()
        with patch.dict('os.environ', {}, clear=True):
            diag = module.Diagnostics()
        self.assertIn('fallback', diag.location_note.lower())
        self.assertTrue(diag.path.is_absolute())
