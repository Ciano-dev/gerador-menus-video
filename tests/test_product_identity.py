"""Current distribution contract; legacy bytes are opaque compatibility fixtures."""
import ast
import hashlib
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TITLE = 'Gerador de menus em vídeo'
LEGACY_MARKER = bytes.fromhex('416e6973696f20636c69656e742d646f776e6c6f6164206c6f636b2076310a')


class ProductIdentityTests(unittest.TestCase):
    def test_current_versionable_paths_and_text_are_neutral(self):
        forbidden = re.compile(r'anis[i\u00ed]o', re.I)
        violations = []
        for path in ROOT.rglob('*'):
            parts = path.relative_to(ROOT).parts
            if any(p in ('.git', '__pycache__', '.cache', 'cache', 'evidence') for p in parts):
                continue
            if forbidden.search(path.relative_to(ROOT).as_posix()):
                violations.append(str(path.relative_to(ROOT)))
            # Third-party licenses/fonts and frozen demo assets retain original bytes.
            if path.is_file() and parts[0] not in ('font_assets',) and path.suffix in ('.py', '.md', '.spec', '.yml', '.ps1', '.txt', '.json'):
                if forbidden.search(path.read_text(encoding='utf-8')):
                    violations.append(str(path.relative_to(ROOT)) + ': text')
        self.assertEqual(violations, [])

    def test_named_entrypoint_and_spec_exist(self):
        self.assertTrue((ROOT / 'menu_video.py').is_file())
        self.assertIsNotNone(importlib.util.find_spec('menu_video'))
        self.assertTrue((ROOT / 'windows_build/GeradorMenusVideo.spec').is_file())

    def test_distribution_targets(self):
        spec = ROOT / 'windows_build/GeradorMenusVideo.spec'
        self.assertTrue(spec.is_file())
        self.assertEqual(spec.read_text(encoding='utf-8').count("name='Gerador-de-Menus'"), 2)
        pipeline = (ROOT / 'windows_build/pipeline.py').read_text(encoding='utf-8')
        self.assertIn("'Gerador-de-Menus.exe'", pipeline)
        self.assertIn("prefix = 'Gerador-de-Menus/'", pipeline)
        workflow = (ROOT / '.github/workflows/windows-portable.yml').read_text(encoding='utf-8')
        for expected in ('windows_build/GeradorMenusVideo.spec', 'dist/Gerador-de-Menus',
                         'ci-output/Gerador-de-Menus.zip', 'gerador-menus-video-windows-x64-candidate',
                         'MENU_VIDEO_CI_RUNNER_ENVIRONMENT', 'MENU_VIDEO_NATIVE_EVIDENCE_DIR',
                         'GeradorMenusVideo/logs/diagnostic.log'):
            self.assertIn(expected, workflow)

    def test_product_titles_and_appdata(self):
        for name in ('menu_gui.py', 'bootstrap_gui.py', 'windows_entry.py', 'diagnosticos.py'):
            self.assertIn(TITLE, (ROOT / name).read_text(encoding='utf-8'), name)
        for name in ('menu_gui.py', 'bootstrap_gui.py'):
            tree = ast.parse((ROOT / name).read_text(encoding='utf-8'))
            titles = [node.args[0].value for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and (node.func.attr == 'title' or
                           isinstance(node.func.value, ast.Name) and node.func.value.id == 'messagebox')]
            self.assertTrue(titles)
            self.assertEqual(set(titles), {TITLE}, name)
        import diagnosticos
        with tempfile.TemporaryDirectory() as temporary, patch.dict('os.environ', {'LOCALAPPDATA': temporary}):
            diagnostics = diagnosticos.Diagnostics()
            self.assertEqual(diagnostics.path, Path(temporary) / 'GeradorMenusVideo/logs/diagnostic.log')

    def test_export_lock_and_worker_namespace(self):
        for name in ('menu_video.py', 'gui_servicos.py'):
            path = ROOT / name
            self.assertTrue(path.is_file())
            self.assertIn('.menu-video.lock', path.read_text(encoding='utf-8'))
        for name in ('menu_video.py', 'media_runtime.py', 'gui_trabalho.py', 'smoke_windows.py'):
            self.assertIn('menu-video-', (ROOT / name).read_text(encoding='utf-8'))

    def test_new_marker_and_exact_legacy_lock_without_rewriting(self):
        import client_download as download
        self.assertEqual(hashlib.sha256(LEGACY_MARKER).hexdigest(),
                         '3b9f1b0e2137b1a494bab737dac538f1036b605664d7e0a92077d52c1d692078')
        self.assertEqual(download.LOCK_MARKER, b'MenuVideo client-download lock v1\n')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = root / '.client-download.lock'
            for marker in (LEGACY_MARKER, download.LOCK_MARKER, b''):
                with self.subTest(marker_sha256=hashlib.sha256(marker).hexdigest()):
                    lock.write_bytes(marker)
                    before = lock.stat()
                    with download.tool_lock(root, timeout=1):
                        # Keep this read below Windows' mandatory lock at byte 4096.
                        with lock.open('rb', buffering=0) as stream:
                            self.assertEqual(stream.read(len(download.LOCK_MARKER) + 1), marker)
                    self.assertEqual(lock.read_bytes(), marker)
                    self.assertEqual((lock.stat().st_ino, lock.stat().st_mtime_ns),
                                     (before.st_ino, before.st_mtime_ns))
            lock.unlink()
            with download.tool_lock(root, timeout=1):
                with lock.open('rb', buffering=0) as stream:
                    self.assertEqual(stream.read(len(download.LOCK_MARKER) + 1), download.LOCK_MARKER)
            self.assertEqual(lock.read_bytes(), download.LOCK_MARKER)

    def test_unknown_and_near_legacy_locks_rejected_unchanged(self):
        import client_download as download
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = root / '.client-download.lock'
            for marker in (b'unknown', LEGACY_MARKER + b'x', LEGACY_MARKER[:-1],
                           LEGACY_MARKER.lower(), b'X' * 4096):
                lock.write_bytes(marker)
                before = lock.stat()
                with self.subTest(marker_sha256=hashlib.sha256(marker).hexdigest()):
                    with self.assertRaisesRegex(ValueError, 'lock desconhecido'):
                        with download.tool_lock(root, timeout=1):
                            self.fail('Unknown lock accepted')
                    self.assertEqual(lock.read_bytes(), marker)
                    self.assertEqual((lock.stat().st_ino, lock.stat().st_mtime_ns),
                                     (before.st_ino, before.st_mtime_ns))

    def test_legacy_lock_on_actual_ensure_tools_path_and_offline_cache(self):
        import client_download as download
        import io
        from unittest.mock import Mock
        from test_client_download import fixture
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tools = root / 'tools'
            tools.mkdir()
            lock = tools / '.client-download.lock'
            lock.write_bytes(LEGACY_MARKER)
            before = lock.stat()
            data, payload = fixture(root)
            opener = Mock()
            opener.open.return_value = io.BytesIO(payload)
            with patch.object(download.urllib.request, 'build_opener', return_value=opener):
                result = download.ensure_tools(root, data, consent=True)
            opener.open.assert_called_once()
            self.assertTrue(result['cache_verified'])
            with patch.object(download.urllib.request, 'build_opener', side_effect=AssertionError('network')):
                cached = download.ensure_tools(root, data, consent=False)
            self.assertEqual(cached['directory'], result['directory'])
            self.assertEqual(cached['download_requests'], 0)
            self.assertEqual(lock.read_bytes(), LEGACY_MARKER)
            self.assertEqual((lock.stat().st_ino, lock.stat().st_mtime_ns),
                             (before.st_ino, before.st_mtime_ns))
