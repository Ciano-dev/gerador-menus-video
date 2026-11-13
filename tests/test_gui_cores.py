"""Miniatura local: contratos executáveis sem servidor gráfico."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui_cores import ColorPreview, draw_model
from menu_anisio import animated_layout

PALETTE = {'fundo': '#112233', 'texto': '#ddeeff', 'destaque': '#ff8800'}


class Var:
    def __init__(self, value):
        self.value, self.traces = value, {}
    def get(self):
        return self.value
    def set(self, value):
        self.value = value
        for callback in list(self.traces.values()):
            callback()
    def trace_add(self, mode, callback):
        token = str(len(self.traces))
        self.traces[token] = callback
        return token
    def trace_remove(self, mode, token):
        del self.traces[token]


class Canvas:
    def __init__(self):
        self.items, self.jobs, self.binds = [], {}, {}
        self.serial = 0
    def bind(self, event, callback, add=None):
        self.binds[event] = callback
        return event
    def unbind(self, event, token):
        self.binds.pop(event, None)
    def after_idle(self, callback):
        self.serial += 1
        self.jobs[self.serial] = callback
        return self.serial
    def after_cancel(self, token):
        self.jobs.pop(token, None)
    def flush(self):
        jobs, self.jobs = self.jobs, {}
        for callback in jobs.values():
            callback()
    def winfo_width(self): return 384
    def winfo_height(self): return 216
    def delete(self, tag): self.items.clear()
    def create_rectangle(self, *coords, **kwargs): self.items.append(('rectangle', coords, kwargs))
    def create_text(self, *coords, **kwargs):
        self.items.append(('text', coords, kwargs))
        return len(self.items)
    def bbox(self, item):
        # Artificial Tk boundary only; native acceptance reads the actual Tk bbox.
        kind, coords, options = self.items[item-1]
        x, y = coords
        lines = options['text'].split('\n')
        font = options['font']
        width = max(font.measure(line) for line in lines)
        height = len(lines) * font.metrics('linespace')
        left = x-width/2 if options['anchor'] == 'n' else x
        return [round(left), round(y), round(left+width), round(y+height)]
    def move(self, item, dx, dy):
        kind, coords, options = self.items[item-1]
        self.items[item-1] = (kind, (coords[0]+dx, coords[1]+dy), options)


class Font:
    def __init__(self, **kwargs): self.size = abs(kwargs['size'])
    def measure(self, value): return len(value) * self.size * .6
    def metrics(self, key): return self.size + 1


class ModelTests(unittest.TestCase):
    def test_three_roles_black_slots_fixed_cards_scaled_layout(self):
        model = draw_model('Título', PALETTE, ['Um', 'Dois'], 384, 216)
        self.assertEqual(model['palette'], PALETTE)
        self.assertEqual(model['size'], (384, 216))
        by_tag = {item['tag']: item for item in model['items']}
        self.assertEqual(by_tag['background']['fill'], PALETTE['fundo'])
        self.assertEqual(by_tag['title']['fill'], PALETTE['texto'])
        self.assertEqual(by_tag['line']['fill'], PALETTE['destaque'])
        for i, cell in enumerate(animated_layout('1080p')[:2], 1):
            self.assertEqual(by_tag[f'card-{i}']['fill'], '#192c42')
            self.assertEqual(by_tag[f'video-{i}']['fill'], '#000000')
            self.assertEqual(by_tag[f'number-{i}']['fill'], PALETTE['destaque'])
            self.assertEqual(by_tag[f'name-{i}']['fill'], PALETTE['texto'])
            self.assertAlmostEqual(by_tag[f'card-{i}']['box'][0], cell['x'] / 5)
            self.assertEqual(by_tag[f'number-{i}']['text'], f'{i:02d}')

    def test_illustration_empty_title_unicode_no_mutation_and_only_six(self):
        colors = dict(PALETTE)
        model = draw_model('', colors, [], 320, 180)
        self.assertTrue(model['illustrative'])
        self.assertTrue(all('Exemplo' in item['text'] for item in model['items'] if item['tag'].startswith('name-')))
        names = ['Árvore 漢字 ' + 'W' * 100, '', 'Três', '4', '5', '6', 'ignored']
        before = copy.deepcopy(names)
        model = draw_model('', colors, names, 320, 180)
        self.assertFalse(model['illustrative'])
        self.assertEqual(model['names'], names[:6])
        self.assertEqual(names, before)
        self.assertEqual(colors, PALETTE)
        self.assertEqual(next(x for x in model['items'] if x['tag'] == 'title')['text'], '')

    def test_invalid_colors_and_aspect_fallback(self):
        for width, height in [(0, 0), (float('nan'), 10), (100, 100), (None, None)]:
            model = draw_model(None, {'fundo': 'bad', 'texto': None}, [], width, height)
            self.assertAlmostEqual(model['size'][0] / model['size'][1], 16 / 9)
            self.assertTrue(all(len(value) == 7 and value.startswith('#') for value in model['palette'].values()))


class AdapterTests(unittest.TestCase):
    def test_default_font_factory_uses_canvas_tcl_without_master_option(self):
        import tkinter as tk
        from tkinter import font as tkfont
        from types import SimpleNamespace

        class FakeTcl:
            def __init__(self):
                self.calls, self.fonts = [], {}

            splitlist = staticmethod(tuple)
            getint = staticmethod(int)

            def call(self, *args):
                self.calls.append(args)
                if args[:2] == ('font', 'create'):
                    options = dict(zip(args[3::2], args[4::2]))
                    if '-master' in options:
                        raise tk.TclError('bad option "-master"')
                    self.fonts[args[2]] = options
                    return args[2]
                if args[:2] == ('font', 'measure'):
                    size = abs(int(self.fonts[args[2]]['-size']))
                    return round(len(args[3]) * size * .6)
                if args[:2] == ('font', 'metrics') and args[3:] == ('-linespace',):
                    return abs(int(self.fonts[args[2]]['-size'])) + 1
                if args[:2] == ('font', 'delete'):
                    self.fonts.pop(args[2])
                    return ''
                raise AssertionError(f'Unexpected Tcl call: {args!r}')

        canvas, title = Canvas(), Var('Título')
        canvas.tk = FakeTcl()
        fallback_root = SimpleNamespace(tk=FakeTcl())
        colors = {key: Var(value) for key, value in PALETTE.items()}
        with patch('tkinter._get_default_root', return_value=fallback_root) as default_root:
            preview = ColorPreview(canvas, title, colors, lambda: ['Cena'])
            self.addCleanup(preview.destroy)
            canvas.flush()
            title.set('Novo título')
            canvas.flush()
            default_root.assert_not_called()

        self.assertEqual(preview.redraw_count, 2)
        self.assertEqual(fallback_root.tk.calls, [])
        text_fonts = [opts['font'] for kind, coords, opts in canvas.items if kind == 'text']
        self.assertTrue(text_fonts)
        for font in text_fonts:
            self.assertIsInstance(font, tkfont.Font)
            self.assertIs(font._tk, canvas.tk)
        create_calls = [call for call in canvas.tk.calls if call[:2] == ('font', 'create')]
        self.assertTrue(create_calls)
        for call in create_calls:
            self.assertNotIn('-master', call[3::2])
        for operation in ('measure', 'metrics'):
            self.assertTrue(any(call[:2] == ('font', operation) for call in canvas.tk.calls))

    def test_debounce_repeat_refresh_and_destroy_remove_every_trace_and_idle(self):
        canvas = Canvas()
        title = Var('Olá')
        colors = {key: Var(value) for key, value in PALETTE.items()}
        names = ['W' * 200, 'Árvore 漢字']
        preview = ColorPreview(canvas, title, colors, lambda: names, font_factory=Font)
        for _ in range(20): preview.request()
        self.assertEqual(len(canvas.jobs), 1)
        canvas.flush()
        count = len(canvas.items)
        self.assertEqual(preview.redraw_count, 1)
        for _ in range(10):
            title.set('Novo')
            canvas.flush()
            self.assertEqual(len(canvas.items), count)
        names.reverse()
        preview.request()
        canvas.flush()
        self.assertEqual(preview.model['names'], names)
        for kind, coords, opts in canvas.items:
            if kind == 'text':
                box = next(x['box'] for x in preview.model['items'] if x['tag'] in opts['tags'])
                font = opts['font']
                tolerance = 1 if 'title' in opts['tags'] else .01  # actual integer Tk bbox rounding
                self.assertTrue(all(font.measure(line) <= box[2] - box[0] + tolerance for line in opts['text'].split('\n')))
                self.assertLessEqual(len(opts['text'].split('\n')) * font.metrics('linespace'), box[3] - box[1] + tolerance)
        title.set('pending')
        preview.destroy()
        self.assertFalse(canvas.jobs)
        self.assertFalse(canvas.binds)
        self.assertFalse(title.traces)
        self.assertTrue(all(not value.traces for value in colors.values()))
        preview.destroy()
        preview.request()
        self.assertFalse(canvas.jobs)

    def test_partial_state_invalid_color_zero_io(self):
        canvas = Canvas()
        title = Var('')
        colors = {key: Var(value) for key, value in PALETTE.items()}
        preview = ColorPreview(canvas, title, colors, lambda: [], font_factory=Font)
        with patch('builtins.open', side_effect=AssertionError('I/O')), \
             patch('menu_anisio.resolve_tool', side_effect=AssertionError('tool')), \
             patch('gui_servicos.inspect_media', side_effect=AssertionError('probe')), \
             patch('gui_servicos.render_project', side_effect=AssertionError('render')):
            canvas.flush()
            colors['fundo'].set('not a color')
            title.set(None)
            canvas.flush()
        self.assertEqual(preview.model['palette']['fundo'], '#101f33')
        self.assertTrue(preview.model['illustrative'])


class AppInvalidationTests(unittest.TestCase):
    def test_confirm_color_invalidates_without_capture_and_cancel_preserves(self):
        import menu_gui
        app = menu_gui.App.__new__(menu_gui.App)
        app.root = None
        app.color_vars = {key: Var(value) for key, value in PALETTE.items()}
        app.color_swatches = {key: Mock() for key in PALETTE}
        app.color_preview = Mock()
        app.capture = Mock(side_effect=AssertionError('snapshot proibido no redraw'))
        with patch.object(menu_gui.colorchooser, 'askcolor', return_value=((0, 0, 0), '#aabbcc')):
            app.choose_color('fundo')
        self.assertEqual(app.color_vars['fundo'].get(), '#aabbcc')
        app.color_preview.request.assert_called_once()
        app.color_preview.request.reset_mock()
        with patch.object(menu_gui.colorchooser, 'askcolor', return_value=(None, None)):
            app.choose_color('fundo')
        self.assertEqual(app.color_vars['fundo'].get(), '#aabbcc')
        app.color_preview.request.assert_not_called()


class NativeReportTests(unittest.TestCase):
    def test_gate_rejects_absent_native_canvas_evidence(self):
        from native_color_checks import validate_color_report
        with self.assertRaises(ValueError):
            validate_color_report({})


class RealTkTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        import menu_gui
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            if sys.platform == 'win32':
                raise
            self.skipTest(f'Tk real indisponível sem DISPLAY: {exc}')
        self.app = menu_gui.App(self.root)
        self.root.update()

    def tearDown(self):
        if hasattr(self, 'app'):
            self.app.shutdown()

    @unittest.skipUnless(sys.platform == 'win32', 'Captura real exige Windows.')
    def test_00_windows_real_panel_captures_and_colorreport(self):
        import hashlib
        import json
        import struct
        import tempfile
        from native_color_checks import probe_color_preview, validate_color_report
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / 'real-tk-panel'
            report = probe_color_preview(self.app, capture_prefix=prefix)
            validate_color_report(report, require_captures=True)
            report_path = Path(temporary) / 'colorreport.json'
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
            loaded = json.loads(report_path.read_text(encoding='utf-8'))
            validate_color_report(loaded, require_captures=True)
            self.assertEqual(loaded, report)
            self.assertEqual(len(list(Path(temporary).glob('*.png'))), 2)
            hashes = []
            for capture, probe in zip(loaded['captures'], loaded['probes']):
                path = Path(capture['path'])
                self.assertEqual(path.parent, Path(temporary))
                self.assertEqual(capture['phase'], probe['phase'])
                self.assertEqual(capture['method'], 'Win32.GDI.BitBlt')
                raw = path.read_bytes()
                self.assertEqual(raw[:8], b'\x89PNG\r\n\x1a\n')
                self.assertEqual(struct.unpack('>II', raw[16:24]), tuple(probe['panel_bounds'][2:]))
                hashes.append(hashlib.sha256(raw).hexdigest())
                self.assertEqual(hashes[-1], capture['sha256'])
            self.assertNotEqual(*hashes)
            self.assertEqual(loaded['media_calls'], 0)
            self.assertTrue(loaded['state_preserved'] and loaded['cancel_preserved'])
            self.assertEqual(loaded['mutation_checks'], [True] * 6)

    def test_effective_canvas_and_all_mutations_at_minimum_width(self):
        from native_color_checks import probe_color_preview, validate_color_report
        for width, height in [(1024, 768), (1366, 768), (820, 600), (960, 720)]:
            width = min(width, self.root.winfo_screenwidth() - 40)
            height = min(height, self.root.winfo_screenheight() - 40)
            self.root.geometry(f'{width}x{height}+20+20')
            self.root.update()
            result = probe_color_preview(self.app)
            validate_color_report(result, require_captures=False)

    def test_open_and_worker_add_use_current_names_and_palette(self):
        import menu_gui
        from gui_autoria import MediaInfo, Project
        project = Project(title='Projeto reaberto', colors=dict(PALETTE))
        project.add_video(MediaInfo(Path('sintetico.mp4'), 12))
        project.edit_scene(0, 'Cena reaberta', 0, 3)
        self.app.confirm_discard = lambda: True
        # Mesmo callback usado pelo Worker; apenas a sondagem de fontes é substituída.
        self.app.run_job = lambda operation, done: done(operation(lambda text: None))
        with patch.object(menu_gui.filedialog, 'askopenfilename', return_value='sintetico.menu'), \
             patch.object(menu_gui.services, 'open_project', return_value=project):
            self.app.choose_open()
        self.root.update()
        self.assertEqual(self.app.color_preview.model['names'], ['Cena reaberta'])
        self.assertEqual(self.app.color_preview.model['palette'], PALETTE)
        with patch.object(menu_gui.filedialog, 'askopenfilenames', return_value=('adicionada.mp4',)), \
             patch.object(menu_gui.services, 'inspect_media', return_value=MediaInfo(Path('adicionada.mp4'), 10)):
            self.app.choose_videos()
        self.root.update()
        self.assertEqual(self.app.color_preview.model['names'], ['Cena reaberta', 'adicionada'])
        self.app.new_project()
        self.root.update()
        self.assertTrue(self.app.color_preview.model['illustrative'])
        self.assertEqual(self.app.project.scenes, [])
        self.assertEqual(self.app.color_preview.model['palette'],
                         {'fundo': '#101f33', 'texto': '#f3ead6', 'destaque': '#c7a362'})

    def test_etapa2_native_geometry_load_controls_and_scrolling(self):
        import json
        import os
        import tempfile
        from gerar_fixtures import generate
        from gui_autoria import Project
        import gui_servicos as services
        from native_ui_checks import probe_interface, validate_interface_report
        with tempfile.TemporaryDirectory(prefix='etapa2-native-') as temporary:
            base = Path(temporary)
            sources = generate(base / 'fontes')
            seed = Project(duration=.4)
            seed.add_video(services.inspect_media(sources / 'cores-com-audio.mp4'))
            seed.edit_scene(0, 'Cena sintética', 0, .2)
            seed.music = services.inspect_media(sources / 'som-sem-video.m4a', audio=True)
            self.app.adopt(seed)
            evidence = Path(os.environ.get('ANISIO_NATIVE_EVIDENCE_DIR', temporary))
            evidence.mkdir(parents=True, exist_ok=True)
            report = probe_interface(self.app, evidence / 'source-interface', capture=sys.platform == 'win32')
            validate_interface_report(report, require_captures=sys.platform == 'win32')
            loaded = json.loads((evidence / 'source-interface/interface.json').read_text(encoding='utf-8'))
            self.assertEqual(report, loaded)


def report_fixture(alignment='esquerda'):
    """Dados artificiais só para testar rejeição do schema; não são evidência visual."""
    from native_color_checks import BASE_PALETTE, ALT_PALETTE
    names = [f'Cena sintética {i}' for i in range(1, 7)]
    probes, captures = [], []
    for index, (phase, palette) in enumerate([('before', BASE_PALETTE), ('after', ALT_PALETTE)], 1):
        title = 'Título de fixture'
        model = draw_model(title, palette, names, 384, 216, title_alignment=alignment)
        for item in model['items']:
            item['coords'] = list(item['box'] if item['kind'] == 'rectangle' else item['box'][:2])
            item['bbox'] = list(item['box'])
            if item['kind'] == 'text':
                centered = item.get('alignment') == 'centralizado'
                if centered:
                    item['coords'][0] = (item['box'][0] + item['box'][2]) / 2
                item['anchor'] = 'n' if centered else 'nw'
                item['justify'] = 'center' if centered else 'left'
        # Final contract: title geometry comes from measured Canvas bounds.
        canvas = Canvas()
        preview = ColorPreview(canvas, Var(title), {k: Var(v) for k, v in palette.items()},
                               lambda: names, font_factory=Font, alignment=Var(alignment))
        canvas.flush()
        title_id = next(i for i, row in enumerate(canvas.items, 1) if 'title' in row[2]['tags'])
        title_item = next(item for item in model['items'] if item['tag'] == 'title')
        title_item['coords'] = list(canvas.items[title_id-1][1])
        title_item['bbox'] = canvas.bbox(title_id)
        preview.destroy()
        probes.append({'phase': phase, 'palette': palette, 'title': title, 'names': names,
                       'alignment': alignment,
                       'visible': True, 'canvas_bounds': [300, 100, 384, 216],
                       'panel_bounds': [20, 60, 700, 300], 'viewport_bounds': [0, 0, 820, 450],
                       'screen_bounds': [0, 0, 1920, 1080], 'redraw_count': index,
                       'eventloop_ticks': index, 'items': model['items']})
        captures.append({'phase': phase, 'method': 'Win32.GDI.BitBlt',
                         'path': f'C:/synthetic-fixture-{phase}.png', 'width': 700, 'height': 300,
                         'sha256': str(index) * 64})
    return {'schema_version': 1, 'media_calls': 0, 'state_preserved': True,
            'cancel_preserved': True, 'mutation_checks': [True] * 6, 'probes': probes, 'captures': captures}


class NativeGateTests(unittest.TestCase):
    def test_capture_provenance_requires_current_native_method(self):
        from native_color_checks import validate_color_report
        valid = report_fixture()
        validate_color_report(valid, require_captures=True)
        for method in ('System.Drawing.CopyFromScreen', 'fake', '', None):
            invalid = copy.deepcopy(valid)
            invalid['captures'][0]['method'] = method
            with self.subTest(method=method), self.assertRaises(ValueError):
                validate_color_report(invalid, require_captures=True)
        ps = (Path(__file__).resolve().parents[1] / 'windows_build/smoke_windows.ps1').read_text()
        self.assertIn("$capture.method -ne 'Win32.GDI.BitBlt'", ps)
        self.assertNotIn("$capture.method -ne 'System.Drawing.CopyFromScreen'", ps)

    def test_each_missing_or_corrupt_measurement_fails_closed(self):
        from native_color_checks import validate_color_report
        valid = report_fixture()
        validate_color_report(valid)
        for key in valid:
            invalid = copy.deepcopy(valid)
            invalid.pop(key)
            with self.subTest(missing=key), self.assertRaises(ValueError):
                validate_color_report(invalid)
        for field, value in [('visible', False), ('eventloop_ticks', 0), ('names', []),
                             ('canvas_bounds', [0, 0, 200, 200]), ('items', [])]:
            invalid = copy.deepcopy(valid)
            invalid['probes'][1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_color_report(invalid)
        for tag in ('background', 'title', 'line', 'card-1', 'video-1', 'name-1', 'number-1'):
            invalid = copy.deepcopy(valid)
            next(item for item in invalid['probes'][1]['items'] if item['tag'] == tag)['fill'] = '#ffffff'
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                validate_color_report(invalid)
        invalid = copy.deepcopy(valid)
        invalid['probes'][0]['items'][3]['coords'][0] += 20
        with self.assertRaises(ValueError):
            validate_color_report(invalid)
        invalid = copy.deepcopy(valid)
        invalid['probes'][0]['items'][1]['bbox'][2] += 500
        with self.assertRaises(ValueError):
            validate_color_report(invalid)

    def test_windows_smoke_requires_canvas_and_capture_evidence(self):
        from test_smoke_windows import NativeSmokeContractTests
        import smoke_windows
        valid = NativeSmokeContractTests().valid_result()
        valid['platform'] = 'win32'
        valid.pop('color_preview', None)
        with self.assertRaisesRegex(ValueError, 'miniatura'):
            smoke_windows.validate_result(valid)
        valid['color_preview'] = report_fixture()
        smoke_windows.validate_result(valid)


class LifecycleAndGeometryTests(unittest.TestCase):
    def test_configure_events_coalesce_and_canvas_destroy_cleans_up(self):
        from types import SimpleNamespace
        canvas, title = Canvas(), Var('Título')
        colors = {key: Var(value) for key, value in PALETTE.items()}
        preview = ColorPreview(canvas, title, colors, lambda: [], font_factory=Font)
        for _ in range(50):
            canvas.binds['<Configure>'](SimpleNamespace(widget=canvas))
        self.assertEqual(len(canvas.jobs), 1)
        canvas.flush()
        preview.request()
        canvas.binds['<Destroy>'](SimpleNamespace(widget=canvas))
        self.assertFalse(canvas.jobs)
        self.assertFalse(canvas.binds)
        self.assertFalse(title.traces)

    def test_transient_tcl_error_is_not_a_dialog_or_validation(self):
        import tkinter as tk
        canvas = Canvas()
        title = Var('')
        colors = {key: Var(value) for key, value in PALETTE.items()}
        preview = ColorPreview(canvas, title, colors, lambda: [], font_factory=Font)
        with patch.object(title, 'get', side_effect=tk.TclError('unset')), \
             patch('tkinter.messagebox.showerror', side_effect=AssertionError('dialog')), \
             patch('pathlib.Path.open', side_effect=AssertionError('disk')), \
             patch('subprocess.Popen', side_effect=AssertionError('process')), \
             patch('threading.Thread', side_effect=AssertionError('thread')):
            canvas.flush()
        self.assertEqual(next(item for item in preview.model['items'] if item['tag'] == 'title')['text'], '')

    def test_every_slot_and_label_matches_pure_layout_and_stays_in_menu(self):
        from gui_cores import DEFAULT_COLORS
        for width, height in [(384, 216), (320, 180), (200, 150)]:
            model = draw_model('Título', {}, ['Cena'] * 6, width, height)
            self.assertEqual(model['palette'], DEFAULT_COLORS)
            factor = model['size'][0] / 1920
            by_tag = {item['tag']: item for item in model['items']}
            for i, cell in enumerate(animated_layout('1080p'), 1):
                self.assertEqual(by_tag[f'video-{i}']['box'], tuple(value * factor for value in
                    (cell['x']+cell['pad'], cell['y']+cell['pad'],
                     cell['x']+cell['pad']+cell['vw'], cell['y']+cell['pad']+cell['vh'])))
                for prefix in ('name', 'number', 'video', 'card'):
                    x, y, right, bottom = by_tag[f'{prefix}-{i}']['box']
                    self.assertTrue(0 <= x < right <= model['size'][0])
                    self.assertTrue(0 <= y < bottom <= model['size'][1])
            line = by_tag['line']['box']
            self.assertGreaterEqual(line[3] - line[1], .999)


class FakeWin32:
    """Fronteira ABI artificial: não comprova ABI Windows nem gera evidência visual."""
    def __init__(self):
        from types import SimpleNamespace
        self.calls, self.selected = [], False
        self.hwnd, self.screen, self.dc = 0x100000001, 0x200000002, 0x300000003
        self.bitmap, self.old = 0x400000004, 0x500000005
        self.user, self.gdi = SimpleNamespace(), SimpleNamespace()
        self.failures = {}
        for name in ('GetAncestor', 'SetForegroundWindow', 'GetForegroundWindow', 'GetDC', 'ReleaseDC'):
            setattr(self.user, name, self.function(name))
        for name in ('CreateCompatibleDC', 'CreateCompatibleBitmap', 'SelectObject', 'BitBlt',
                     'GetDIBits', 'DeleteObject', 'DeleteDC'):
            setattr(self.gdi, name, self.function(name))

    def function(self, name):
        def invoke(*args):
            import ctypes
            self.calls.append((name, args))
            if name in self.failures:
                value = self.failures[name]
                if isinstance(value, BaseException):
                    raise value
                return value(*args) if callable(value) else value
            if name == 'GetAncestor': return self.hwnd
            if name == 'GetForegroundWindow': return self.hwnd
            if name == 'GetDC': return self.screen
            if name == 'CreateCompatibleDC': return self.dc
            if name == 'CreateCompatibleBitmap': return self.bitmap
            if name == 'DeleteDC':
                self.selected = False
                return 1
            if name == 'SelectObject':
                previous = self.bitmap if self.selected else self.old
                self.selected = args[1] == self.bitmap
                return previous
            if name == 'GetDIBits':
                if self.selected:
                    raise AssertionError('GetDIBits exige bitmap não selecionado')
                header = args[5]._obj.bmiHeader
                if (header.biSize, header.biWidth, header.biHeight, header.biPlanes,
                    header.biBitCount, header.biCompression) != (40, 3, -2, 1, 24, 0):
                    raise AssertionError('BITMAPINFO top-down 24bit inválido')
                ctypes.memmove(args[4], bytes(range(24)), 24)
                return 2
            return 1
        return invoke

    def load(self, name, **kwargs):
        return self.user if name.lower() == 'user32' else self.gdi


def capture_app():
    def widget(rect):
        return Mock(winfo_rootx=Mock(return_value=rect[0]), winfo_rooty=Mock(return_value=rect[1]),
                    winfo_width=Mock(return_value=rect[2]), winfo_height=Mock(return_value=rect[3]),
                    winfo_viewable=Mock(return_value=True))
    root = widget([0, 0, 800, 600])
    root.winfo_screenwidth.return_value = 1920
    root.winfo_screenheight.return_value = 1080
    root.winfo_id.return_value = 0x600000006
    return Mock(root=root, visual_group=widget([10, 10, 3, 2]), viewport_canvas=widget([0, 0, 800, 600]))


class CaptureHarness:
    def run_capture(self, fake=None, app=None, rect=None):
        import ctypes
        import os
        import tempfile
        from contextlib import ExitStack
        import native_color_checks as native
        fake, app = fake or FakeWin32(), app or capture_app()
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            stack.enter_context(patch.object(native.sys, 'platform', 'win32'))
            stack.enter_context(patch.dict(os.environ, {'SystemRoot': 'C:/Windows'}))
            stack.enter_context(patch.object(ctypes, 'WinDLL', side_effect=fake.load, create=True))
            stack.enter_context(patch('subprocess.run', side_effect=AssertionError('child capture proibido')))
            stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('child capture proibido')))
            stack.enter_context(patch('threading.Thread.start', side_effect=AssertionError('thread proibida')))
            path = Path(temporary) / 'unit-only-before.png'
            result = native.capture_panel(app, path, rect if rect is not None else [10, 10, 3, 2])
            return result, path.read_bytes(), fake, app

class NativeCaptureTests(CaptureHarness, unittest.TestCase):
    def test_actual_capture_has_no_child_or_thread(self):
        import hashlib
        result, raw, fake, app = self.run_capture()
        self.assertEqual(raw[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(result['method'], 'Win32.GDI.BitBlt')
        self.assertEqual((result['phase'], result['width'], result['height']), ('before', 3, 2))
        self.assertEqual(result['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(Path(result['path']).name, 'unit-only-before.png')
        app.root.lift.assert_called_once()
        app.root.update.assert_called()
        names = [name for name, _ in fake.calls]
        self.assertIn('BitBlt', names)
        self.assertGreaterEqual(names.count('GetForegroundWindow'), 2)


class PngEncoderTests(unittest.TestCase):
    def test_padded_top_down_bgr_strip_decodes_to_rgb_with_valid_crc(self):
        import struct
        import zlib
        import native_color_checks as native
        # Artificial unit fixture only: red/green/blue top, white/black/yellow bottom.
        bgr = bytes([0, 0, 255, 0, 255, 0, 255, 0, 0, 91, 92, 93,
                     255, 255, 255, 0, 0, 0, 0, 255, 255, 94, 95, 96])
        png = native._encode_png(3, 2, bgr)
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        offset, chunks = 8, []
        while offset < len(png):
            length = struct.unpack_from('>I', png, offset)[0]
            kind = png[offset+4:offset+8]
            data = png[offset+8:offset+8+length]
            crc = struct.unpack_from('>I', png, offset+8+length)[0]
            self.assertEqual(crc, zlib.crc32(kind + data) & 0xffffffff)
            chunks.append((kind, data))
            offset += 12 + length
        self.assertEqual(offset, len(png))
        self.assertEqual([kind for kind, _ in chunks], [b'IHDR', b'IDAT', b'IEND'])
        self.assertEqual(struct.unpack('>IIBBBBB', chunks[0][1]), (3, 2, 8, 2, 0, 0, 0))
        self.assertEqual(chunks[-1][1], b'')
        self.assertEqual(zlib.decompress(chunks[1][1]), bytes([
            0, 255, 0, 0, 0, 255, 0, 0, 0, 255,
            0, 255, 255, 255, 0, 0, 0, 255, 255, 0]))

    def test_encoder_rejects_bad_dimensions_and_buffer(self):
        import native_color_checks as native
        for width, height, data in [(0, 2, b''), (3, -1, b''), (3, 2, b'x' * 23),
                                    (3, 2, b'x' * 25), (True, 2, b'')]:
            with self.subTest(width=width, height=height), self.assertRaises(ValueError):
                native._encode_png(width, height, data)


class Win32AbiAndCleanupTests(CaptureHarness, unittest.TestCase):

    def test_all_signatures_are_explicit_fixed_width_and_pointer_handles(self):
        import ctypes as c
        import native_color_checks as native
        _, _, fake, _ = self.run_capture()
        ptr, i32, u32 = c.c_void_p, c.c_int32, c.c_uint32
        signatures = {
            'GetAncestor': ([ptr, u32], ptr), 'SetForegroundWindow': ([ptr], i32),
            'GetForegroundWindow': ([], ptr), 'GetDC': ([ptr], ptr), 'ReleaseDC': ([ptr, ptr], i32),
            'CreateCompatibleDC': ([ptr], ptr), 'CreateCompatibleBitmap': ([ptr, i32, i32], ptr),
            'SelectObject': ([ptr, ptr], ptr), 'BitBlt': ([ptr, i32, i32, i32, i32, ptr, i32, i32, u32], i32),
            'GetDIBits': ([ptr, ptr, u32, u32, ptr, c.POINTER(native.BITMAPINFO), u32], i32),
            'DeleteObject': ([ptr], i32), 'DeleteDC': ([ptr], i32)}
        for name, (args, result) in signatures.items():
            function = getattr(fake.user, name, None) or getattr(fake.gdi, name)
            with self.subTest(api=name):
                self.assertEqual(function.argtypes, args)
                self.assertIs(function.restype, result)
        self.assertEqual(c.sizeof(native.BITMAPINFOHEADER), 40)
        self.assertEqual(c.sizeof(native.RGBQUAD), 4)
        self.assertEqual(c.sizeof(native.BITMAPINFO), 44)
        self.assertEqual(c.sizeof(ptr), 8)
        self.assertEqual(native.BITMAPINFOHEADER.biWidth.offset, 4)
        self.assertEqual(native.BITMAPINFOHEADER.biHeight.offset, 8)
        self.assertEqual(native.BITMAPINFOHEADER.biCompression.offset, 16)
        calls = dict(fake.calls)
        self.assertEqual(calls['GetAncestor'], (0x600000006, 2))
        self.assertEqual(calls['BitBlt'], (fake.dc, 0, 0, 3, 2, fake.screen, 10, 10, 0x00CC0020))
        self.assertEqual(calls['GetDIBits'][1], fake.bitmap)

    def assert_cleanup(self, fake, expected):
        cleanup = [(name, args) for name, args in fake.calls
                   if name in ('SelectObject', 'DeleteObject', 'DeleteDC', 'ReleaseDC')]
        self.assertEqual(cleanup, expected)
        self.assertFalse(fake.selected)

    def cleanup_sequence(self, fake, selected=True):
        selections = [('SelectObject', (fake.dc, fake.bitmap)), ('SelectObject', (fake.dc, fake.old))] if selected else []
        return selections + [('DeleteObject', (fake.bitmap,)), ('DeleteDC', (fake.dc,)),
                             ('ReleaseDC', (None, fake.screen))]

    def test_success_restores_bitmap_before_reading_and_releases_every_handle(self):
        _, _, fake, _ = self.run_capture()
        self.assert_cleanup(fake, self.cleanup_sequence(fake))
        names = [name for name, _ in fake.calls]
        self.assertLess(max(i for i, name in enumerate(names) if name == 'SelectObject'), names.index('GetDIBits'))

    def test_partial_allocation_and_selection_failures_cleanup(self):
        import ctypes
        for failed in ('GetDC', 'CreateCompatibleDC', 'CreateCompatibleBitmap', 'SelectObject'):
            for value in ([0, ctypes.c_void_p(-1).value] if failed == 'SelectObject' else [0]):
                fake = FakeWin32()
                fake.failures[failed] = value
                with self.subTest(api=failed, result=value), self.assertRaisesRegex(RuntimeError, failed):
                    self.run_capture(fake)
                expected = []
                if failed == 'SelectObject':
                    expected += [('SelectObject', (fake.dc, fake.bitmap)), ('DeleteObject', (fake.bitmap,))]
                if failed in ('SelectObject', 'CreateCompatibleBitmap'):
                    expected += [('DeleteDC', (fake.dc,))]
                if failed != 'GetDC': expected += [('ReleaseDC', (None, fake.screen))]
                self.assert_cleanup(fake, expected)

    def test_bitblt_and_dib_failure_cleanup(self):
        for failed, value in [('BitBlt', 0), ('GetDIBits', 0), ('GetDIBits', 1)]:
            fake = FakeWin32()
            fake.failures[failed] = value
            with self.subTest(api=failed, result=value), self.assertRaisesRegex(RuntimeError, failed):
                self.run_capture(fake)
            self.assert_cleanup(fake, self.cleanup_sequence(fake))

    def test_restore_failure_retries_and_never_reads_selected_bitmap(self):
        import ctypes
        for persistent in (False, True):
            for bad_handle in (0, ctypes.c_void_p(-1).value):
                fake, restores = FakeWin32(), []
                def select(dc, bitmap):
                    if bitmap == fake.bitmap:
                        fake.selected = True
                        return fake.old
                    restores.append(bitmap)
                    if persistent or len(restores) == 1:
                        return bad_handle
                    fake.selected = False
                    return fake.bitmap
                fake.failures['SelectObject'] = select
                with self.subTest(persistent=persistent, result=bad_handle):
                    with self.assertRaisesRegex(RuntimeError, 'SelectObject restore'):
                        self.run_capture(fake)
                    self.assertEqual(restores, [fake.old, fake.old])
                    self.assertFalse(fake.selected)
                    names = [name for name, _ in fake.calls]
                    self.assertNotIn('GetDIBits', names)
                    self.assertIn('DeleteObject', names)
                    self.assertIn('ReleaseDC', names)
                    if persistent:
                        self.assertLess(names.index('DeleteDC'), names.index('DeleteObject'))

    def test_failed_restore_waits_for_dc_retry_before_deleting_bitmap(self):
        for retry_succeeds in (True, False):
            with self.subTest(retry_succeeds=retry_succeeds):
                fake = FakeWin32()
                restores, dc_attempts, selected_at_delete = [], [], []
                released = {'bitmap': False, 'dc': False, 'screen': False}
                original = OSError('original restore fixture')
                def select(dc, bitmap):
                    if bitmap == fake.bitmap:
                        fake.selected = True
                        return fake.old
                    restores.append(bitmap)
                    if not retry_succeeds and len(restores) == 1:
                        raise original
                    return 0
                def delete_dc(dc):
                    dc_attempts.append(dc)
                    if len(dc_attempts) == 1:
                        return 0
                    if not retry_succeeds:
                        raise OSError('cleanup DC fixture')
                    released['dc'] = True
                    fake.selected = False
                    return 1
                def delete_bitmap(bitmap):
                    selected_at_delete.append(fake.selected)
                    if fake.selected:
                        return 0
                    released['bitmap'] = True
                    return 1
                def release_screen(hwnd, dc):
                    released['screen'] = True
                    return 1
                fake.failures.update(SelectObject=select, DeleteDC=delete_dc,
                                     DeleteObject=delete_bitmap, ReleaseDC=release_screen)
                with self.assertRaises(RuntimeError if retry_succeeds else OSError) as caught:
                    self.run_capture(fake)
                if retry_succeeds:
                    self.assertEqual(str(caught.exception), 'SelectObject restore falhou.')
                else:
                    self.assertIs(caught.exception, original)
                self.assertEqual(restores, [fake.old, fake.old])
                self.assertEqual(dc_attempts, [fake.dc, fake.dc])
                self.assertNotIn('GetDIBits', [name for name, _ in fake.calls])
                self.assertIn(('ReleaseDC', (None, fake.screen)), fake.calls)
                self.assertEqual(
                    {'selected_at_delete': selected_at_delete, 'released': released,
                     'selected': fake.selected},
                    {'selected_at_delete': [False] if retry_succeeds else [],
                     'released': {'bitmap': retry_succeeds, 'dc': retry_succeeds, 'screen': True},
                     'selected': not retry_succeeds})

    def test_cleanup_failure_still_attempts_remaining_releases(self):
        for failed in ('DeleteObject', 'DeleteDC', 'ReleaseDC'):
            for result in (0, OSError('cleanup fixture')):
                fake = FakeWin32()
                fake.failures[failed] = result
                with self.subTest(api=failed, result=result):
                    with self.assertRaisesRegex(RuntimeError, failed):
                        self.run_capture(fake)
                    self.assert_cleanup(fake, self.cleanup_sequence(fake))

    def test_encode_and_save_failure_cleanup(self):
        for target in ('native_color_checks._encode_png', 'pathlib.Path.write_bytes'):
            fake = FakeWin32()
            with self.subTest(target=target), patch(target, side_effect=OSError('fixture failure')):
                with self.assertRaisesRegex(OSError, 'fixture failure'):
                    self.run_capture(fake)
            self.assert_cleanup(fake, self.cleanup_sequence(fake))

    def test_incomplete_save_fails_and_releases_every_handle(self):
        fake = FakeWin32()
        with patch('pathlib.Path.write_bytes', return_value=0):
            with self.assertRaisesRegex(OSError, 'incompleta'):
                self.run_capture(fake)
        self.assert_cleanup(fake, self.cleanup_sequence(fake))

    def test_foreground_refusal_before_and_after_copy_fails_closed(self):
        for failed in ('GetAncestor', 'SetForegroundWindow', 'before', 'after'):
            fake = FakeWin32()
            if failed in ('before', 'after'):
                sequence = iter([0] if failed == 'before' else [fake.hwnd, fake.hwnd, 0])
                fake.failures['GetForegroundWindow'] = lambda: next(sequence)
            else: fake.failures[failed] = 0
            with self.subTest(stage=failed), self.assertRaises(RuntimeError):
                self.run_capture(fake)
            names = [name for name, _ in fake.calls]
            if failed != 'after': self.assertNotIn('GetDC', names)
            else: self.assert_cleanup(fake, self.cleanup_sequence(fake))

    def test_foreground_loss_during_allocation_refuses_bitblt(self):
        fake = FakeWin32()
        sequence = iter([fake.hwnd, 0])
        fake.failures['GetForegroundWindow'] = lambda: next(sequence)
        with self.assertRaisesRegex(RuntimeError, 'foreground'):
            self.run_capture(fake)
        self.assertNotIn('BitBlt', [name for name, _ in fake.calls])
        self.assert_cleanup(fake, self.cleanup_sequence(fake))

    def test_non_main_thread_refused_before_tk_or_win32_calls(self):
        fake, app = FakeWin32(), capture_app()
        with patch('threading.current_thread', return_value=object()):
            with self.assertRaisesRegex(RuntimeError, 'principal'):
                self.run_capture(fake, app)
        self.assertFalse(fake.calls)
        app.root.lift.assert_not_called()
        app.root.update.assert_not_called()

    def test_invalid_offscreen_or_non_panel_bounds_refused(self):
        for rect in ([10, 10, 0, 2], [-1, 10, 3, 2], [10, 10, 3.0, 2], [10, 10, True, 2],
                     [10, 10, 3], [11, 10, 3, 2], [1919, 10, 3, 2]):
            fake = FakeWin32()
            with self.subTest(bounds=rect), self.assertRaises(ValueError):
                self.run_capture(fake, rect=rect)
            self.assertNotIn('GetDC', [name for name, _ in fake.calls])
        for widget in ('visual_group', 'viewport_canvas', 'root'):
            fake, app = FakeWin32(), capture_app()
            if widget == 'root': app.root.winfo_screenwidth.return_value = 12
            elif widget == 'visual_group': app.visual_group.winfo_viewable.return_value = False
            else: app.viewport_canvas.winfo_width.return_value = 12
            with self.subTest(widget=widget), self.assertRaises(ValueError):
                self.run_capture(fake, app)
            self.assertFalse(fake.calls)
