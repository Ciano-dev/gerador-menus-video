"""Synthetic ZIPs only: these tests never fetch or execute vendor binaries."""
import copy
import hashlib
import importlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def fixture(root, entries=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, body in (entries or [('test/bin/ffmpeg.exe', b'synthetic ffmpeg'),
                                       ('test/bin/ffprobe.exe', b'synthetic ffprobe'),
                                       ('test/bin/ffplay.exe', b'never extract')]):
            archive.writestr(name, body)
    payload = stream.getvalue()
    data = json.loads((ROOT / 'windows_build/client-download-manifest.json').read_text())
    data['binary'].update(size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
                          ffmpeg_member='test/bin/ffmpeg.exe', ffprobe_member='test/bin/ffprobe.exe')
    return data, payload


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.m = importlib.import_module('client_download')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data, self.payload = fixture(self.root)

    def fetch(self, payload=None, **kwargs):
        opener = Mock()
        opener.open.return_value = io.BytesIO(self.payload if payload is None else payload)
        with patch.object(self.m.urllib.request, 'build_opener', return_value=opener):
            result = self.m.ensure_tools(self.root, self.data, consent=True, **kwargs)
        return result, opener

    def test_no_consent_and_import_have_no_network(self):
        with patch.object(self.m.urllib.request, 'build_opener') as network:
            importlib.reload(self.m)
            with self.assertRaisesRegex(ValueError, 'consent'):
                self.m.ensure_tools(self.root, self.data, consent=False)
            network.assert_not_called()
        self.assertFalse((self.root / 'tools').exists())

    def test_real_small_zip_only_pair_and_verified_offline_cache(self):
        result, opener = self.fetch()
        self.assertTrue(result['downloaded'])
        self.assertEqual(result['download_requests'], 1)
        self.assertEqual(result['download_bytes'], len(self.payload))
        self.assertTrue(result['cache_verified'])
        self.assertEqual({p.name for p in Path(result['directory']).iterdir()},
                         {'ffmpeg.exe', 'ffprobe.exe', 'receipt.json'})
        with patch.object(self.m.urllib.request, 'build_opener', side_effect=AssertionError('network')):
            cached = self.m.ensure_tools(self.root, self.data, consent=False)
        self.assertFalse(cached['downloaded'])
        self.assertEqual(cached['download_requests'], 0)
        self.assertEqual(result['directory'], cached['directory'])
        opener.open.assert_called_once()

    def test_hash_size_partial_failures_cleanup_owned_only_and_retry(self):
        tools = self.root / 'tools'
        tools.mkdir()
        for name in ('unknown.part', 'unknown.lock', 'ffmpeg.exe'):
            (tools / name).write_bytes(b'preserve')
        for payload in (b'wrong', b'x' * len(self.payload), self.payload + b'excess'):
            with self.subTest(payload=len(payload)), self.assertRaises(ValueError):
                self.fetch(payload)
            self.assertEqual(sorted(p.name for p in tools.glob('.stage-*')), [])
        class Broken(io.BytesIO):
            def read(self, size=-1):
                raise OSError('https://release-assets.githubusercontent.com/x?secret=SIGNED')
        opener = Mock()
        opener.open.return_value = Broken()
        with patch.object(self.m.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(Exception) as error:
                self.m.ensure_tools(self.root, self.data, consent=True)
        self.assertNotIn('SIGNED', str(error.exception))
        self.assertIsNone(error.exception.__cause__)
        for name in ('unknown.part', 'unknown.lock', 'ffmpeg.exe'):
            self.assertEqual((tools / name).read_bytes(), b'preserve')
        self.fetch()

    def test_tampered_cache_is_rejected_and_preserved_then_new_owned_pair(self):
        result, _ = self.fetch()
        tool = Path(result['directory']) / 'ffmpeg.exe'
        tool.write_bytes(b'tamper')
        self.assertIsNone(self.m.verified_cache(self.root, self.data))
        replacement, _ = self.fetch()
        self.assertNotEqual(replacement['directory'], result['directory'])
        self.assertEqual(tool.read_bytes(), b'tamper')

    def test_receipt_tied_to_manifest_and_pair_hashes(self):
        result, _ = self.fetch()
        receipt = Path(result['directory']) / 'receipt.json'
        data = json.loads(receipt.read_text())
        data['manifest_sha256'] = '0' * 64
        receipt.write_text(json.dumps(data))
        self.assertIsNone(self.m.verified_cache(self.root, self.data))

    def test_archive_traversal_link_duplicates_and_bounds(self):
        bad = [('../escape', b'x'), ('/absolute', b'x'), ('C:/drive', b'x'),
               ('bad\\file', b'x'), ('A:stream', b'x'), ('CON', b'x')]
        for entry in bad:
            with self.subTest(entry=entry):
                self.data, self.payload = fixture(self.root, [entry])
                with self.assertRaises(ValueError): self.fetch()
        link = zipfile.ZipInfo('link')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.data, self.payload = fixture(self.root, [(link, b'target')])
        with self.assertRaises(ValueError): self.fetch()
        self.data, self.payload = fixture(self.root, [('A', b'a'), ('a', b'b')])
        with self.assertRaises(ValueError): self.fetch()
        self.data, self.payload = fixture(self.root)
        with patch.object(self.m, 'MAX_EXTRACTED', 1), self.assertRaises(ValueError): self.fetch()

    def test_symlink_tools_or_cached_exe_rejected(self):
        external = self.root / 'external'
        external.mkdir()
        (self.root / 'tools').symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError): self.fetch()
        self.assertEqual(list(external.iterdir()), [])
        (self.root / 'tools').unlink()
        result, _ = self.fetch()
        tool = Path(result['directory']) / 'ffprobe.exe'
        tool.unlink()
        tool.symlink_to(external / 'unknown')
        self.assertIsNone(self.m.verified_cache(self.root, self.data))

    def test_unknown_lock_preserved(self):
        (self.root / 'tools').mkdir()
        lock = self.root / 'tools/.client-download.lock'
        lock.write_bytes(b'unknown')
        with self.assertRaisesRegex(ValueError, 'lock|trava'):
            self.fetch()
        self.assertEqual(lock.read_bytes(), b'unknown')

    def test_concurrent_launches_single_download_and_crash_releases_os_lock(self):
        results, errors = [], []
        opener = Mock()
        opener.open.side_effect = lambda *a, **k: io.BytesIO(self.payload)
        def start():
            try: results.append(self.m.ensure_tools(self.root, self.data, consent=True))
            except Exception as exc: errors.append(exc)
        with patch.object(self.m.urllib.request, 'build_opener', return_value=opener):
            threads = [threading.Thread(target=start) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(10)
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(r['downloaded'] for r in results), 1)
        self.assertEqual(opener.open.call_count, 1)
        # Actual separate process exits without releasing its context manager.
        code = 'from client_download import tool_lock; from pathlib import Path; import os; c=tool_lock(Path(__import__("sys").argv[1])); c.__enter__(); os._exit(0)'
        subprocess.run([sys.executable, '-c', code, str(self.root / 'tools')], check=True, timeout=10)
        with self.m.tool_lock(self.root / 'tools', timeout=1): pass

    def test_cancel_leaves_no_cache_and_can_retry(self):
        event = threading.Event()
        def cancel(*args): event.set()
        with self.assertRaises(Exception): self.fetch(cancel=event, progress=cancel)
        self.assertIsNone(self.m.verified_cache(self.root, self.data))
        self.fetch()

    def test_initial_and_redirect_policies_are_distinct_and_redacted(self):
        valid = self.data['binary']['url']
        self.m.initial_url(valid)
        signed = 'https://release-assets.githubusercontent.com/asset?sig=SIGNED'
        self.m.redirect_url(signed)
        request = urllib.request.Request(valid)
        redirected = self.m.HTTPSRedirect().redirect_request(request, None, 302, 'Found', {}, signed)
        self.assertEqual(redirected.full_url, signed)
        for url in (signed, valid + '?x=1', valid + '?', valid.replace('2026-10-22', '2026-10-21'), valid.replace('https:', 'http:'),
                    valid.replace('github.com', 'hostile.test'), valid.replace('github.com', 'user@github.com'),
                    valid.replace('github.com', 'github.com:444'), valid + '#fragment',
                    'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/tool.zip'):
            with self.subTest(initial=url), self.assertRaises(ValueError): self.m.initial_url(url)
        for url in ('http://github.com/x', 'https://evil.test/x?sig=SIGNED',
                    'https://user@github.com/x', 'https://@github.com/x', 'https://github.com:/x',
                    'https://github.com:444/x', 'https://github.com/x#f', 'https://github.com/x#'):
            with self.subTest(redirect=url), self.assertRaises(ValueError) as error:
                self.m.redirect_url(url)
            self.assertNotIn('SIGNED', str(error.exception))

    def test_pinned_production_metadata(self):
        data = self.m.load_manifest()
        self.assertEqual(data['binary']['size_bytes'], 193978038)
        self.assertEqual(data['binary']['sha256'], '5a8898e31f2c452062d1f1be475befd8ffba04db11e9f8251da3d10d9a1ae2e5')
        self.assertNotIn('approved', data)
        self.assertIn('937868360a21d711ec94a33f5591d345ad9b7754', data['links']['build_scripts'])

    def test_zip_file_directory_collisions_rejected_in_both_orders(self):
        for entries in ([('a/b', b'x'), ('A', b'y')], [('A', b'y'), ('a/b', b'x')]):
            with self.subTest(entries=entries):
                self.data, self.payload = fixture(self.root, entries)
                with self.assertRaises(ValueError): self.fetch()

    def test_empty_interrupted_lock_and_stale_staging_are_preserved_on_retry(self):
        tools = self.root / 'tools'
        tools.mkdir()
        lock = tools / '.client-download.lock'
        lock.touch()
        stale = tools / '.stage-unknown'
        stale.mkdir()
        (stale / 'vendor.zip.part').write_bytes(b'unknown interrupted data')
        self.fetch()
        self.assertEqual(lock.read_bytes(), b'')
        self.assertEqual((stale / 'vendor.zip.part').read_bytes(), b'unknown interrupted data')

    def test_foreign_lock_times_out_without_touching_then_retry_works(self):
        tools = self.root / 'tools'
        tools.mkdir()
        code = ('from client_download import tool_lock; from pathlib import Path; import sys; '
                'ctx=tool_lock(Path(sys.argv[1])); ctx.__enter__(); print("LOCKED", flush=True); sys.stdin.read(1)')
        process = subprocess.Popen([sys.executable, '-c', code, str(tools)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'LOCKED')
            with self.assertRaises(ValueError):
                with self.m.tool_lock(tools, timeout=0.15): self.fail('lock was not exclusive')
        finally:
            process.communicate('x', timeout=5)
        with self.m.tool_lock(tools, timeout=1): pass

    def test_diagnostics_do_not_leak_signed_query_in_chained_traceback(self):
        import diagnosticos
        opener = Mock()
        opener.open.side_effect = OSError('https://release-assets.githubusercontent.com/x?sig=SIGNED_SECRET')
        diagnostics = diagnosticos.Diagnostics(self.root / 'logs')
        with patch.object(self.m.urllib.request, 'build_opener', return_value=opener):
            try: self.m.ensure_tools(self.root, self.data, consent=True)
            except Exception as error:
                diagnostics.report('test download', type(error), error, error.__traceback__, notify_user=False)
        self.assertEqual(diagnostics.failures, 1)
        self.assertNotIn('SIGNED_SECRET', diagnostics.path.read_text())
