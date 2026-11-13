import importlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from test_license_gates import deny_legacy_licensing

from test_client_download import fixture
import client_download


class BootstrapTests(unittest.TestCase):
    def test_decline_never_starts_worker_or_network(self):
        bootstrap = importlib.import_module('bootstrap_gui')
        with tempfile.TemporaryDirectory() as tmp, patch.object(client_download, 'ensure_tools') as fetch:
            session = bootstrap.DownloadSession(Path(tmp), client_download.load_manifest())
            session.decline()
            self.assertTrue(session.closed)
            self.assertFalse(session.busy)
            fetch.assert_not_called()

    def test_worker_progress_and_cancel_are_delivered_without_tk_from_worker(self):
        bootstrap = importlib.import_module('bootstrap_gui')
        import io
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, payload = fixture(root)
            session = bootstrap.DownloadSession(root, data)
            opener = Mock()
            opener.open.return_value = io.BytesIO(payload)
            with patch.object(client_download.urllib.request, 'build_opener', return_value=opener):
                session.accept()
                session.thread.join(5)
            events = session.drain()
            self.assertIn('progress', [kind for kind, value in events])
            self.assertIn('done', [kind for kind, value in events])
            self.assertFalse(session.busy)
            self.assertIsNotNone(client_download.verified_cache(root, data))
            session.close()
            self.assertTrue(session.cancel.is_set())
            self.assertTrue(session.closed)

    def test_close_while_busy_waits_for_worker_and_does_not_publish(self):
        bootstrap = importlib.import_module('bootstrap_gui')
        import threading
        entered, release = threading.Event(), threading.Event()
        def download(*args, **kwargs):
            entered.set()
            release.wait(5)
            client_download.cancelled(kwargs['cancel'])
        with tempfile.TemporaryDirectory() as tmp, patch.object(client_download, 'ensure_tools', side_effect=download):
            session = bootstrap.DownloadSession(Path(tmp), client_download.load_manifest())
            session.accept()
            self.assertTrue(entered.wait(2))
            session.close()
            self.assertTrue(session.busy)
            self.assertTrue(session.cancel.is_set())
            release.set()
            session.thread.join(5)
            self.assertEqual(session.drain()[-1][0], 'error')

    def test_first_reopen_and_decline_entry_wiring(self):
        import windows_entry
        gui = Mock()
        with patch.object(sys, 'frozen', True, create=True), \
                deny_legacy_licensing() as imports, \
                patch('bootstrap_gui.prepare_tools', return_value=None) as prepare, \
                patch.dict(sys.modules, {'menu_gui': gui}), \
                patch.object(windows_entry, 'Diagnostics', return_value=Mock(failures=0)):
            self.assertEqual(windows_entry.main([]), 0)
            imports.assert_not_called()
            prepare.assert_called_once()
            gui.run_app.assert_not_called()
        gui.run_app.return_value = 0
        with patch.object(sys, 'frozen', True, create=True), \
                deny_legacy_licensing() as imports, \
                patch('bootstrap_gui.prepare_tools', return_value={'cache_verified': True}), \
                patch.dict(sys.modules, {'menu_gui': gui}), \
                patch.object(windows_entry, 'Diagnostics', return_value=Mock(failures=0)):
            self.assertEqual(windows_entry.main([]), 0)
            imports.assert_not_called()
            gui.run_app.assert_called_once()

    def test_source_entry_does_not_bootstrap_and_smoke_authorization_is_explicit(self):
        import windows_entry
        gui = Mock()
        gui.run_app.return_value = 0
        with patch.object(sys, 'frozen', False, create=True), \
                patch('bootstrap_gui.prepare_tools') as prepare, \
                patch.dict(sys.modules, {'menu_gui': gui}), \
                patch.object(windows_entry, 'Diagnostics', return_value=Mock(failures=0)):
            self.assertEqual(windows_entry.main([]), 0)
            prepare.assert_not_called()
        import diagnosticos
        import threading
        original_hooks = (sys.excepthook, threading.excepthook, diagnosticos.active)
        with self.assertRaises(SystemExit):
            windows_entry.main(['--smoke-download'])
        self.assertEqual((sys.excepthook, threading.excepthook, diagnosticos.active), original_hooks)

    def test_verified_reopen_creates_no_gui_and_never_networks(self):
        bootstrap = importlib.import_module('bootstrap_gui')
        with patch.object(client_download, 'verified_cache', return_value={'cache_verified': True}) as cache, \
                patch.object(client_download, 'load_manifest', return_value={}), \
                patch('tkinter.Tk', side_effect=AssertionError('no GUI')), \
                patch.object(client_download.urllib.request, 'build_opener', side_effect=AssertionError('no network')):
            self.assertTrue(bootstrap.prepare_tools(Mock())['cache_verified'])

    def test_portuguese_consent_contains_cost_destination_and_visible_links(self):
        bootstrap = importlib.import_module('bootstrap_gui')
        root = Path('/portable')
        text = bootstrap.consent_text(root)
        for part in ('195 MB', 'internet', str(root / 'tools'), 'administrador', 'PATH', 'ZIP'):
            self.assertIn(part, text)
        self.assertIn('https://', text)
