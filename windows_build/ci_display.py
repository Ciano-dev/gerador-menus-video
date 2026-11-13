"""Disposable github-hosted Windows CI display preparation; never a product import.

No registry persistence or DPI changes. Apply/restore use flags=0 only. DEVMODEW
uses fixed Win32 widths even when artificial ABI tests run on Linux. Reference:
https://learn.microsoft.com/windows/win32/api/wingdi/ns-wingdi-devmodew
https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-changedisplaysettingsexw
"""
import argparse
import base64
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import sys

DWORD = C.c_uint32
LONG = BOOL = C.c_int32
WORD = WCHAR = C.c_uint16
HANDLE = HWND = HINSTANCE = HMENU = C.c_void_p
LPARAM = C.c_ssize_t
LPCWSTR = C.c_wchar_p
CLIENT_SIZES = ((1024, 768), (1366, 768))
TARGET = [1920, 1080]
STYLE, EXSTYLE = 0x00cf0000, 0x00040100


class POINTL(C.Structure):
    _fields_ = [('x', LONG), ('y', LONG)]


class Printer(C.Structure):
    _fields_ = [(name, C.c_int16) for name in
                ('dmOrientation', 'dmPaperSize', 'dmPaperLength', 'dmPaperWidth',
                 'dmScale', 'dmCopies', 'dmDefaultSource', 'dmPrintQuality')]


class Display(C.Structure):
    _fields_ = [('dmPosition', POINTL), ('dmDisplayOrientation', DWORD),
                ('dmDisplayFixedOutput', DWORD)]


class ModeUnion(C.Union):
    _anonymous_ = ('printer', 'display')
    _fields_ = [('printer', Printer), ('display', Display)]


class FlagsUnion(C.Union):
    _fields_ = [('dmDisplayFlags', DWORD), ('dmNup', DWORD)]


class DEVMODEW(C.Structure):
    _anonymous_ = ('mode', 'flags')
    _fields_ = [('dmDeviceName', WCHAR * 32), ('dmSpecVersion', WORD),
                ('dmDriverVersion', WORD), ('dmSize', WORD), ('dmDriverExtra', WORD),
                ('dmFields', DWORD), ('mode', ModeUnion), ('dmColor', C.c_int16),
                ('dmDuplex', C.c_int16), ('dmYResolution', C.c_int16),
                ('dmTTOption', C.c_int16), ('dmCollate', C.c_int16),
                ('dmFormName', WCHAR * 32), ('dmLogPixels', WORD),
                ('dmBitsPerPel', DWORD), ('dmPelsWidth', DWORD), ('dmPelsHeight', DWORD),
                ('flags', FlagsUnion), ('dmDisplayFrequency', DWORD),
                ('dmICMMethod', DWORD), ('dmICMIntent', DWORD), ('dmMediaType', DWORD),
                ('dmDitherType', DWORD), ('dmReserved1', DWORD), ('dmReserved2', DWORD),
                ('dmPanningWidth', DWORD), ('dmPanningHeight', DWORD)]


class RECT(C.Structure):
    _fields_ = [('left', LONG), ('top', LONG), ('right', LONG), ('bottom', LONG)]


class DisplayError(Exception):
    def __init__(self, category, code=None):
        self.category, self.code = category, code
        self.measurements = None
        super().__init__(category)


def require_ci(platform, environment):
    expected = {'GITHUB_ACTIONS': 'true', 'RUNNER_OS': 'Windows',
                'RUNNER_ENVIRONMENT': 'github-hosted',
                'ANISIO_CI_RUNNER_ENVIRONMENT': 'github-hosted'}
    if platform != 'win32' or any(environment.get(k) != v for k, v in expected.items()):
        raise DisplayError('ci_guard_denied')


def decode_mode(raw):
    if not isinstance(raw, bytes) or len(raw) != C.sizeof(DEVMODEW):
        raise DisplayError('mode_layout')
    value = DEVMODEW.from_buffer_copy(raw)
    if (value.dmSize not in (188, 220) or value.dmDriverExtra != 0
            or value.dmFields & 0x180000 != 0x180000
            or not 0 < value.dmPelsWidth <= 32768 or not 0 < value.dmPelsHeight <= 32768):
        # Never discard driver-private data or silently truncate a nonstandard mode.
        raise DisplayError('mode_layout')
    # EnumDisplaySettingsW can return public dmSize=188 in our 220-byte buffer.
    # That header ends at dmDisplayFrequency. Reject nonzero undeclared tail
    # bytes conservatively; keep the entire buffer and its real header unchanged.
    if value.dmSize == 188 and any(raw[188:]):
        raise DisplayError('mode_layout')
    return value


def summary(raw):
    value = decode_mode(raw)
    return {'size': [value.dmPelsWidth, value.dmPelsHeight],
            'position': [value.dmPosition.x, value.dmPosition.y],
            'bpp': value.dmBitsPerPel, 'frequency': value.dmDisplayFrequency,
            'orientation': value.dmDisplayOrientation, 'fixed_output': value.dmDisplayFixedOutput,
            'flags': value.dmDisplayFlags, 'fields': value.dmFields,
            'panning': ([value.dmPanningWidth, value.dmPanningHeight]
                        if value.dmSize == 220 else None)}


class NativeBackend:
    def __init__(self, *, platform=None, environment=None, user32=None):
        require_ci(sys.platform if platform is None else platform,
                   os.environ if environment is None else environment)
        if user32 is None:
            # Injected platform is for artificial API doubles, never Linux native calls.
            if sys.platform != 'win32':
                raise DisplayError('native_platform_denied')
            user32 = C.WinDLL('user32', use_last_error=True)
        self.api = user32
        signatures = {
            'EnumDisplaySettingsW': ([LPCWSTR, DWORD, C.POINTER(DEVMODEW)], BOOL),
            'ChangeDisplaySettingsExW': ([LPCWSTR, C.POINTER(DEVMODEW), HWND, DWORD, C.c_void_p], LONG),
            'SystemParametersInfoW': ([DWORD, DWORD, C.c_void_p, DWORD], BOOL),
            'GetSystemMetrics': ([C.c_int32], C.c_int32),
            'AdjustWindowRectEx': ([C.POINTER(RECT), DWORD, BOOL, DWORD], BOOL),
            'CreateWindowExW': ([DWORD, LPCWSTR, LPCWSTR, DWORD, C.c_int32, C.c_int32,
                                 C.c_int32, C.c_int32, HWND, HMENU, HINSTANCE, C.c_void_p], HWND),
            'GetClientRect': ([HWND, C.POINTER(RECT)], BOOL),
            'GetWindowRect': ([HWND, C.POINTER(RECT)], BOOL),
            'ClientToScreen': ([HWND, C.POINTER(POINTL)], BOOL),
            'DestroyWindow': ([HWND], BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result

    def _enum(self, index):
        value = DEVMODEW()
        value.dmSize = C.sizeof(value)
        if not self.api.EnumDisplaySettingsW(None, index, C.byref(value)):
            return None
        raw = bytes(value)
        decode_mode(raw)
        return raw

    def current(self):
        raw = self._enum(0xffffffff)
        if raw is None:
            raise DisplayError('current_mode')
        return raw

    def enumerate(self):
        modes = []
        for index in range(4096):
            raw = self._enum(index)
            if raw is None:
                return modes
            modes.append(raw)
        raise DisplayError('enumeration_limit')

    def change(self, raw):
        value = decode_mode(raw)
        return int(self.api.ChangeDisplaySettingsExW(None, C.byref(value), None, 0, None))

    @staticmethod
    def _rect(value):
        return [value.left, value.top, value.right, value.bottom]

    def measure(self):
        self.measurements = None
        try:
            return self._measure()
        except DisplayError as exc:
            exc.measurements = self.measurements
            raise

    def _measure(self):
        api = self.api
        work = RECT()
        if not api.SystemParametersInfoW(48, 0, C.byref(work), 0):
            raise DisplayError('workarea_query')
        metric = api.GetSystemMetrics
        screen = [0, 0, metric(0), metric(1)]
        virtual = [metric(76), metric(77), metric(76)+metric(78), metric(77)+metric(79)]
        result = {'workarea': self._rect(work), 'screen': screen, 'virtual': virtual,
                  'metrics': [metric(i) for i in (32, 33, 4, 92)], 'probes': []}
        self.measurements = result
        for width, height in CLIENT_SIZES:
            rect = RECT(0, 0, width, height)
            if not api.AdjustWindowRectEx(C.byref(rect), STYLE, 0, EXSTYLE):
                raise DisplayError('probe_adjust')
            outer_width, outer_height = rect.right-rect.left, rect.bottom-rect.top
            window = api.CreateWindowExW(EXSTYLE, 'STATIC', 'CI display probe', STYLE,
                                         work.left, work.top, outer_width, outer_height,
                                         None, None, None, None)
            if not window:
                raise DisplayError('probe_create')
            primary = None
            try:
                client, outer, origin = RECT(), RECT(), POINTL()
                if not api.GetClientRect(window, C.byref(client)):
                    raise DisplayError('probe_client')
                if not api.GetWindowRect(window, C.byref(outer)):
                    raise DisplayError('probe_outer')
                if not api.ClientToScreen(window, C.byref(origin)):
                    raise DisplayError('probe_capture')
                result['probes'].append({'request': [width, height],
                    'adjusted': [outer_width, outer_height], 'client': self._rect(client),
                    'outer': self._rect(outer),
                    'capture': [origin.x, origin.y, origin.x+client.right, origin.y+client.bottom]})
            except Exception as exc:
                primary = exc
                raise
            finally:
                try:
                    if not api.DestroyWindow(window):
                        raise DisplayError('probe_cleanup')
                except Exception as cleanup_error:
                    if primary is None:
                        raise
                    # Keep the original exception; retain only safe cleanup fields.
                    primary.cleanup_failure = failure_details(cleanup_error)
        return result


def contains(outer, inner):
    return (outer[0] <= inner[0] < inner[2] <= outer[2]
            and outer[1] <= inner[1] < inner[3] <= outer[3])


def validate_measurements(data):
    """Sufficient criterion: exact clients, decorated outers and captures all fit.

    Each client is tested individually, like the sequential native source gates.
    No product geometry or scaling is adjusted to make this criterion pass.
    """
    work, screen, virtual = (data[k] for k in ('workarea', 'screen', 'virtual'))
    if screen != [0, 0, *TARGET] or not contains(screen, work) or not contains(virtual, screen):
        raise DisplayError('screen_bounds')
    if len(data['probes']) != len(CLIENT_SIZES):
        raise DisplayError('probe_count')
    for probe, (width, height) in zip(data['probes'], CLIENT_SIZES):
        outer, adjusted, capture = (probe[k] for k in ('outer', 'adjusted', 'capture'))
        if (probe['request'] != [width, height] or probe['client'] != [0, 0, width, height]
                or adjusted[0] <= width or adjusted[1] <= height
                or [outer[2]-outer[0], outer[3]-outer[1]] != adjusted
                or [capture[2]-capture[0], capture[3]-capture[1]] != [width, height]
                or not contains(work, outer) or not contains(outer, capture)
                or not contains(screen, capture) or not contains(virtual, capture)):
            raise DisplayError('client_bounds')


class Reporter:
    def __init__(self, path):
        self.path = Path(path)

    def emit(self, event, **values):
        line = json.dumps({'event': event, **values}, sort_keys=True)
        failure = None
        try:
            print(line, flush=True)
        except Exception as exc:
            failure = exc
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a', encoding='utf-8') as stream:
                stream.write(line+'\n'); stream.flush(); os.fsync(stream.fileno())
        except Exception as exc:
            if failure is None:
                failure = exc
        if failure is not None:
            raise failure


def emit_best_effort(report, event, **values):
    """Only reporting failures are deferred; driver operations stay outside here."""
    try:
        report.emit(event, **values)
        return True
    except Exception:
        # Use only our safe fields, never exception text or a destination path.
        # Even a broken fallback stream must not interrupt native restoration.
        try:
            print(json.dumps({'event': event, **values, 'metadata_error': True},
                             sort_keys=True), file=sys.stderr, flush=True)
        except Exception:
            pass
        return False


def failure_details(exc):
    # Never emit exception text, file paths, host names, environment or secrets.
    if isinstance(exc, DisplayError):
        return {'category': exc.category, 'code': exc.code}
    return {'category': 'io_failure' if isinstance(exc, OSError) else 'unexpected_failure',
            'code': None}


def save_state(path, raw):
    decode_mode(raw)
    data = {'version': 1, 'mode': base64.b64encode(raw).decode('ascii'),
            'sha256': hashlib.sha256(raw).hexdigest()}
    with path.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, sort_keys=True)
        stream.flush(); os.fsync(stream.fileno())


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def load_state(path):
    try:
        if path.stat().st_size > 4096:
            raise ValueError()
        data = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique_pairs)
        if (not isinstance(data, dict) or set(data) != {'version', 'mode', 'sha256'}
                or type(data['version']) is not int or data['version'] != 1):
            raise ValueError()
        raw = base64.b64decode(data['mode'], validate=True)
        if hashlib.sha256(raw).hexdigest() != data['sha256']:
            raise ValueError()
        decode_mode(raw)
        return raw
    except (ValueError, TypeError, KeyError, UnicodeError, DisplayError):
        raise DisplayError('state_invalid') from None


def restore_mode(native, raw, report, prefix='restore'):
    logged = emit_best_effort(report, prefix+'_attempt', original=summary(raw))
    code = native.change(raw)
    logged = emit_best_effort(report, prefix+'_native', code=code) and logged
    if code != 0:
        raise DisplayError('restore_native', code)
    actual = native.current()
    logged = emit_best_effort(report, prefix+'_actual', mode=summary(actual)) and logged
    if summary(actual) != summary(raw):
        raise DisplayError('restore_mismatch')
    logged = emit_best_effort(report, 'restored' if prefix == 'restore' else 'rolled_back') and logged
    if not logged:
        raise DisplayError('metadata_io')


def execute(operation, state, metadata, *, platform=None, environment=None,
            backend_factory=NativeBackend):
    report = Reporter(metadata)
    native, original, attempted = None, None, False
    try:
        require_ci(sys.platform if platform is None else platform,
                   os.environ if environment is None else environment)
        state = Path(state)
        if operation == 'restore':
            if not state.exists():
                report.emit('restore_no_state')
                return 0
            original = load_state(state)  # Validate before constructing native backend.
            native = backend_factory()
            restore_mode(native, original, report)
            return 0
        if operation != 'prepare':
            raise DisplayError('operation_invalid')
        if state.exists():
            raise DisplayError('state_exists')
        native = backend_factory()
        original = native.current()
        report.emit('before', mode=summary(original))
        modes = native.enumerate()
        report.emit('enumerated', modes=[summary(raw) for raw in modes], request=TARGET)
        supported = [raw for raw in modes if summary(raw)['size'] == TARGET]
        if not supported:
            raise DisplayError('target_unsupported')
        before = summary(original)
        target = min(supported, key=lambda raw: (summary(raw)['bpp'] != before['bpp'],
                                                summary(raw)['frequency'] != before['frequency']))
        save_state(state, original)  # Durable exact original BEFORE first mutation.
        report.emit('state_saved')
        attempted = True
        code = native.change(target)
        logged = emit_best_effort(report, 'apply', code=code, request=summary(target))
        if code != 0:
            raise DisplayError('apply_native', code)
        actual = native.current()
        logged = emit_best_effort(report, 'after', mode=summary(actual)) and logged
        if summary(actual)['size'] != TARGET:
            raise DisplayError('apply_mismatch')
        measurements = native.measure()
        logged = emit_best_effort(report, 'measurements', **measurements) and logged
        validate_measurements(measurements)
        logged = emit_best_effort(report, 'prepared') and logged
        if not logged:
            raise DisplayError('metadata_io')
        return 0
    except Exception as exc:
        if isinstance(exc, DisplayError) and exc.measurements is not None:
            emit_best_effort(report, 'measurements', **exc.measurements)
        emit_best_effort(report, 'failure', **failure_details(exc))
        cleanup_failure = getattr(exc, 'cleanup_failure', None)
        if cleanup_failure is not None:
            emit_best_effort(report, 'cleanup_failed', **cleanup_failure)
        if attempted:
            try:
                restore_mode(native, original, report, 'rollback')
            except Exception as rollback_error:
                emit_best_effort(report, 'rollback_failed', **failure_details(rollback_error))
        return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='Hosted Windows CI-only ephemeral display helper')
    parser.add_argument('operation', choices=('prepare', 'restore'))
    parser.add_argument('--state', type=Path, default=Path('.ci-display-state.json'))
    parser.add_argument('--metadata', type=Path, default=Path('ci-logs/display.jsonl'))
    args = parser.parse_args(argv)
    return execute(args.operation, args.state, args.metadata)


if __name__ == '__main__':
    raise SystemExit(main())
