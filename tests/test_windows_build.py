import hashlib
import io
import importlib.util
import json
import shutil
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def api():
    spec = importlib.util.spec_from_file_location('windows_pipeline', ROOT / 'windows_build/pipeline.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildContractTests(unittest.TestCase):
    def test_client_manifest_valid_without_human_gate_or_source_companion(self):
        module = api()
        path = ROOT / 'windows_build/client-download-manifest.json'
        data = module.validate_manifest_file(path)
        self.assertEqual(data['policy'], 'client-consented-download')
        self.assertNotIn('approved', data)
        self.assertNotIn('compliance', data)

    def test_zip_traversal_links_case_collisions_and_windows_paths_rejected(self):
        module = api()
        for names in (['../escape'], ['/absolute'], ['C:/drive'], ['a\\evil'],
                      ['bin/ffmpeg.exe', 'BIN/FFMPEG.EXE'], ['bin/a:stream']):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'synthetic.zip'
                with zipfile.ZipFile(path, 'w') as archive:
                    for name in names:
                        entry = zipfile.ZipInfo(name)
                        entry.filename = name  # Keep malicious raw separators even on Windows.
                        archive.writestr(entry, 'synthetic')
                with self.assertRaises(ValueError): module.safe_members(path)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                entry = zipfile.ZipInfo('link')
                entry.create_system = 3
                entry.external_attr = 0o120777 << 16
                archive.writestr(entry, '../elsewhere')
            with self.assertRaises(ValueError): module.safe_members(path)

    def test_workflow_manual_only_pinned_and_validate_precedes_dependencies(self):
        content = (ROOT / '.github/workflows/windows-portable.yml').read_text()
        for part in ('workflow_dispatch:', 'windows-2022', 'contents: read',
                     'persist-credentials: false', 'if: always()', 'retention-days: 5',
                     'smoke_windows.ps1', '--consent-download', 'audit-archive'):
            self.assertIn(part, content)
        self.assertNotIn('push:', content)
        self.assertNotIn('pull_request:', content)
        self.assertNotIn('companion', content)
        self.assertLess(content.index('pipeline.py validate'), content.index('pip download'))
        for sha in ('34e114876b0b11c390a56381ad16ebd13914f8d5',
                    'a26af69be951a213d495a4c3e4e4022e16d87065',
                    'ea165f8d65b6e75b540449e92b4886f43607fa02'):
            self.assertIn(sha, content)
        self.assertIn("python-version: '3.13.17'", content)
        self.assertIn('PyInstaller==6.16.0', (ROOT / 'windows_build/requirements-build.txt').read_text())

    def test_spec_collects_notices_manifest_docs_but_no_vendor_binaries(self):
        content = (ROOT / 'windows_build/SistemaMenuAnisio.spec').read_text()
        for part in ('COLLECT(', 'console=False', 'upx=False', "contents_directory='_internal'",
                     'windows_entry.py', 'binaries=[]', 'client-download-manifest.json', 'README.md'):
            self.assertIn(part, content)
        self.assertNotIn('ffmpeg.exe', content)
        self.assertNotIn('ffprobe.exe', content)

    def package(self, root):
        module = api()
        for name in module.REQUIRED_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic package audit fixture only')
        path = root / '_internal/notices/runtime/PyInstaller/COPYING.txt'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'synthetic audit fixture only')
        shutil.copytree(ROOT / 'font_assets', root / '_internal/font_assets', dirs_exist_ok=True)
        return module

    def test_tree_and_final_zip_exclude_tools_and_binary_source_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Sistema-Menu-Anisio'
            module = self.package(root)
            module.audit_package(root)
            for name in ('tools/cache/ffmpeg.exe', '_internal/ffprobe.exe', 'vendor.zip',
                         'corresponding-source.zip', 'source.tar.xz', 'unknown.exe'):
                extra = root / name
                extra.parent.mkdir(parents=True, exist_ok=True)
                extra.write_bytes(b'forbidden')
                with self.subTest(name=name), self.assertRaises(ValueError): module.audit_package(root)
                archive = Path(tmp) / 'test-only.zip'
                with zipfile.ZipFile(archive, 'w') as out:
                    for path in root.rglob('*'):
                        if path.is_file(): out.write(path, 'Sistema-Menu-Anisio/' + path.relative_to(root).as_posix())
                with self.assertRaises(ValueError): module.audit_archive(archive)
                extra.unlink()
            # Empty directories left above must be rejected too (tools never belongs in a fresh ZIP).
            (root / 'tools/cache').rmdir()
            (root / 'tools').rmdir()
            with zipfile.ZipFile(archive, 'w') as out:
                for path in root.rglob('*'):
                    if path.is_file(): out.write(path, 'Sistema-Menu-Anisio/' + path.relative_to(root).as_posix())
            module.audit_archive(archive)

    def test_no_activation_resources_direct_ci_and_strong_exclusion_audit(self):
        # Contract §5/6 replaces the obsolete public-key/CI activation requirement.
        module = api()
        self.assertNotIn('_internal/issuer-public-key.json', module.REQUIRED_FILES)
        spec = (ROOT / 'windows_build/SistemaMenuAnisio.spec').read_text(encoding='utf-8')
        self.assertNotIn("str(root / 'issuer-public-key.json')", spec)
        for excluded in ('licensing', 'license_native', 'windows_build.ci_activation'):
            self.assertIn(repr(excluded), spec)
        workflow = (ROOT / '.github/workflows/windows-portable.yml').read_text(encoding='utf-8')
        self.assertNotIn('ci_activation.py', workflow)
        self.assertNotIn('native-activation-request', workflow)
        self.assertNotIn('GITHUB_TOKEN', workflow)
        self.assertLess(workflow.index('pipeline.py audit dist/'), workflow.index('./windows_build/smoke_windows.ps1'))
        self.assertIn('timeout-minutes: 45', workflow)
        self.assertIn('dump_traceback_later(240, exit=True)', workflow)
        smoke = (ROOT / 'windows_build/smoke_windows.ps1').read_text(encoding='utf-8')
        self.assertNotIn('activation.bin', smoke)
        self.assertNotIn('DPAPI', smoke)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Sistema-Menu-Anisio'
            self.package(root)
            module.audit_package(root)
            for name in ('_internal/issuer-private.pem', '_internal/windows_build/ci_activation.py',
                         '_internal/ci-licenses/123.json', '_internal/activation.bin',
                         '_internal/issuer-public-key.json', '_internal/licensing.pyc',
                         '_internal/license_native.pyc', '_internal/issuer/tool.py'):
                forbidden = root / name
                forbidden.parent.mkdir(parents=True, exist_ok=True)
                forbidden.write_bytes(b'forbidden synthetic fixture')
                with self.subTest(name=name), self.assertRaises(ValueError):
                    module.audit_package(root)
                archive = Path(tmp) / 'test-only.zip'
                with zipfile.ZipFile(archive, 'w') as out:
                    for path in root.rglob('*'):
                        if path.is_file(): out.write(path, root.name+'/'+path.relative_to(root).as_posix())
                with self.subTest(zip=name), self.assertRaises(ValueError):
                    module.audit_archive(archive)
                forbidden.unlink()
            # Directory remnants themselves must fail closed for CI/issuer data.
            (root / '_internal/ci-licenses').rmdir()
            (root / '_internal/issuer').rmdir()
            module.audit_package(root)

    def test_notices_mandatory_in_tree_and_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Sistema-Menu-Anisio'
            module = self.package(root)
            (root / '_internal/notices/runtime/Tk-license.terms').unlink()
            with self.assertRaises(ValueError): module.audit_package(root)
            archive = Path(tmp) / 'test-only.zip'
            with zipfile.ZipFile(archive, 'w') as out:
                for p in root.rglob('*'):
                    if p.is_file(): out.write(p, root.name + '/' + p.relative_to(root).as_posix())
            with self.assertRaises(ValueError): module.audit_archive(archive)

    def test_allowed_python_library_zip_cannot_hide_ffmpeg_or_source_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Sistema-Menu-Anisio'
            module = self.package(root)
            nested = root / '_internal/base_library.zip'
            for hidden in ('ffmpeg.exe', 'bin/ffprobe.exe', 'sources.zip',
                           'licensing.pyc', 'license_native.pyc', 'issuer/__init__.pyc',
                           'windows_build/ci_activation.pyc'):
                with self.subTest(hidden=hidden):
                    with zipfile.ZipFile(nested, 'w') as out: out.writestr(hidden, b'forbidden')
                    with self.assertRaises(ValueError): module.audit_package(root)
                    archive = Path(tmp) / 'test-only.zip'
                    with zipfile.ZipFile(archive, 'w') as out:
                        for path in root.rglob('*'):
                            if path.is_file(): out.write(path, root.name + '/' + path.relative_to(root).as_posix())
                    with self.assertRaises(ValueError): module.audit_archive(archive)
            with zipfile.ZipFile(nested, 'w') as out: out.writestr('encodings/__init__.pyc', b'synthetic bytecode')
            module.audit_package(root)

    def test_source_test_fetch_cli_refuses_binary_download_on_linux_or_without_consent(self):
        module = api()
        with tempfile.TemporaryDirectory() as tmp, patch.object(module, 'ensure_tools') as download:
            with patch.object(module.sys, 'platform', 'linux'):
                self.assertEqual(module.main(['fetch-tools', tmp, '--consent-download']), 1)
            with patch.object(module.sys, 'platform', 'win32'):
                self.assertEqual(module.main(['fetch-tools', tmp]), 1)
            download.assert_not_called()


class TclTermsTests(unittest.TestCase):
    def runner(self, root):
        for name in ('LICENSE.txt', 'tcl/tk8.6/license.terms', 'wheel/pyinstaller.dist-info/licenses/COPYING.txt'):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'actual-layout test fixture only: ' + name.encode())
        file = Path('pyinstaller.dist-info/licenses/COPYING.txt')
        return SimpleNamespace(files=[file], locate_file=lambda name: root / 'wheel' / name)

    def test_pinned_exact_tcl_metadata(self):
        module = api()
        self.assertEqual(module.TCL_TERMS_SIZE, 2295)
        self.assertEqual(module.TCL_TERMS_SHA256, '41613eabfc08921a7da9c4bfd7f3ce5d5406f55214b253d89f392ed86eebeb8f')
        self.assertEqual(module.TCL_TERMS_URL, 'https://raw.githubusercontent.com/python/cpython-bin-deps/5a51bb7b87efa119332c52b04a84c49d3042279c/amd64/tcllicense.terms')

    def test_missing_python313_tcl_layout_uses_verified_bytes_without_crlf_conversion(self):
        module = api()
        # Synthetic license fixture only: production bytes were not fetched on this VPS.
        terms = b'SYNTHETIC license integrity fixture\r\nline two\r\n'
        opener = Mock()
        opener.open.return_value = io.BytesIO(terms)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            distribution = self.runner(root)
            self.assertFalse((root / 'tcl/tcl8.6/license.terms').exists())
            with patch.object(module.sys, 'base_prefix', str(root)), \
                    patch.object(module.importlib.metadata, 'distribution', return_value=distribution), \
                    patch.object(module.urllib.request, 'build_opener', return_value=opener), \
                    patch.object(module, 'TCL_TERMS_SIZE', len(terms)), \
                    patch.object(module, 'TCL_TERMS_SHA256', hashlib.sha256(terms).hexdigest()):
                module.runtime_notices(root / 'stage')
            target = root / 'stage/notices/runtime'
            self.assertEqual((target / 'Tcl-license.terms').read_bytes(), terms)
            self.assertEqual((target / 'Python-LICENSE.txt').read_bytes(), (root / 'LICENSE.txt').read_bytes())
            self.assertEqual((target / 'Tk-license.terms').read_bytes(), (root / 'tcl/tk8.6/license.terms').read_bytes())
            self.assertTrue((target / 'PyInstaller/COPYING.txt').is_file())
            opener.open.assert_called_once_with(module.TCL_TERMS_URL, timeout=30)

    def test_bad_tcl_hash_or_missing_network_fails_closed(self):
        module = api()
        for payload in (b'wrong', b'x' * 2295, b'x' * 2296, None):
            with self.subTest(size=None if payload is None else len(payload)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                distribution = self.runner(root)
                opener = Mock()
                if payload is None: opener.open.side_effect = OSError('offline')
                else: opener.open.return_value = io.BytesIO(payload)
                with patch.object(module.sys, 'base_prefix', str(root)), \
                        patch.object(module.importlib.metadata, 'distribution', return_value=distribution), \
                        patch.object(module.urllib.request, 'build_opener', return_value=opener):
                    with self.assertRaises(ValueError): module.runtime_notices(root / 'stage')
                self.assertFalse((root / 'stage/notices/runtime/Tcl-license.terms').exists())

    def test_missing_python_tk_or_wheel_notices_fail_closed(self):
        module = api()
        for missing in ('LICENSE.txt', 'tcl/tk8.6/license.terms', 'wheel'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                distribution = self.runner(root)
                tcl = root / 'tcl/tcl8.6/license.terms'
                tcl.parent.mkdir(parents=True)
                tcl.write_bytes(b'actual layout test fixture only')
                if missing == 'wheel': distribution.files = []
                else: (root / missing).unlink()
                with patch.object(module.sys, 'base_prefix', str(root)), \
                        patch.object(module.importlib.metadata, 'distribution', return_value=distribution):
                    with self.assertRaises(ValueError): module.runtime_notices(root / 'stage')
