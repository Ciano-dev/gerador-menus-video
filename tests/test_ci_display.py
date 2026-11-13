"""Artificial Win32 fixtures only: these tests do NOT prove native execution."""
import base64
import ctypes as C
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from windows_build import ci_display as display

ROOT = Path(__file__).resolve().parents[1]
CI = {'GITHUB_ACTIONS': 'true', 'RUNNER_OS': 'Windows',
      'RUNNER_ENVIRONMENT': 'github-hosted',
      'MENU_VIDEO_CI_RUNNER_ENVIRONMENT': 'github-hosted'}


def mode(width=1024, height=768, frequency=60):
    value = display.DEVMODEW()
    value.dmSize = C.sizeof(value)
    value.dmFields = 0x5c0000
    value.dmPelsWidth, value.dmPelsHeight = width, height
    value.dmBitsPerPel, value.dmDisplayFrequency = 32, frequency
    return bytes(value)


class ArtificialNative:
    def __init__(self):
        self.original = mode()
        self.active = self.original
        self.supported = [self.original, mode(1920, 1080)]
        self.calls = []
        self.result = 0
        self.wrong_apply = False
        self.measure_error = False

    def current(self):
        return self.active

    def enumerate(self):
        self.calls.append('enumerate')
        return self.supported

    def change(self, raw):
        self.calls.append(('change', raw))
        if self.result == 0 and not self.wrong_apply:
            self.active = raw
        return self.result

    def measure(self):
        if self.measure_error:
            raise display.DisplayError('probe_client')
        return {'workarea': [0, 0, 1920, 1040], 'screen': [0, 0, 1920, 1080],
                'virtual': [0, 0, 1920, 1080], 'metrics': [8, 8, 23, 4],
                'probes': [dict(request=[w, h], adjusted=[w+16, h+39],
                                client=[0, 0, w, h], outer=[0, 0, w+16, h+39],
                                capture=[8, 31, w+8, h+31])
                           for w, h in display.CLIENT_SIZES]}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'private-state.json'
        self.log = Path(self.tmp.name) / 'safe.jsonl'
        self.native = ArtificialNative()
        self.factory = Mock(return_value=self.native)

    def run_operation(self, operation='prepare', env=None, platform='win32'):
        with patch('sys.stdout', new_callable=io.StringIO):
            return display.execute(operation, self.state, self.log, platform=platform,
                                   environment=dict(CI) if env is None else env,
                                   backend_factory=self.factory)

    def events(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_guard_denies_all_partial_malformed_and_nonwindows_before_backend(self):
        environments = [{}, {**CI, 'RUNNER_ENVIRONMENT': 'self-hosted'},
                        {**CI, 'MENU_VIDEO_CI_RUNNER_ENVIRONMENT': 'self-hosted'}]
        for key in CI:
            environments.extend([{k: v for k, v in CI.items() if k != key},
                                 {**CI, key: ''}, {**CI, key: CI[key].upper()},
                                 {**CI, key: CI[key]+' '}])
        for operation in ('prepare', 'restore'):
            for env in environments:
                with self.subTest(operation=operation, env=env):
                    self.assertEqual(self.run_operation(operation, env), 1)
            for platform in ('linux', 'darwin', 'win32 ', ''):
                self.assertEqual(self.run_operation(operation, platform=platform), 1)
        self.factory.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_supported_prepare_persists_exact_mode_before_first_change(self):
        original_change = self.native.change
        def inspect_state(raw):
            data = json.loads(self.state.read_text())
            self.assertEqual(base64.b64decode(data['mode']), self.native.original)
            self.assertIn('enumerate', self.native.calls)
            return original_change(raw)
        self.native.change = inspect_state
        self.assertEqual(self.run_operation(), 0)
        self.assertEqual(display.summary(self.native.active)['size'], [1920, 1080])
        self.assertEqual([e['event'] for e in self.events()],
                         ['before', 'enumerated', 'state_saved', 'apply', 'after', 'measurements', 'prepared'])

    def test_unsupported_mode_fails_without_mutation_or_state(self):
        self.native.supported = [mode(), mode(1600, 1200)]
        self.assertEqual(self.run_operation(), 1)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.native.calls, ['enumerate'])

    def test_apply_native_error_is_failure_and_keeps_restore_state(self):
        self.native.result = -2
        self.assertEqual(self.run_operation(), 1)
        self.assertTrue(self.state.exists())
        self.assertTrue(any(e.get('code') == -2 for e in self.events()))
        self.assertEqual(self.events()[-1]['event'], 'rollback_failed')

    def test_actual_resolution_mismatch_fails_even_after_native_success(self):
        self.native.wrong_apply = True
        self.assertEqual(self.run_operation(), 1)
        self.assertIn('apply_mismatch', [e.get('category') for e in self.events()])
        self.assertTrue(self.state.exists())

    def test_insufficient_measurements_fail_and_immediately_restore(self):
        normal = self.native.measure
        variants = [lambda m: m.update(workarea=[0, 0, 1366, 768]),
                    lambda m: m['probes'][1].update(client=[0, 0, 1300, 768]),
                    lambda m: m['probes'][1].update(outer=[0, 0, 1366, 768]),
                    lambda m: m['probes'][1].update(capture=[1900, 0, 3266, 768]),
                    lambda m: m.update(screen=[0, 0, 1024, 768]),
                    lambda m: m['probes'][0].update(adjusted=[1024, 768])]
        for mutate in variants:
            with self.subTest(mutate=mutate):
                if self.state.exists(): self.state.unlink()
                def measure():
                    result = normal(); mutate(result); return result
                self.native.measure = measure
                self.assertEqual(self.run_operation(), 1)
                self.assertEqual(self.native.active, self.native.original)
                self.assertTrue(self.state.exists())

    def test_probe_failure_preserves_error_and_restore_state(self):
        self.native.measure_error = True
        self.assertEqual(self.run_operation(), 1)
        self.assertEqual(self.native.active, self.native.original)
        self.assertTrue(self.state.exists())
        self.assertIn('probe_client', [e.get('category') for e in self.events()])

    def test_separate_restore_process_success_and_verified_mode(self):
        self.assertEqual(self.run_operation(), 0)
        self.assertEqual(self.run_operation('restore'), 0)
        self.assertEqual(self.native.active, self.native.original)
        self.assertEqual(self.events()[-1]['event'], 'restored')
        self.assertTrue(self.state.exists())  # durable even after immediate rollback

    def test_restore_error_and_mismatch_are_visible_failures(self):
        self.assertEqual(self.run_operation(), 0)
        self.native.result = -1
        self.assertEqual(self.run_operation('restore'), 1)
        self.native.result = 0; self.native.wrong_apply = True
        self.assertEqual(self.run_operation('restore'), 1)
        categories = [e.get('category') for e in self.events()]
        self.assertIn('restore_native', categories)
        self.assertIn('restore_mismatch', categories)

    def test_no_state_is_explicit_noop_after_guard_without_backend(self):
        self.assertEqual(self.run_operation('restore'), 0)
        self.factory.assert_not_called()
        self.assertEqual(self.events()[-1]['event'], 'restore_no_state')

    def test_malformed_state_denied_without_backend_or_change(self):
        for content in ('{}', 'not json', '[]', '{"version":true}'):
            self.state.write_text(content)
            self.assertEqual(self.run_operation('restore'), 1)
        self.factory.assert_not_called()

    def test_state_integrity_and_layout_fail_closed(self):
        self.assertEqual(self.run_operation(), 0)
        valid = json.loads(self.state.read_text())
        for field, value in [('sha256', '0'*64), ('version', 2), ('mode', 'bad'),
                             ('mode', base64.b64encode(b'bad').decode())]:
            data = {**valid, field: value}; self.state.write_text(json.dumps(data))
            self.factory.reset_mock()
            self.assertEqual(self.run_operation('restore'), 1)
            self.factory.assert_not_called()

    def test_existing_state_never_overwritten_or_new_change_attempted(self):
        self.assertEqual(self.run_operation(), 0)
        frozen = self.state.read_bytes(); self.factory.reset_mock()
        self.assertEqual(self.run_operation(), 1)
        self.assertEqual(self.state.read_bytes(), frozen)
        self.factory.assert_not_called()

    def test_state_write_failure_prevents_mutation(self):
        with patch.object(display, 'save_state', side_effect=OSError('SECRET /private/path')):
            self.assertEqual(self.run_operation(), 1)
        self.assertEqual(self.native.calls, ['enumerate'])
        self.assertNotIn('SECRET', self.log.read_text())

    def test_unexpected_exception_is_sanitized_and_always_restore_remains_possible(self):
        self.native.measure = Mock(side_effect=RuntimeError('SECRET /private/path'))
        self.assertEqual(self.run_operation(), 1)
        self.assertNotIn('SECRET', self.log.read_text())
        self.assertEqual(self.run_operation('restore'), 0)

    def test_nonzero_driver_private_data_denied_before_change(self):
        raw = display.DEVMODEW.from_buffer_copy(mode()); raw.dmDriverExtra = 4
        self.native.original = self.native.active = bytes(raw)
        self.assertEqual(self.run_operation(), 1)
        self.assertFalse(self.state.exists())


    def test_duplicate_state_keys_are_malformed_even_with_valid_last_values(self):
        self.assertEqual(self.run_operation(), 0)
        original = self.state.read_text()
        self.state.write_text(original.replace('"version": 1', '"version": 99, "version": 1'))
        self.factory.reset_mock()
        self.assertEqual(self.run_operation('restore'), 1)
        self.factory.assert_not_called()

    def test_original_full_bytes_are_restored_but_device_names_never_logged(self):
        raw = display.DEVMODEW.from_buffer_copy(self.native.original)
        for index, letter in enumerate('SECRET'):
            raw.dmDeviceName[index] = ord(letter)
            raw.dmFormName[index] = ord(letter)
        raw.dmSpecVersion, raw.dmDriverVersion = 0x401, 0x4321
        raw.dmReserved1, raw.dmICMMethod = 1234, 7
        self.native.original = self.native.active = bytes(raw)
        self.assertEqual(self.run_operation(), 0)
        self.assertEqual(self.run_operation('restore'), 0)
        self.assertEqual(self.native.calls[-1], ('change', bytes(raw)))
        self.assertNotIn('SECRET', self.log.read_text())

    def test_mutation_then_exception_rolls_back_without_losing_original_failure(self):
        change = self.native.change
        def mutate_then_fail(raw):
            result = change(raw)
            if raw != self.native.original:
                raise RuntimeError('SECRET')
            return result
        self.native.change = mutate_then_fail
        self.assertEqual(self.run_operation(), 1)
        self.assertEqual(self.native.active, self.native.original)
        self.assertTrue(self.state.exists())
        self.assertNotIn('SECRET', self.log.read_text())


class RestoreLoggingTests(unittest.TestCase):
    """Artificial restoration failures; no real Win32 API is loaded."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'state.json'
        self.log = Path(self.tmp.name) / 'metadata.jsonl'
        self.native = ArtificialNative()
        # Include non-summary bytes to detect accidental mode reconstruction.
        raw = display.DEVMODEW.from_buffer_copy(self.native.original)
        raw.dmReserved1, raw.dmDriverVersion = 12345, 4321
        self.native.original = self.native.active = bytes(raw)
        self.factory = Mock(return_value=self.native)
        self.stdout, self.stderr = io.StringIO(), io.StringIO()

    def execute(self, operation):
        with patch('sys.stdout', self.stdout), patch('sys.stderr', self.stderr):
            return display.execute(operation, self.state, self.log, platform='win32',
                                   environment=dict(CI), backend_factory=self.factory)

    def prepare(self):
        self.assertEqual(self.execute('prepare'), 0)
        self.frozen = self.state.read_bytes()
        self.native.calls.clear()
        self.stdout.seek(0); self.stdout.truncate()

    def assert_preserved(self):
        self.assertEqual(self.state.read_bytes(), self.frozen)
        self.assertEqual(self.native.calls[-1], ('change', self.native.original))

    def assert_safe(self):
        text = self.stderr.getvalue() + self.log.read_text()
        self.assertNotIn('SECRET', text)
        self.assertNotIn(self.tmp.name, text)

    def test_separate_restore_metadata_failure_still_changes_and_verifies(self):
        self.prepare()
        self.log.unlink(); self.log.mkdir()
        current = Mock(wraps=self.native.current)
        self.native.current = current
        self.assertEqual(self.execute('restore'), 1)
        self.assertEqual(self.native.active, self.native.original)
        current.assert_called_once_with()
        self.assert_preserved()
        self.assertIn('metadata_io', self.stderr.getvalue() + self.stdout.getvalue())

    def test_every_restore_emit_failure_is_deferred_until_native_verification(self):
        self.prepare()
        emit = display.Reporter.emit
        for event in ('restore_attempt', 'restore_native', 'restore_actual', 'restored'):
            for error in (OSError('SECRET'), RuntimeError('SECRET')):
                with self.subTest(event=event, error=type(error).__name__):
                    self.native.active = mode(1920, 1080)
                    current = Mock(return_value=self.native.original)
                    self.native.current = current
                    def broken(report, name, **values):
                        if name == event:
                            raise error
                        return emit(report, name, **values)
                    with patch.object(display.Reporter, 'emit', broken):
                        self.assertEqual(self.execute('restore'), 1)
                    self.assertEqual(self.native.active, self.native.original)
                    current.assert_called_once_with()
                    self.assert_preserved()
        self.assert_safe()

    def test_rollback_after_apply_metadata_failure_still_changes_and_verifies(self):
        original_change = self.native.change
        def change(raw):
            result = original_change(raw)
            if raw != self.native.original:
                self.frozen = self.state.read_bytes()
                self.log.unlink(); self.log.mkdir()
            return result
        self.native.change = change
        current = Mock(wraps=self.native.current)
        self.native.current = current
        self.assertEqual(self.execute('prepare'), 1)
        self.assertEqual(self.native.active, self.native.original)
        self.assertEqual(current.call_count, 3)  # before, applied, restored
        self.assert_preserved()

    def test_broken_failure_diagnostics_never_block_rollback_or_original_error(self):
        self.native.measure_error = True
        emit = display.Reporter.emit
        def broken(report, event, **values):
            if event == 'failure' or event.startswith('rollback') or event == 'rolled_back':
                raise RuntimeError('SECRET')
            return emit(report, event, **values)
        with patch.object(display.Reporter, 'emit', broken):
            self.assertEqual(self.execute('prepare'), 1)
        self.frozen = self.state.read_bytes()
        self.assertEqual(self.native.active, self.native.original)
        self.assert_preserved()
        self.assertIn('probe_client', self.stderr.getvalue())
        self.assert_safe()

    def test_stdout_failure_still_writes_metadata_and_restores(self):
        self.prepare()
        self.stdout = Mock(write=Mock(side_effect=BrokenPipeError('SECRET')))
        self.assertEqual(self.execute('restore'), 1)
        self.assertEqual(self.native.active, self.native.original)
        self.assert_preserved()
        events = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertIn('restored', [event['event'] for event in events])
        self.assertIn('metadata_io', [event.get('category') for event in events])
        self.assert_safe()

    def test_stdout_failure_after_apply_cannot_block_rollback(self):
        self.stdout = Mock(wraps=io.StringIO())
        original_change = self.native.change
        def change(raw):
            result = original_change(raw)
            if raw != self.native.original:
                self.frozen = self.state.read_bytes()
                self.stdout.write.side_effect = BrokenPipeError('SECRET')
            return result
        self.native.change = change
        self.assertEqual(self.execute('prepare'), 1)
        self.assertEqual(self.native.active, self.native.original)
        self.assert_preserved()
        self.assertIn('rolled_back', self.log.read_text())
        self.assert_safe()

    def test_native_restore_errors_take_priority_and_driver_exceptions_propagate(self):
        for failure in ('code', 'mismatch', 'change_exception', 'current_exception'):
            with self.subTest(failure=failure):
                native = ArtificialNative()
                native.active = mode(1920, 1080)
                error = RuntimeError('SECRET')
                if failure == 'code': native.result = -2
                if failure == 'mismatch': native.wrong_apply = True
                if failure == 'change_exception': native.change = Mock(side_effect=error)
                if failure == 'current_exception': native.current = Mock(side_effect=error)
                report = Mock(emit=Mock(side_effect=OSError('SECRET')))
                with patch('sys.stderr', self.stderr), self.assertRaises(Exception) as caught:
                    display.restore_mode(native, native.original, report)
                if failure.endswith('exception'):
                    self.assertIs(caught.exception, error)
                else:
                    self.assertIsInstance(caught.exception, display.DisplayError)
                    self.assertEqual(caught.exception.category,
                                     'restore_native' if failure == 'code' else 'restore_mismatch')
                    self.assertEqual(caught.exception.code, -2 if failure == 'code' else None)

    def test_command_native_failures_remain_visible_with_both_logging_sinks_broken(self):
        self.prepare()
        for failure, category in (('code', 'restore_native'), ('mismatch', 'restore_mismatch'),
                                  ('change_exception', 'unexpected_failure'),
                                  ('current_exception', 'io_failure')):
            with self.subTest(failure=failure):
                self.native = ArtificialNative()
                self.native.active = mode(1920, 1080)
                self.factory.return_value = self.native
                if failure == 'code': self.native.result = -2
                if failure == 'mismatch': self.native.wrong_apply = True
                if failure == 'change_exception':
                    self.native.change = Mock(side_effect=RuntimeError('SECRET'))
                if failure == 'current_exception':
                    self.native.current = Mock(side_effect=OSError('SECRET'))
                self.stdout = Mock(write=Mock(side_effect=BrokenPipeError('SECRET')))
                self.stderr.seek(0); self.stderr.truncate()
                # State validation remains real and precedes reporting failure.
                real_open = Path.open
                def metadata_broken(path, *args, **kwargs):
                    if path == self.log:
                        raise OSError('SECRET')
                    return real_open(path, *args, **kwargs)
                with patch.object(Path, 'open', metadata_broken):
                    self.assertEqual(self.execute('restore'), 1)
                diagnostic = [json.loads(line) for line in self.stderr.getvalue().splitlines()
                              if json.loads(line)['event'] == 'failure']
                self.assertEqual(diagnostic[-1]['category'], category)
                self.assertEqual(diagnostic[-1]['code'], -2 if failure == 'code' else None)
                self.assertEqual(self.state.read_bytes(), self.frozen)
                self.assert_safe()

    def test_apply_native_failure_or_mismatch_is_not_replaced_by_logging_failure(self):
        emit = display.Reporter.emit
        for failure, category in (('code', 'apply_native'), ('mismatch', 'apply_mismatch')):
            with self.subTest(failure=failure):
                if self.state.exists(): self.state.unlink()
                self.native = ArtificialNative()
                self.factory.return_value = self.native
                self.native.result = -2 if failure == 'code' else 0
                self.native.wrong_apply = failure == 'mismatch'
                captured = []
                def broken(report, event, **values):
                    captured.append({'event': event, **values})
                    if event in ('apply', 'after', 'failure') or event.startswith('rollback'):
                        raise OSError('SECRET')
                    return emit(report, event, **values)
                with patch.object(display.Reporter, 'emit', broken):
                    self.assertEqual(self.execute('prepare'), 1)
                failures = [event for event in captured if event['event'] == 'failure']
                self.assertEqual(failures[0]['category'], category)
                self.assertEqual(failures[0]['code'], -2 if failure == 'code' else None)
                changes = [call[1] for call in self.native.calls if isinstance(call, tuple)]
                self.assertEqual(changes[-1], self.native.original)
                if failure == 'code':
                    rollback = [event for event in captured if event['event'] == 'rollback_failed']
                    self.assertEqual(rollback[-1].get('category'), 'restore_native')
                    self.assertEqual(rollback[-1].get('code'), -2)

    def test_unavailable_diagnostics_still_fail_closed_before_backend(self):
        self.stdout = Mock(write=Mock(side_effect=BrokenPipeError('SECRET')))
        self.stderr = Mock(write=Mock(side_effect=RuntimeError('SECRET')))
        with patch.object(display.Reporter, 'emit', side_effect=RuntimeError('SECRET')):
            with patch.dict(CI, {'RUNNER_ENVIRONMENT': 'self-hosted'}):
                self.assertEqual(self.execute('restore'), 1)
            self.state.write_text('{}')
            self.assertEqual(self.execute('restore'), 1)
        self.factory.assert_not_called()

    def test_unavailable_diagnostics_cannot_interrupt_rollback_after_apply(self):
        self.stdout = Mock(wraps=io.StringIO())
        self.stderr = Mock(write=Mock(side_effect=RuntimeError('SECRET')))
        original_change = self.native.change
        def change(raw):
            result = original_change(raw)
            if raw != self.native.original:
                self.frozen = self.state.read_bytes()
                self.stdout.write.side_effect = BrokenPipeError('SECRET')
                self.log.unlink(); self.log.mkdir()
            return result
        self.native.change = change
        self.assertEqual(self.execute('prepare'), 1)
        self.assertEqual(self.native.active, self.native.original)
        self.assert_preserved()

    def test_rollback_native_failures_keep_original_error_when_both_sinks_fail(self):
        emit, real_open = display.Reporter.emit, Path.open
        for failure, category in (('code', 'restore_native'), ('mismatch', 'restore_mismatch'),
                                  ('change_exception', 'unexpected_failure'),
                                  ('current_exception', 'io_failure')):
            with self.subTest(failure=failure):
                if self.state.exists(): self.state.unlink()
                self.native = ArtificialNative()
                self.factory.return_value = self.native
                self.stdout = Mock(wraps=io.StringIO())
                reporting_broken, captured = False, []
                change = Mock(wraps=self.native.change)
                self.native.change = change
                def measure():
                    nonlocal reporting_broken
                    self.frozen = self.state.read_bytes()
                    reporting_broken = True
                    self.stdout.write.side_effect = BrokenPipeError('SECRET')
                    if failure == 'code': self.native.result = -2
                    if failure == 'mismatch': self.native.wrong_apply = True
                    if failure == 'change_exception':
                        change.side_effect = RuntimeError('SECRET')
                    if failure == 'current_exception':
                        self.native.current = Mock(side_effect=OSError('SECRET'))
                    raise display.DisplayError('probe_client')
                self.native.measure = measure
                def metadata_broken(path, *args, **kwargs):
                    if path == self.log and reporting_broken:
                        raise OSError('SECRET')
                    return real_open(path, *args, **kwargs)
                def capture(report, event, **values):
                    captured.append({'event': event, **values})
                    return emit(report, event, **values)
                with patch.object(Path, 'open', metadata_broken), \
                        patch.object(display.Reporter, 'emit', capture):
                    self.assertEqual(self.execute('prepare'), 1)
                self.assertEqual(change.call_count, 2)
                self.assertEqual(change.call_args.args, (self.native.original,))
                self.assertEqual(self.state.read_bytes(), self.frozen)
                primary = [event for event in captured if event['event'] == 'failure']
                rollback = [event for event in captured if event['event'] == 'rollback_failed']
                self.assertEqual(primary[-1]['category'], 'probe_client')
                self.assertEqual(rollback[-1]['category'], category)
                self.assertEqual(rollback[-1]['code'], -2 if failure == 'code' else None)
                self.assert_safe()


class ArtificialFunction:
    def __init__(self, call):
        self.call = call
        self.argtypes = self.restype = None

    def __call__(self, *args):
        return self.call(*args)


class ArtificialUser32:
    """No native calls. ctypes writes exercise the real helper ABI boundary."""
    def __init__(self):
        self.calls, self.fail = [], None
        self.active = mode()
        self.handle = 0x123400005678
        self.windows = {}
        for name in ('EnumDisplaySettingsW', 'ChangeDisplaySettingsExW',
                     'SystemParametersInfoW', 'GetSystemMetrics', 'AdjustWindowRectEx',
                     'CreateWindowExW', 'GetClientRect', 'GetWindowRect',
                     'ClientToScreen', 'DestroyWindow'):
            setattr(self, name, ArtificialFunction(lambda *args, name=name: self.invoke(name, args)))

    def invoke(self, name, args):
        self.calls.append((name, args))
        if name == self.fail:
            return 0
        if name == 'EnumDisplaySettingsW':
            index = args[1]
            raw = self.active if index == 0xffffffff else (mode(1920, 1080) if index == 0 else None)
            if raw is None: return 0
            C.memmove(args[2], raw, len(raw)); return 1
        if name == 'ChangeDisplaySettingsExW':
            self.active = C.string_at(args[1], C.sizeof(display.DEVMODEW)); return 0
        if name == 'SystemParametersInfoW':
            C.cast(args[2], C.POINTER(display.RECT))[0] = display.RECT(0, 0, 1920, 1040)
            return 1
        if name == 'GetSystemMetrics':
            return {0: 1920, 1: 1080, 76: 0, 77: 0, 78: 1920, 79: 1080,
                    32: 8, 33: 8, 4: 23, 92: 4}[args[0]]
        if name == 'AdjustWindowRectEx':
            rect = C.cast(args[0], C.POINTER(display.RECT)).contents
            rect.left -= 8; rect.top -= 31; rect.right += 8; rect.bottom += 8
            return 1
        if name == 'CreateWindowExW':
            self.windows[self.handle] = args[4:8]
            return self.handle
        if name in ('GetClientRect', 'GetWindowRect'):
            x, y, width, height = self.windows[args[0]]
            rect = (display.RECT(0, 0, width-16, height-39) if name == 'GetClientRect'
                    else display.RECT(x, y, x+width, y+height))
            C.cast(args[1], C.POINTER(display.RECT))[0] = rect
            return 1
        if name == 'ClientToScreen':
            x, y, _, _ = self.windows[args[0]]
            C.cast(args[1], C.POINTER(display.POINTL))[0] = display.POINTL(x+8, y+31)
            return 1
        if name == 'DestroyWindow':
            del self.windows[args[0]]; return 1
        raise AssertionError(name)


def observed_mode188(width=1024, height=768, *, current=False):
    """Artificial bytes mirroring numeric facts from readonly run 40375330168."""
    value = display.DEVMODEW()
    value.dmSize = 188
    value.dmSpecVersion = value.dmDriverVersion = 1025
    value.dmFields = 544997536 if current else 8126592
    value.dmPelsWidth, value.dmPelsHeight = width, height
    value.dmBitsPerPel, value.dmDisplayFrequency = 32, 64
    return bytes(value)


class ArtificialMode188User32(ArtificialUser32):
    """Artificial public-188 API responses; never calls Windows."""
    def __init__(self):
        super().__init__()
        self.active = observed_mode188(current=True)
        self.supported = [observed_mode188(w, h) for w, h in (
            (640, 480), (800, 600), (1024, 768), (1152, 864), (1280, 720),
            (1280, 768), (1280, 1024), (1366, 768), (1440, 900), (1600, 900),
            (1600, 1200), (1680, 1050), (1920, 1080))]
        self.enum_buffers, self.changes = [], []

    def invoke(self, name, args):
        if name == 'EnumDisplaySettingsW':
            self.calls.append((name, args))
            value = C.cast(args[2], C.POINTER(display.DEVMODEW)).contents
            self.enum_buffers.append((C.sizeof(value), value.dmSize, value.dmDriverExtra))
            index = args[1]
            raw = (self.active if index == 0xffffffff else
                   self.supported[index] if index < len(self.supported) else None)
            if raw is None:
                return 0
            # Leave the already-zero allocated tail outside the public header untouched.
            C.memmove(args[2], raw, 188)
            return 1
        if name == 'ChangeDisplaySettingsExW':
            self.changes.append(C.string_at(args[1], C.sizeof(display.DEVMODEW)))
        return super().invoke(name, args)


class PublicMode188Tests(unittest.TestCase):
    """Regression fixtures only; real Windows apply/restore remains unverified."""

    def test_observed_public188_and_existing220_decode_without_rewriting(self):
        self.assertEqual(display.DEVMODEW.dmDisplayFrequency.offset + 4, 188)
        self.assertEqual(C.sizeof(display.DEVMODEW), 220)
        for raw in (observed_mode188(current=True), observed_mode188(1920, 1080), mode()):
            with self.subTest(header=int.from_bytes(raw[68:70], 'little')):
                value = display.decode_mode(raw)
                self.assertEqual(bytes(value), raw)
                self.assertEqual(value.dmDriverExtra, 0)
                data = display.summary(raw)
                self.assertEqual(data['size'], [value.dmPelsWidth, value.dmPelsHeight])
                self.assertEqual(data['fields'], value.dmFields)
                self.assertEqual(data['bpp'], 32)
                self.assertEqual(data['frequency'], 64 if value.dmSize == 188 else 60)
                self.assertEqual(data['orientation'], 0)
                self.assertEqual(data['fixed_output'], 0)
                self.assertEqual(data['panning'], None if value.dmSize == 188 else [0, 0])

    def test_public188_current_and_enumeration_keep_real_target_and_allocated220(self):
        api = ArtificialMode188User32()
        native = display.NativeBackend(platform='win32', environment=dict(CI), user32=api)
        self.assertEqual(native.current(), observed_mode188(current=True))
        self.assertEqual(native.enumerate(), api.supported)
        self.assertEqual(len(api.supported), 13)
        self.assertEqual(api.supported[-1], observed_mode188(1920, 1080))
        self.assertEqual(api.enum_buffers, [(220, 220, 0)] * 15)
        self.assertEqual(api.changes, [])

    def test_public188_prepare_selection_state_and_cross_process_restore_exact(self):
        api = ArtificialMode188User32()
        original = display.DEVMODEW.from_buffer_copy(api.active)
        original.dmDeviceName[0], original.dmFormName[0], original.dmColor = 88, 89, 7
        api.active = bytes(original)
        target = api.supported[-1]
        alternate = display.DEVMODEW.from_buffer_copy(target)
        alternate.dmDisplayFrequency = 60
        api.supported.insert(0, bytes(alternate))
        alternate.dmDisplayFrequency, alternate.dmBitsPerPel = 64, 24
        api.supported.insert(0, bytes(alternate))
        native = display.NativeBackend(platform='win32', environment=dict(CI), user32=api)
        with tempfile.TemporaryDirectory() as tmp:
            state, log = Path(tmp) / 'state.json', Path(tmp) / 'metadata.jsonl'
            change = api.ChangeDisplaySettingsExW.call
            def inspect_state(*args):
                self.assertEqual(display.load_state(state), bytes(original))
                return change(*args)
            api.ChangeDisplaySettingsExW.call = inspect_state
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(display.execute('prepare', state, log, platform='win32',
                    environment=dict(CI), backend_factory=lambda: native), 0)
            self.assertEqual(api.changes, [target])
            self.assertEqual(api.active, target)
            frozen = state.read_bytes()
            data = json.loads(frozen)
            self.assertEqual(base64.b64decode(data['mode']), bytes(original))
            self.assertEqual(data['sha256'], hashlib.sha256(bytes(original)).hexdigest())
            # New interpreter and new artificial API: only the durable state crosses processes.
            child = '''
import contextlib, io, json, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_ci_display import ArtificialMode188User32, CI, observed_mode188
from windows_build import ci_display as display
api = ArtificialMode188User32()
api.active = observed_mode188(1920, 1080)
native = display.NativeBackend(platform='win32', environment=dict(CI), user32=api)
with contextlib.redirect_stdout(io.StringIO()):
    code = display.execute('restore', Path(sys.argv[1]), Path(sys.argv[2]),
        platform='win32', environment=dict(CI), backend_factory=lambda: native)
print(json.dumps({'code': code, 'active': api.active.hex(),
                  'changes': [raw.hex() for raw in api.changes]}))
sys.exit(code)
'''
            result = subprocess.run([sys.executable, '-B', '-c', child, str(state), str(log)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {
                'code': 0, 'active': bytes(original).hex(), 'changes': [bytes(original).hex()]})
            self.assertEqual(state.read_bytes(), frozen)
            self.assertEqual(display.load_state(state), bytes(original))
            self.assertEqual(json.loads(log.read_text().splitlines()[-1])['event'], 'restored')

    def test_unknown_public_sizes_and_malformed_buffers_remain_rejected(self):
        for size in (0, 68, 172, 176, 180, 184, 187, 189, 192, 212, 216, 219, 221, 65535):
            value = display.DEVMODEW.from_buffer_copy(mode())
            value.dmSize = size
            with self.subTest(size=size), self.assertRaisesRegex(display.DisplayError, 'mode_layout'):
                display.decode_mode(bytes(value))
        for raw in (None, '', bytearray(mode()), b'', observed_mode188()[:188],
                    mode()[:-1], mode() + b'\0'):
            with self.subTest(type=type(raw), length=len(raw) if raw is not None else None), \
                    self.assertRaisesRegex(display.DisplayError, 'mode_layout'):
                display.decode_mode(raw)

    def test_private_extra_required_fields_and_bounded_dimensions_stay_strict(self):
        for raw in (observed_mode188(), mode()):
            for field, invalid in (('dmDriverExtra', 1), ('dmDriverExtra', 32),
                    ('dmDriverExtra', 65535), ('dmFields', 0), ('dmFields', 0x80000),
                    ('dmFields', 0x100000), ('dmPelsWidth', 0), ('dmPelsHeight', 0),
                    ('dmPelsWidth', 32769), ('dmPelsHeight', 32769),
                    ('dmPelsWidth', 0xffffffff), ('dmPelsHeight', 0xffffffff)):
                value = display.DEVMODEW.from_buffer_copy(raw)
                setattr(value, field, invalid)
                with self.subTest(size=value.dmSize, field=field, invalid=invalid), \
                        self.assertRaisesRegex(display.DisplayError, 'mode_layout'):
                    display.decode_mode(bytes(value))
            for bound in (1, 32768):
                value = display.DEVMODEW.from_buffer_copy(raw)
                value.dmPelsWidth = value.dmPelsHeight = bound
                self.assertEqual(bytes(display.decode_mode(bytes(value))), bytes(value))

    def test_public188_undeclared_tail_is_zero_only_and_never_modern_panning(self):
        for offset in range(188, 220):
            raw = bytearray(observed_mode188())
            raw[offset] = 1
            with self.subTest(offset=offset), self.assertRaisesRegex(display.DisplayError, 'mode_layout'):
                display.decode_mode(bytes(raw))
        modern = display.DEVMODEW.from_buffer_copy(mode())
        modern.dmICMMethod, modern.dmReserved1 = 7, 1234
        modern.dmPanningWidth, modern.dmPanningHeight = 2048, 1200
        self.assertEqual(bytes(display.decode_mode(bytes(modern))), bytes(modern))
        self.assertEqual(display.summary(bytes(modern))['panning'], [2048, 1200])
        self.assertIsNone(display.summary(observed_mode188())['panning'])


class ArtificialAbiTests(unittest.TestCase):
    def backend(self, api=None):
        api = ArtificialUser32() if api is None else api
        return display.NativeBackend(platform='win32', environment=dict(CI), user32=api), api

    def test_fixed_width_structure_offsets_and_full_union(self):
        self.assertEqual(C.sizeof(display.DEVMODEW), 220)
        self.assertEqual(C.sizeof(display.RECT), 16)
        self.assertEqual(C.sizeof(display.POINTL), 8)
        self.assertEqual(C.sizeof(display.ModeUnion), 16)
        for name, offset in {'dmSize': 68, 'dmFields': 72, 'dmPosition': 76,
                             'dmDisplayOrientation': 84, 'dmColor': 92,
                             'dmFormName': 102, 'dmLogPixels': 166,
                             'dmBitsPerPel': 168, 'dmPelsWidth': 172,
                             'dmPelsHeight': 176, 'dmDisplayFrequency': 184,
                             'dmPanningHeight': 216}.items():
            self.assertEqual(getattr(display.DEVMODEW, name).offset, offset)
        self.assertEqual(C.sizeof(display.HWND), C.sizeof(C.c_void_p))
        self.assertEqual(C.sizeof(display.LPARAM), C.sizeof(C.c_void_p))
        for field in (display.LONG, display.DWORD, display.BOOL):
            self.assertEqual(C.sizeof(field), 4)

    def test_every_invoked_signature_is_explicit_and_handles_are_pointer_sized(self):
        native, api = self.backend()
        ptr, i32, u32 = C.c_void_p, C.c_int32, C.c_uint32
        expected = {
            'EnumDisplaySettingsW': ([C.c_wchar_p, u32, C.POINTER(display.DEVMODEW)], i32),
            'ChangeDisplaySettingsExW': ([C.c_wchar_p, C.POINTER(display.DEVMODEW), ptr, u32, ptr], i32),
            'SystemParametersInfoW': ([u32, u32, ptr, u32], i32),
            'GetSystemMetrics': ([i32], i32),
            'AdjustWindowRectEx': ([C.POINTER(display.RECT), u32, i32, u32], i32),
            'CreateWindowExW': ([u32, C.c_wchar_p, C.c_wchar_p, u32, i32, i32, i32, i32,
                                 ptr, ptr, ptr, ptr], ptr),
            'GetClientRect': ([ptr, C.POINTER(display.RECT)], i32),
            'GetWindowRect': ([ptr, C.POINTER(display.RECT)], i32),
            'ClientToScreen': ([ptr, C.POINTER(display.POINTL)], i32),
            'DestroyWindow': ([ptr], i32)}
        for name, (args, result) in expected.items():
            with self.subTest(api=name):
                self.assertEqual(getattr(api, name).argtypes, args)
                self.assertIs(getattr(api, name).restype, result)
        native.change(native.enumerate()[0])
        data = native.measure(); display.validate_measurements(data)
        self.assertFalse(api.windows)
        for name, args in api.calls:
            if name == 'ChangeDisplaySettingsExW': self.assertEqual(args[3], 0)
            if name in ('GetClientRect', 'GetWindowRect', 'ClientToScreen', 'DestroyWindow'):
                self.assertEqual(args[0], api.handle)

    def test_native_boundary_guard_precedes_api_binding_on_every_host(self):
        api = Mock()
        for env, platform in (({}, 'win32'), (CI, 'linux'),
                              ({**CI, 'RUNNER_ENVIRONMENT': 'self-hosted'}, 'win32')):
            with self.assertRaises(display.DisplayError):
                display.NativeBackend(platform=platform, environment=env, user32=api)
        self.assertFalse(api.mock_calls)

    def test_fake_platform_cannot_load_linux_native_backend(self):
        with patch.object(display.sys, 'platform', 'linux'), \
                patch.object(display.C, 'WinDLL', create=True) as loader:
            with self.assertRaisesRegex(display.DisplayError, 'native_platform_denied'):
                display.NativeBackend(platform='win32', environment=dict(CI))
        loader.assert_not_called()

    def test_probe_failure_cleanup_for_every_native_measurement_api(self):
        for failure in ('SystemParametersInfoW', 'AdjustWindowRectEx', 'CreateWindowExW',
                        'GetClientRect', 'GetWindowRect', 'ClientToScreen'):
            native, api = self.backend(); api.fail = failure
            with self.subTest(failure=failure), self.assertRaises(display.DisplayError):
                native.measure()
            self.assertFalse(api.windows)
            cleanup = [name for name, _ in api.calls if name == 'DestroyWindow']
            self.assertEqual(len(cleanup), int(failure in ('GetClientRect', 'GetWindowRect', 'ClientToScreen')))

    def test_failed_probe_keeps_safe_workarea_and_completed_probe_measurements(self):
        native, api = self.backend(); api.fail = 'GetClientRect'
        with self.assertRaises(display.DisplayError) as caught:
            native.measure()
        self.assertEqual(caught.exception.measurements['workarea'], [0, 0, 1920, 1040])
        self.assertEqual(caught.exception.measurements['probes'], [])
        self.assertFalse(api.windows)

    def test_probe_destroy_failure_is_visible(self):
        native, api = self.backend(); api.fail = 'DestroyWindow'
        with self.assertRaisesRegex(display.DisplayError, 'probe_cleanup'):
            native.measure()

    def test_only_ephemeral_change_no_registry_or_dpi_api(self):
        native, api = self.backend()
        original = native.current()
        native.change(native.enumerate()[0]); native.change(original)
        changes = [args for name, args in api.calls if name == 'ChangeDisplaySettingsExW']
        self.assertEqual(len(changes), 2)
        self.assertTrue(all(args[3] == 0 for args in changes))
        source = (ROOT / 'windows_build/ci_display.py').read_text()
        for denied in ('winreg', 'CDS_UPDATEREGISTRY', 'SetProcessDPI', 'SetThreadDpi', 'SetProcessDpi'):
            self.assertNotIn(denied, source)

    def test_current_mode_failure_does_not_masquerade_as_enumeration_end(self):
        native, api = self.backend(); api.fail = 'EnumDisplaySettingsW'
        with self.assertRaisesRegex(display.DisplayError, 'current_mode'):
            native.current()
        self.assertEqual(native.enumerate(), [])


class NativeCleanupTests(unittest.TestCase):
    """Combined failures through artificial Win32 calls; Windows is not exercised."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'SECRET-state.json'
        self.log = Path(self.tmp.name) / 'metadata.jsonl'
        self.stdout, self.stderr = io.StringIO(), io.StringIO()
        self.api = ArtificialUser32()
        raw = display.DEVMODEW.from_buffer_copy(self.api.active)
        raw.dmReserved1, raw.dmDriverVersion = 12345, 4321
        for index, letter in enumerate('SECRET'):
            raw.dmDeviceName[index] = ord(letter)
        self.original = self.api.active = bytes(raw)
        self.native = display.NativeBackend(platform='win32', environment=dict(CI),
                                            user32=self.api)

    def execute(self):
        with patch('sys.stdout', self.stdout), patch('sys.stderr', self.stderr):
            return display.execute('prepare', self.state, self.log, platform='win32',
                                   environment=dict(CI), backend_factory=lambda: self.native)

    def assert_rollback_and_safe_logs(self):
        self.assertEqual(self.api.active, self.original)
        self.assertEqual(display.load_state(self.state), self.original)
        changes = [args for name, args in self.api.calls if name == 'ChangeDisplaySettingsExW']
        self.assertEqual(len(changes), 2)
        self.assertTrue(all(args[3] == 0 for args in changes))
        cleanup = [args for name, args in self.api.calls if name == 'DestroyWindow']
        self.assertEqual(cleanup, [(self.api.handle,)])
        text = self.stdout.getvalue() + self.stderr.getvalue() + self.log.read_text()
        for forbidden in ('SECRET', self.tmp.name, 'Traceback', '/private/path'):
            self.assertNotIn(forbidden, text)
        events = [json.loads(line) for line in text.splitlines()]
        self.assertNotIn('prepared', [event['event'] for event in events])
        self.assertIn('rolled_back', [event['event'] for event in events])
        return events

    def test_client_rect_false_and_destroy_false_preserve_primary(self):
        self.api.fail = 'DestroyWindow'
        self.api.GetClientRect.call = Mock(return_value=0)
        with self.assertRaises(display.DisplayError) as caught:
            self.native.measure()
        self.assertEqual(caught.exception.category, 'probe_client')
        self.assertEqual(caught.exception.cleanup_failure,
                         {'category': 'probe_cleanup', 'code': None})
        self.assertEqual(caught.exception.measurements['probes'], [])
        self.assertEqual([name for name, _ in self.api.calls].count('DestroyWindow'), 1)

    def test_other_measurement_false_and_destroy_false_preserve_primary(self):
        self.api.fail = 'DestroyWindow'
        self.api.GetWindowRect.call = Mock(return_value=0)
        with self.assertRaises(display.DisplayError) as caught:
            self.native.measure()
        self.assertEqual(caught.exception.category, 'probe_outer')
        self.assertEqual(caught.exception.cleanup_failure,
                         {'category': 'probe_cleanup', 'code': None})
        self.assertEqual([name for name, _ in self.api.calls].count('DestroyWindow'), 1)

    def test_execute_combined_false_results_keep_both_diagnostics_and_rollback(self):
        for method, category in (('GetClientRect', 'probe_client'),
                                 ('GetWindowRect', 'probe_outer'),
                                 ('ClientToScreen', 'probe_capture')):
            with self.subTest(method=method):
                if self.state.exists(): self.state.unlink()
                if self.log.exists(): self.log.unlink()
                self.stdout.seek(0); self.stdout.truncate()
                self.stderr.seek(0); self.stderr.truncate()
                self.api.active = self.original
                self.api.calls.clear()
                self.api.fail = 'DestroyWindow'
                with patch.object(getattr(self.api, method), 'call', return_value=0):
                    self.assertEqual(self.execute(), 1)
                events = [json.loads(line) for line in self.stdout.getvalue().splitlines()]
                self.assertEqual([e for e in events if e['event'] == 'failure'][-1],
                                 {'event': 'failure', 'category': category, 'code': None})
                self.assertEqual([e for e in events if e['event'] == 'cleanup_failed'][-1],
                                 {'event': 'cleanup_failed', 'category': 'probe_cleanup', 'code': None})
                self.assert_rollback_and_safe_logs()

    def test_cleanup_without_primary_remains_command_failure(self):
        self.api.fail = 'DestroyWindow'
        self.assertEqual(self.execute(), 1)
        events = self.assert_rollback_and_safe_logs()
        self.assertIn({'event': 'failure', 'category': 'probe_cleanup', 'code': None}, events)
        self.assertNotIn('cleanup_failed', [e['event'] for e in events])

    def test_reporter_failure_cannot_hide_combined_errors_or_block_rollback(self):
        self.api.fail = 'DestroyWindow'
        self.api.GetClientRect.call = Mock(return_value=0)
        emit = display.Reporter.emit
        def broken(report, event, **values):
            if event in ('measurements', 'failure', 'cleanup_failed') or event.startswith('rollback'):
                raise OSError('SECRET /private/path')
            return emit(report, event, **values)
        with patch.object(display.Reporter, 'emit', broken):
            self.assertEqual(self.execute(), 1)
        events = self.assert_rollback_and_safe_logs()
        self.assertIn({'event': 'failure', 'category': 'probe_client', 'code': None,
                       'metadata_error': True}, events)
        self.assertIn({'event': 'cleanup_failed', 'category': 'probe_cleanup', 'code': None,
                       'metadata_error': True}, events)

    def test_arbitrary_measurement_and_cleanup_exceptions_keep_identity_and_safe_details(self):
        primary = RuntimeError('SECRET /private/path')
        self.api.GetClientRect.call = Mock(side_effect=primary)
        self.api.DestroyWindow.call = Mock(side_effect=OSError('SECRET /private/path'))
        with self.assertRaises(RuntimeError) as caught:
            self.native.measure()
        self.assertIs(caught.exception, primary)
        self.assertEqual(primary.cleanup_failure, {'category': 'io_failure', 'code': None})
        self.api.DestroyWindow.call.assert_called_once_with(self.api.handle)
        def destroy_error(*args):
            self.api.calls.append(('DestroyWindow', args))
            raise OSError('SECRET /private/path')
        self.api.DestroyWindow.call = destroy_error
        self.assertEqual(self.execute(), 1)
        events = self.assert_rollback_and_safe_logs()
        self.assertIn({'event': 'failure', 'category': 'unexpected_failure', 'code': None}, events)
        self.assertIn({'event': 'cleanup_failed', 'category': 'io_failure', 'code': None}, events)


def _read_utf8_lf_bytes(path):
    """Preserve UTF-8 text exactly except for checkout CRLF line endings."""
    return path.read_bytes().decode('utf-8').replace('\r\n', '\n').encode('utf-8')


class WorkflowContractTests(unittest.TestCase):
    def test_ci_prepare_restore_order_hosted_context_and_all_original_text(self):
        raw = _read_utf8_lf_bytes(ROOT / '.github/workflows/windows-portable.yml')
        blocks = raw.split(b'      - name: ')
        inserted = [block for block in blocks[1:] if block.startswith((
            b'Prepare disposable hosted Windows display', b'Always restore original hosted Windows display'))]
        self.assertEqual(len(inserted), 2)
        prepare, restore = (block.decode() for block in inserted)
        for block in (prepare, restore):
            self.assertIn('MENU_VIDEO_CI_RUNNER_ENVIRONMENT: ${{ runner.environment }}', block)
            self.assertIn('if ($LASTEXITCODE -ne 0)', block)
            self.assertIn('--state .ci-display-state.json --metadata ci-logs/display.jsonl', block)
        self.assertIn('if: always()', restore)
        workflow = raw.decode()
        self.assertLess(workflow.index('ci_display.py prepare'), workflow.index("discover('tests')"))
        for gate in ('tests/render_eof_matrix.py', 'python -m PyInstaller',
                     './windows_build/smoke_windows.ps1', 'pipeline.py audit-archive'):
            self.assertLess(workflow.index('ci_display.py prepare'), workflow.index(gate))
            self.assertLess(workflow.index(gate), workflow.index('ci_display.py restore'))
        self.assertLess(workflow.index('ci_display.py restore'), workflow.index('Upload portable candidate'))
        self.assertLess(workflow.index('ci_display.py restore'), workflow.index('Collect exact source-test'))
        unchanged = blocks[0] + b''.join(b'      - name: '+b for b in blocks[1:] if b not in inserted)
        # Approved neutral identity delta; all content/pins remain exact.
        # Independent action/runtime/download pins and display gates remain asserted.
        self.assertEqual(hashlib.sha256(unchanged).hexdigest(),
                         '5d4d837aae9fce1c4cbc1aa9398ec7a2051f5e5d44aa2dfc8eba57d34c8c2424')

    def test_helper_has_no_product_or_spec_import_or_packaging_path(self):
        for path in list(ROOT.glob('*.py')) + list((ROOT/'windows_build').glob('*.spec')):
            self.assertNotIn('ci_display', path.read_text(encoding='utf-8'), str(path))
        # Reviewed name-only spec delta; keep rejecting all other text changes.
        self.assertEqual(hashlib.sha256(_read_utf8_lf_bytes(ROOT/'windows_build/GeradorMenusVideo.spec')).hexdigest(),
                         '481b8517d1296af75c186533755309ae6018443952dafb032f1e769ca0a61ab6')


class ArtificialTextContractTests(unittest.TestCase):
    """Temporary checkouts and codec simulation; no native Windows execution."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.originals = {
            name: (ROOT / name).read_bytes().replace(b'\r\n', b'\n')
            for name in ('.github/workflows/windows-portable.yml',
                         'windows_build/GeradorMenusVideo.spec', 'gui_servicos.py')}
        self.contract = WorkflowContractTests()

    def checkout(self, newline):
        for name, raw in self.originals.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw.replace(b'\n', newline))
        return patch(__name__ + '.ROOT', self.root)

    def test_workflow_contract_accepts_lf_and_crlf(self):
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), self.checkout(newline):
                self.contract.test_ci_prepare_restore_order_hosted_context_and_all_original_text()

    def test_spec_contract_accepts_lf_and_crlf(self):
        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), self.checkout(newline):
                self.contract.test_helper_has_no_product_or_spec_import_or_packaging_path()

    def test_utf8_source_inspection_with_simulated_cp1252_default(self):
        read_text = Path.read_text

        def cp1252_read_text(path, encoding=None, errors=None):
            return read_text(path, encoding=encoding or 'cp1252', errors=errors)

        for newline in (b'\n', b'\r\n'):
            with self.subTest(newline=newline), self.checkout(newline), \
                    patch.object(Path, 'read_text', cp1252_read_text):
                source = self.root / 'gui_servicos.py'
                with self.assertRaises(UnicodeDecodeError):
                    source.read_text()
                self.contract.test_helper_has_no_product_or_spec_import_or_packaging_path()
                source.write_bytes(source.read_bytes() + b'\nimport ci_display\n')
                with self.assertRaises(AssertionError):
                    self.contract.test_helper_has_no_product_or_spec_import_or_packaging_path()

    def test_workflow_and_spec_contracts_reject_other_text_changes(self):
        for newline in (b'\n', b'\r\n'):
            for name, check in (
                    ('.github/workflows/windows-portable.yml',
                     self.contract.test_ci_prepare_restore_order_hosted_context_and_all_original_text),
                    ('windows_build/GeradorMenusVideo.spec',
                     self.contract.test_helper_has_no_product_or_spec_import_or_packaging_path)):
                for mutation in ('trailing_space', 'lone_cr'):
                    with self.subTest(newline=newline, name=name, mutation=mutation), \
                            self.checkout(newline):
                        path = self.root / name
                        raw = path.read_bytes()
                        changed = (raw + b' ' if mutation == 'trailing_space'
                                   else raw.replace(newline, b'\r', 1))
                        path.write_bytes(changed)
                        with self.assertRaises(AssertionError):
                            check()
