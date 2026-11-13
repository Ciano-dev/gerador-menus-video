"""Tamanho em px 1080p: contratos verticais, sem mídia real nesta primeira camada."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import gui_servicos as services
from gui_autoria import MediaInfo, Project
import menu_anisio as motor


class FontProjectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / 'source.mp4'
        self.source.write_bytes(b'synthetic path fixture; not a rendered video')
        self.project = Project()
        for _ in range(2):
            self.project.add_video(MediaInfo(self.source, 10))

    def test_defaults_remain_legacy_on_save_open_and_capture(self):
        self.assertIsNone(self.project.title_font_size)
        self.assertTrue(all(s.font_size is None for s in self.project.scenes))
        data = services.project_data(self.project, self.base)
        self.assertNotIn('tamanho_titulo', data)
        self.assertTrue(all('tamanho_fonte' not in s for s in data['cenas']))
        path = services.save_project(self.project, self.base / 'legacy.menu')
        with patch.object(services, 'inspect_media', return_value=MediaInfo(self.source, 10)):
            self.assertEqual(services.open_project(path), self.project)

    def test_roundtrip_independent_sizes_follow_scene_reorder_remove_edit(self):
        self.project.title_font_size = 55
        self.project.scenes[0].font_size = 19
        self.project.scenes[1].font_size = 37
        self.project.edit_scene(0, 'Renomeada', 1, 2)
        self.project.move_scene(0, 1)
        self.assertEqual([s.font_size for s in self.project.scenes], [37, 19])
        path = services.save_project(self.project, self.base / 'manual.menu')
        _, rows, _, settings = motor.load_project(path, self.base / 'menu.mp4', with_settings=True)
        self.assertEqual(settings['tamanho_titulo'], 55)
        self.assertEqual([s['tamanho_fonte'] for s in rows], [37, 19])
        with patch.object(services, 'inspect_media', return_value=MediaInfo(self.source, 10)):
            self.assertEqual(services.open_project(path), self.project)
        self.project.remove_scene(0)
        self.assertEqual(self.project.scenes[0].font_size, 19)
        self.assertIsNone(Project().title_font_size)

    def test_invalid_model_and_json_boundaries(self):
        for value in (True, False, 0, 201, -1, 2.5, float('nan'), float('inf'), '24', [], {}):
            for target, field in ((self.project, 'title_font_size'), (self.project.scenes[0], 'font_size')):
                with self.subTest(value=value, field=field):
                    setattr(target, field, value)
                    with self.assertRaises(motor.ErroProjeto):
                        self.project.validate()
                    setattr(target, field, None)
        data = services.project_data(self.project, self.base)
        for key in ('title', 'scene'):
            for value in (True, 0, 201, 2.5, '24', [], {}):
                trial = copy.deepcopy(data)
                if key == 'title':
                    trial['tamanho_titulo'] = value
                else:
                    trial['cenas'][0]['tamanho_fonte'] = value
                path = self.base / 'bad.menu'
                path.write_text(json.dumps(trial), encoding='utf-8')
                with self.subTest(key=key, value=value), self.assertRaises(motor.ErroProjeto):
                    motor.load_project(path, self.base / 'out.mp4')
        for value in (None, 1, 24, 200):
            self.project.title_font_size = value
            self.project.scenes[0].font_size = value
            self.project.validate()

    def test_services_pass_manual_sizes_to_engine_without_saved_project(self):
        self.project.title_font_size = 60
        self.project.scenes[0].font_size = 18
        for fmt in ('MP4', 'AVI'):
            self.project.export_format = fmt
            with patch.object(motor, 'export_media', return_value=self.base / ('out.' + fmt.lower())) as render:
                services.render_project(self.project, None, self.base / ('out.' + fmt.lower()), profile='prova')
            args = render.call_args.args
            self.assertEqual(args[3]['tamanho_titulo'], 60)
            self.assertEqual(args[1][0]['tamanho_fonte'], 18)
            self.assertIsNone(args[1][1].get('tamanho_fonte'))

    def test_integral_float_normalizes_and_export_boundary_rejects_before_media_work(self):
        self.project.title_font_size = 44.0
        self.project.scenes[0].font_size = 24.0
        data = services.project_data(self.project, self.base)
        self.assertIs(type(data['tamanho_titulo']), int)
        self.assertIs(type(data['cenas'][0]['tamanho_fonte']), int)
        for value in (True, 1.5, float('nan'), '24'):
            for scope in ('title', 'scene'):
                scenes = [{'tamanho_fonte': value if scope == 'scene' else None}]
                settings = {'tamanho_titulo': value if scope == 'title' else None}
                with self.subTest(scope=scope, value=value), \
                        patch.object(motor, 'resolve_tool', side_effect=AssertionError('media work forbidden')), \
                        self.assertRaises(motor.ErroProjeto):
                    motor.export_media('Título', scenes, self.base / 'bad.mp4', settings)


class FontFittingTests(unittest.TestCase):
    @staticmethod
    def measure(value, size):
        return max(len(line) for line in value.split('\n')) * size / 2, len(value.split('\n')) * size

    def test_manual_fitting_wraps_only_words_and_never_shrinks(self):
        value, size = motor.fit_text_pixels('Uma cena longa', 60, 90, 20, self.measure, autofit=False)
        self.assertEqual((value, size), ('Uma\ncena\nlonga', 20))
        with self.assertRaisesRegex(motor.ErroProjeto, 'não cabe.*20.*px'):
            motor.fit_text_pixels('W' * 20, 60, 90, 20, self.measure, autofit=False)
        with self.assertRaisesRegex(motor.ErroProjeto, 'não cabe'):
            motor.fit_text_pixels('Uma cena longa', 60, 30, 20, self.measure, autofit=False)
        value, size = motor.fit_text_pixels('W' * 20, 60, 90, 20, self.measure)
        self.assertEqual(value, 'W' * 20)
        self.assertLess(size, 20)

    def test_preview_scales_explicit_sizes_and_preserves_geometry_number_and_legacy(self):
        from gui_cores import draw_model
        for width, height in ((1920, 1080), (640, 360), (384, 216)):
            scale = width / 1920
            legacy = draw_model('Título', {}, ['Cena', 'Outra'], width, height)
            model = draw_model('Título', {}, ['Cena', 'Outra'], width, height,
                               title_size=75, scene_sizes=[18, None])
            tags = {x['tag']: x for x in model['items']}
            old = {x['tag']: x for x in legacy['items']}
            self.assertEqual(tags['title']['size'], max(1, round(75 * scale)))
            self.assertEqual(tags['name-1']['size'], max(1, round(18 * scale)))
            self.assertEqual(tags['name-2']['size'], old['name-2']['size'])
            self.assertFalse(tags['title']['autofit'])
            self.assertTrue(tags['name-2']['autofit'])
            self.assertEqual(tags['number-1'], old['number-1'])
            self.assertEqual([x['box'] for x in model['items']], [x['box'] for x in legacy['items']])
        tiny = draw_model('Título', {}, ['Cena'], 384, 216, title_size=1, scene_sizes=[1])
        self.assertEqual(next(x['size'] for x in tiny['items'] if x['tag'] == 'name-1'), 1)

    def test_preview_manual_overflow_keeps_text_font_and_warns_without_media(self):
        from gui_cores import ColorPreview
        from tests.test_gui_cores import Canvas, Font, Var
        canvas = Canvas()
        title = Var('W' * 60)
        warning = []
        preview = ColorPreview(canvas, title, {}, lambda: ['Uma cena longa'], font_factory=Font,
                               sizes=lambda: (200, [100]), warning=warning.append)
        self.addCleanup(preview.destroy)
        with patch.object(motor, 'run_media', side_effect=AssertionError('media forbidden')), \
                patch.object(services, 'inspect_media', side_effect=AssertionError('probe forbidden')):
            canvas.flush()
        item = next(x for x in preview.model['items'] if x['tag'] == 'title')
        self.assertEqual(item['text'], 'W' * 60)
        self.assertEqual(item['size'], 40)
        self.assertTrue(item['overflow'])
        self.assertIn('Título', warning[-1])
        self.assertIn('não cabe', warning[-1])

    def test_render_graph_respects_sizes_profiles_and_fixed_number(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            for profile, factor in (('1080p', 1), ('prova', 1/3)):
                scenes = [{'source': work / 'source.mp4', 'nome': 'Cena', 'tamanho_fonte': 36,
                           'previa': {'inicio': 0, 'duracao_efetiva': .1}}]
                settings = {'segundos': .1, 'tamanho_titulo': 60, 'visual': self.palette(), 'musica': None}
                with patch.object(motor, 'measure_text_pixels', side_effect=lambda a,b,c,v,s,t: self.measure(v,s)), \
                        patch.object(motor, 'run_media') as run, \
                        patch.object(motor, 'probe_media', return_value={'streams': [{'nb_read_frames': '3', 'duration': '.1'}]}):
                    motor.render_animated('ffmpeg', work, work, 'Título', scenes, profile, True, settings)
                graph = run.call_args.args[0][run.call_args.args[0].index('-filter_complex') + 1]
                for filename, size in (('titre.txt', 60), ('label-1.txt', 36), ('number-1.txt', 32)):
                    self.assertIn(f'textfile={filename}:expansion=none:fontsize={round(size*factor)}:', graph)

    @staticmethod
    def palette():
        return {'fundo': '#101f33', 'texto': '#f3ead6', 'destaque': '#c7a362'}

    def test_render_refuses_manual_overflow_before_preparing_media_and_validates_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            scene = {'source': work / 'source.mp4', 'nome': 'W' * 80, 'tamanho_fonte': 200,
                     'previa': {'inicio': 0, 'duracao_efetiva': .1}}
            settings = {'segundos': .1, 'visual': self.palette(), 'musica': None}
            with patch.object(motor, 'measure_text_pixels', side_effect=lambda a,b,c,v,s,t: self.measure(v,s)), \
                    patch.object(motor, 'run_media') as run:
                with self.assertRaisesRegex(motor.ErroProjeto, 'Cena 1.*não cabe'):
                    motor.render_animated('ffmpeg', work, work, 'Título', [scene], '1080p', True, settings)
                run.assert_not_called()
                scene['tamanho_fonte'] = True
                with self.assertRaisesRegex(motor.ErroProjeto, 'inteiro'):
                    motor.render_animated('ffmpeg', work, work, 'Título', [scene], '1080p', True, settings)


class FontGuiTests(unittest.TestCase):
    def setUp(self):
        import menu_gui
        from tests.test_gui_janela import Var, WindowLogicTests
        from unittest.mock import Mock
        case = WindowLogicTests()
        case.setUp()
        self.app = app = case.app
        self.module = menu_gui
        app._refreshing = False
        app._font_edited = set()
        app.font_notice_var = Var('')
        app.font_entries = {'title': Mock(), 'scene': Mock()}
        app.color_preview = Mock()
        app.title_font_var, app.scene_font_var = Var('44'), Var('24')
        for scope in ('title', 'scene'):
            getattr(app, f'{scope}_font_var').trace_add('write', lambda *args, s=scope: app.mark_font_edited(s))

    def test_unchanged_focusout_and_refresh_do_not_convert_legacy_to_manual(self):
        app = self.app
        app.finish_font_edit('title')
        app.finish_font_edit('scene')
        self.assertIsNone(app.project.title_font_size)
        self.assertIsNone(app.project.scenes[0].font_size)
        app.load_scene_fields()
        self.assertEqual(app.scene_font_var.get(), '24')
        self.assertEqual(app._font_edited, set())
        self.assertIsNone(app.capture().scenes[0].font_size)

    def test_font_confirmation_preserves_pending_name_trim_duration_and_saved_state(self):
        app = self.app
        saved = copy.deepcopy(app.project)
        app.saved_state = copy.deepcopy(app.project)
        app.duration_seconds_var.set('17')
        app.scene_start_seconds_var.set('invalid pending')
        pending = (app.title_var.get(), app.scene_name_var.get(), app.scene_start_seconds_var.get(),
                   app.duration_seconds_var.get())
        with patch.object(app, 'capture', side_effect=AssertionError('capture forbidden')), \
                patch.object(services, 'inspect_media', side_effect=AssertionError('probe')), \
                patch.object(services, 'render_project', side_effect=AssertionError('render')):
            app.title_font_var.set('55')
            self.assertIsNone(app.project.title_font_size)
            self.assertTrue(app.finish_font_edit('title'))
            app.scene_font_var.set('30')
            self.assertTrue(app.finish_font_edit('scene'))
        self.assertEqual((app.project.title_font_size, app.project.scenes[0].font_size), (55, 30))
        self.assertEqual(app.project.scenes[0].name, saved.scenes[0].name)
        self.assertEqual(app.project.duration, saved.duration)
        self.assertEqual(app.project.scenes[0].start, saved.scenes[0].start)
        self.assertEqual(app.saved_state, saved)
        self.assertNotEqual(app.project, saved)
        self.assertEqual(pending, (app.title_var.get(), app.scene_name_var.get(), app.scene_start_seconds_var.get(),
                                   app.duration_seconds_var.get()))
        self.assertEqual(app.color_preview.request.call_count, 2)

    def test_invalid_temporary_value_is_editable_no_popup_or_model_mutation_then_recovers(self):
        app = self.app
        before = copy.deepcopy(app.project)
        with patch.object(self.module.messagebox, 'showerror') as popup:
            for value in ('', '-', '2.5', 'NaN', '0', '201', 'abc', '１２'):
                app.scene_font_var.set(value)
                popup.assert_not_called()
                self.assertFalse(app.finish_font_edit('scene'))
                self.assertEqual(app.scene_font_var.get(), value)
                self.assertEqual(app.project, before)
                self.assertIn('1 a 200', app.font_notice_var.get())
            popup.assert_not_called()
        app.scene_font_var.set('27')
        self.assertTrue(app.finish_font_edit('scene'))
        self.assertEqual(app.project.scenes[0].font_size, 27)
        self.assertEqual(app.font_notice_var.get(), '')

    def test_unconfirmed_field_blocks_capture_and_confirmed_values_survive_capture(self):
        app = self.app
        app.title_font_var.set('52')
        with self.assertRaisesRegex(motor.ErroProjeto, 'fonte pendente'):
            app.capture()
        self.assertIsNone(app.project.title_font_size)
        app.finish_font_edit('title')
        self.assertEqual(app.capture(validate=True).title_font_size, 52)
        self.assertIsNone(app.capture().scenes[0].font_size)
        self.assertTrue(app.finish_font_edit('scene', force=True))
        self.assertEqual(app.project.scenes[0].font_size, 24)

    def test_disabled_scene_and_binding_contract_spin_enter_focusout(self):
        from unittest.mock import Mock
        app = self.app
        app.editing_index = None
        app.scene_font_var.set('40')
        self.assertFalse(app.finish_font_edit('scene', force=True))
        self.assertIsNone(app.project.scenes[0].font_size)
        app.controls = []
        spin = Mock()
        with patch.object(self.module.ttk, 'Spinbox', return_value=spin) as factory:
            result = app._font_spinbox(Mock(), 'title', app.title_font_var)
        self.assertIs(result, spin)
        options = factory.call_args.kwargs
        self.assertEqual((options['from_'], options['to'], options['increment']), (1, 200, 1))
        events = {call.args[0]: call.args[1] for call in spin.bind.call_args_list}
        self.assertIn('<Return>', events)
        self.assertIn('<FocusOut>', events)
        app.editing_index = 0
        app._font_edited.clear()
        app.title_font_var.set('56')
        events['<Return>'](None)
        self.assertEqual(app.project.title_font_size, 56)
        app.title_font_var.set('57')
        options['command']()
        self.assertEqual(app.project.title_font_size, 57)
        app.title_font_var.set('58')
        events['<FocusOut>'](None)
        self.assertEqual(app.project.title_font_size, 58)


class FontProbeSelectionTests(unittest.TestCase):
    """Doubles de widgets; adopt/refresh e callbacks de fonte continuam reais."""
    setUp = FontGuiTests.setUp

    def prepare_probe(self, selected):
        from tests.test_gui_janela import Var
        from unittest.mock import Mock
        app = self.app
        if selected is None:
            app.project.scenes.clear()
        else:
            app.project.add_video(MediaInfo(Path('/second.mp4'), 10))
            app.project.scenes[0].font_size, app.project.scenes[1].font_size = 19, 41
        app.project_path = None
        app.saved_state = copy.deepcopy(app.project)
        app.last_catalog = None
        app.path_var, app.music_var = Var(''), Var('')
        app.duration_entries = [Mock(), Mock()]
        app.color_swatches = {key: Mock() for key in app.project.colors}
        app.root, app.color_canvas = Mock(), Mock()
        selection = []
        app.tree = Mock()
        app.tree.get_children.return_value = ('0', '1')
        app.tree.delete.side_effect = lambda *items: selection.clear()
        app.tree.selection_set.side_effect = lambda item: selection.__setitem__(slice(None), [item])
        app.tree.selection.side_effect = lambda: tuple(selection)
        app.controls = []
        focused, events = [], []
        for scope in ('title', 'scene'):
            widget = Mock()
            variable = getattr(app, f'{scope}_font_var')
            with patch.object(self.module.ttk, 'Spinbox', return_value=widget) as factory:
                app.font_entries[scope] = app._font_spinbox(Mock(), scope, variable)
            options = factory.call_args.kwargs
            bindings = {call.args[0]: call.args[1] for call in widget.bind.call_args_list}
            widget.cget.side_effect = {'from': options['from_'], 'to': options['to'],
                                      'increment': options['increment']}.__getitem__
            widget.instate.return_value = False

            def event(name, *, scope=scope, variable=variable, bindings=bindings, options=options):
                events.append((scope, name, app.editing_index))
                if name in ('<Up>', '<Down>'):
                    variable.set(str(int(variable.get()) + (1 if name == '<Up>' else -1)))
                    options['command']()
                else:
                    bindings[name](None)

            def focus(*, scope=scope, event=event):
                if focused and focused[0][0] != scope:
                    focused[0][1]('<FocusOut>')
                focused[:] = [(scope, event)]

            widget.event_generate.side_effect = event
            widget.focus_force.side_effect = focus
        app.refresh(selected or 0)
        return events

    def test_probe_events_use_seed_scene_zero_and_finally_restores_original(self):
        from native_color_checks import probe_font_sizes

        class StopBeforeNativeFont(Exception):
            pass

        for selected in (1, 0, None):
            with self.subTest(selected=selected):
                self.setUp()
                events = self.prepare_probe(selected)
                app = self.app
                original, saved = app.project, app.saved_state
                before = copy.deepcopy(original)
                observed = {}

                def panel(current):
                    observed.update(seed=current.project, selected=current.editing_index,
                                    selection=current.tree.selection())

                # Interrompe só na medição Tk; todos os eventos anteriores são do probe.
                # Não fabrica relatório nativo nem substitui validate_font_report.
                with patch('native_color_checks.show_panel', side_effect=panel), \
                        patch('tkinter.font.Font', side_effect=StopBeforeNativeFont), \
                        self.assertRaises(StopBeforeNativeFont):
                    probe_font_sizes(app)
                self.assertIs(app.project, original)
                self.assertEqual(app.project, before)
                self.assertIs(app.saved_state, saved)
                self.assertEqual(app.saved_state, before)
                self.assertEqual(app.editing_index, selected)
                self.assertEqual(app.tree.selection(), () if selected is None else (str(selected),))
                sizes = [scene.font_size for scene in observed['seed'].scenes]
                self.assertEqual(observed['selected'], 0, f'seed sizes={sizes}; events={events}')
                self.assertEqual(observed['selection'], ('0',))
                self.assertEqual(sizes, [31, None])
                self.assertEqual(observed['seed'].title_font_size, 60)
                self.assertEqual(events, [('title', '<Return>', 0), ('title', '<FocusOut>', 0),
                                          ('scene', '<Up>', 0), ('scene', '<Down>', 0),
                                          ('scene', '<Return>', 0), ('scene', '<FocusOut>', 0)])

    def test_probe_finally_restores_original_when_show_panel_fails(self):
        from native_color_checks import probe_font_sizes
        for selected in (1, 0, None):
            with self.subTest(selected=selected):
                self.setUp()
                self.prepare_probe(selected)
                app = self.app
                original, saved = app.project, app.saved_state
                before = copy.deepcopy(original)
                with patch('native_color_checks.show_panel', side_effect=RuntimeError('panel failure')), \
                        self.assertRaisesRegex(RuntimeError, 'panel failure'):
                    probe_font_sizes(app)
                self.assertIs(app.project, original)
                self.assertEqual(app.project, before)
                self.assertIs(app.saved_state, saved)
                self.assertEqual(app.editing_index, selected)
                self.assertEqual(app.tree.selection(), () if selected is None else (str(selected),))


class FontTkTests(unittest.TestCase):
    def test_real_spin_enter_focusout_and_per_scene_values(self):
        import tkinter as tk
        import sys
        import menu_gui
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            if sys.platform == 'win32':
                raise
            self.skipTest(f'Tk real indisponível sem DISPLAY: {exc}')
        app = menu_gui.App(root)
        self.addCleanup(app.shutdown)
        for _ in range(2):
            app.project.add_video(MediaInfo(Path('/synthetic.mp4'), 10))
        app.refresh()
        root.update()
        self.assertEqual((app.title_font_var.get(), app.scene_font_var.get()), ('44', '24'))
        self.assertEqual(float(app.font_entries['title'].cget('increment')), 1)
        app.title_font_var.set('60')
        app.font_entries['title'].focus_force()
        app.font_entries['title'].event_generate('<Return>')
        root.update()
        self.assertEqual(app.project.title_font_size, 60)
        app.scene_font_var.set('33')
        app.font_entries['scene'].event_generate('<FocusOut>')
        root.update()
        self.assertEqual(app.project.scenes[0].font_size, 33)
        app.font_entries['scene'].focus_force()
        root.update()
        app.font_entries['scene'].event_generate('<Up>')
        root.update()
        self.assertEqual(app.project.scenes[0].font_size, 34)
        app.tree.selection_set('1')
        app.select_scene()
        self.assertEqual(app.scene_font_var.get(), '24')
        self.assertIsNone(app.project.scenes[1].font_size)
        from native_color_checks import probe_font_sizes
        report = probe_font_sizes(app)
        self.assertTrue(all(report['checks'].values()))


def native_font_fixture(*, captures=False):
    """Relatório ARTIFICIAL para testes negativos do gate; nunca evidência Tk."""
    return {'schema_version': 1, 'controls': [{'from': 1, 'to': 200, 'increment': 1, 'editable': True} for _ in range(2)],
            'checks': {name: True for name in ('legacy_defaults', 'spin_up', 'spin_down', 'enter', 'focusout',
                        'invalid_preserved', 'pending_preserved', 'independence', 'reorder_remove', 'overflow')},
            'media_calls': 0, 'pixels': [{'tag': 'title', 'reference': 60, 'effective': 12},
                                       {'tag': 'name-1', 'reference': 31, 'effective': 6}],
            'canvas_size': [384, 216],
            'captures': ([{'phase': phase, 'method': 'Win32.GDI.BitBlt', 'width': 600, 'height': 300,
                           'path': f'synthetic-{phase}.png', 'sha256': digest * 64}
                          for phase, digest in (('manual', 'a'), ('overflow', 'b'))] if captures else [])}


class FontNativeGateTests(unittest.TestCase):
    def test_invalid_or_missing_check_reports_its_key_without_weakening_gate(self):
        from native_color_checks import validate_font_report
        valid = native_font_fixture()
        for name in valid['checks']:
            for value in (False, 0, 1, 'True', None):
                bad = copy.deepcopy(valid)
                bad['checks'][name] = value
                with self.subTest(check=name, value=value), \
                        self.assertRaisesRegex(ValueError, r'checks\.' + name):
                    validate_font_report(bad, require_captures=False)
            bad = copy.deepcopy(valid)
            del bad['checks'][name]
            with self.subTest(missing=name), self.assertRaisesRegex(ValueError, r'checks\.' + name):
                validate_font_report(bad, require_captures=False)

    def test_gate_requires_new_font_events_sizes_and_zero_media(self):
        from native_color_checks import validate_font_report
        valid = native_font_fixture()
        validate_font_report(valid, require_captures=False)
        for name in valid['checks']:
            bad = copy.deepcopy(valid)
            bad['checks'][name] = False
            with self.subTest(check=name), self.assertRaises(ValueError):
                validate_font_report(bad, require_captures=False)
        for key, value in (('media_calls', 1), ('pixels', []), ('controls', [])):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_font_report(dict(valid, **{key: value}), require_captures=False)
        with self.assertRaises(ValueError):
            validate_font_report(valid, require_captures=True)

    def test_render_gate_rejects_sizes_graph_and_missing_retained_frame(self):
        from native_render_checks import validate_font_render
        for profile in ('1080p', 'prova'):
            valid = font_render_fixture(profile, 'MP4')
            validate_font_render(valid, profile=profile, export_format='MP4')
            for key, value in (('sizes', {'title': 44, 'scene': 24, 'number': 32}),
                               ('graph', ''), ('composition_exit', 1), ('frame_sha256', ''), ('sample', '')):
                with self.subTest(profile=profile, key=key), self.assertRaises(ValueError):
                    validate_font_render(dict(valid, **{key: value}), profile=profile, export_format='MP4')

    def test_windows_smoke_rejects_missing_font_report(self):
        import smoke_windows
        from test_smoke_windows import NativeSmokeContractTests
        result = NativeSmokeContractTests().valid_result()
        result['platform'] = 'win32'
        result.pop('font_sizes', None)
        with self.assertRaisesRegex(ValueError, 'fonte'):
            smoke_windows.validate_result(result)


class FontRealRenderTests(unittest.TestCase):
    def test_real_manual_overflow_refuses_both_formats_profiles_without_output_or_source_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / 'source.mp4'
            motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg')) + [
                '-f', 'lavfi', '-i', 'testsrc2=size=64x36:rate=30:duration=0.2',
                '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(source)])
            before = motor.sha256(source)
            project = Project(title='Título', duration=.1)
            project.add_video(services.inspect_media(source))
            for fmt in ('MP4', 'AVI'):
                project.export_format = fmt
                for profile in ('1080p', 'prova'):
                    for scope in ('title', 'scene'):
                        project.title = 'W' * 80 if scope == 'title' else 'Título'
                        project.title_font_size = 200 if scope == 'title' else 60
                        project.scenes[0].name = 'W' * 80 if scope == 'scene' else 'Cena'
                        project.scenes[0].font_size = 200 if scope == 'scene' else 36
                        output = base / f'refused-{fmt}-{profile}-{scope}.{fmt.lower()}'
                        with self.subTest(fmt=fmt, profile=profile, scope=scope), \
                                self.assertRaisesRegex(motor.ErroProjeto, 'não cabe.*px'):
                            services.render_project(project, None, output, profile=profile)
                        self.assertFalse(output.exists())
                        self.assertFalse(list(base.glob('.*anisio*')))
            self.assertEqual(motor.sha256(source), before)

    def test_observed_real_proof_avi_keeps_manual_sizes_and_retains_frame(self):
        from native_render_checks import render_font_evidence
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / 'source.mp4'
            motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg')) + [
                '-f', 'lavfi', '-i', 'testsrc2=size=64x36:rate=30:duration=0.2',
                '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(source)])
            project = Project(title='Título', duration=.1, title_font_size=60, export_format='AVI')
            project.add_video(services.inspect_media(source))
            project.scenes[0].name, project.scenes[0].font_size = 'Cena', 36
            output, evidence = render_font_evidence(
                lambda: services.render_project(project, None, base / 'out.avi', profile='prova'),
                base / 'evidence', profile='prova', export_format='AVI')
            self.assertTrue(output.is_file())
            self.assertEqual(evidence['sizes'], {'title': 20, 'scene': 12, 'number': 11})
            self.assertEqual(evidence['composition_exit'], 0)
            self.assertTrue(Path(evidence['frame']).is_file())
            self.assertEqual(motor.sha256(evidence['frame']), evidence['frame_sha256'])


def font_render_fixture(profile, export_format, *, current=False):
    """Double do grafo/retention para gate; não é render real."""
    factor = 1 if profile == '1080p' else 1/3
    sizes = {key: round(size * factor) for key, size in (('title', 60), ('scene', 36), ('number', 32))}
    import font_families as fonts
    pins = {row['id']: row['files'][0]['sha256'] for row in fonts.audit_assets()['families']}
    roles = {}
    graph_parts, measurements = [], []
    for filename, identity, size in (('titre.txt', 'cinzel', sizes['title']),
            ('label-1.txt', 'arimo', sizes['scene']), ('label-2.txt', 'lobster', sizes['scene']),
            ('number-1.txt', None, sizes['number']), ('number-2.txt', None, sizes['number'])):
        if current:
            identity = None
        file = f'family-{identity}.ttf' if identity else 'font.ttf'
        digest = pins[identity] if identity else motor.sha256(motor.find_font(None))
        roles[filename] = {'id': identity, 'file': file, 'sha256': digest, 'text': 'Synthetic', 'size': size,
                           'measured_width': 20, 'measured_height': 10}
        measurements.append({'file': file, 'sha256': digest, 'text': 'Synthetic', 'size': size, 'width': 20, 'height': 10})
        graph_parts.append(f'drawtext=fontfile={file}:textfile={filename}:expansion=none:fontsize={size}:')
    return {'profile': profile, 'format': export_format, 'composition_exit': 0,
            'graph': ','.join(graph_parts), 'sizes': sizes, 'sample': 'synthetic.' + export_format.lower(),
            'font_manifest_sha256': fonts.MANIFEST_SHA256, 'font_roles': roles, 'measurements': measurements,
            'sample_sha256': 'a'*64, 'frame': 'synthetic.png', 'frame_sha256': 'b'*64}



class FontWorkflowTests(unittest.TestCase):
    setUp = FontGuiTests.setUp
    def test_other_font_success_does_not_hide_an_invalid_field(self):
        app = self.app
        app.title_font_var.set('')
        self.assertFalse(app.finish_font_edit('title'))
        app.scene_font_var.set('30')
        self.assertTrue(app.finish_font_edit('scene'))
        self.assertIn('Título', app.font_notice_var.get())
        self.assertIn('title', app._font_edited)
        self.assertIsNone(app.project.title_font_size)

    def test_refresh_selection_reorder_remove_dirty_and_new_without_display(self):
        from tests.test_gui_janela import Var
        from unittest.mock import Mock
        app = self.app
        app.project.add_video(MediaInfo(Path('/second.mp4'), 10))
        app.saved_state = copy.deepcopy(app.project)
        app.project.title_font_size = 55
        app.project.scenes[0].font_size, app.project.scenes[1].font_size = 19, 41
        app.project_path = None
        app.path_var, app.music_var = Var(''), Var('')
        app.duration_entries = [Mock(), Mock()]
        app.scene_duration_var = Var('')
        app.color_swatches = {key: Mock() for key in app.project.colors}
        app.tree = Mock()
        app.tree.get_children.return_value = []
        app.refresh()
        self.assertEqual((app.title_font_var.get(), app.scene_font_var.get()), ('55', '19'))
        for index in (1, 0, 1, 0):
            app.tree.selection.return_value = (str(index),)
            app.select_scene()
            self.assertEqual(app.scene_font_var.get(), str((19, 41)[index]))
        self.assertEqual([s.font_size for s in app.capture().scenes], [19, 41])
        with patch.object(self.module.messagebox, 'askyesno', return_value=False) as notice:
            self.assertFalse(app.confirm_discard())
            notice.assert_called_once()
        app.move_scene(1)
        self.assertEqual([s.font_size for s in app.project.scenes], [41, 19])
        self.assertEqual(app.scene_font_var.get(), '19')
        app.remove_scene()
        self.assertEqual([s.font_size for s in app.project.scenes], [41])
        self.assertEqual(app.scene_font_var.get(), '41')
        with patch.object(self.module.messagebox, 'askyesno', return_value=True):
            app.new_project()
        self.assertEqual((app.title_font_var.get(), app.scene_font_var.get()), ('44', '24'))
        self.assertIsNone(app.project.title_font_size)
        self.assertEqual(app.project.scenes, [])
        self.assertTrue(app.confirm_discard())


class FontLegacyRealTests(unittest.TestCase):
    def test_long_legacy_texts_keep_autofit_after_real_save_reopen(self):
        from native_render_checks import render_font_evidence
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / 'source.mp4'
            motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg')) + [
                '-f', 'lavfi', '-i', 'testsrc2=size=64x36:rate=30:duration=0.2',
                '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(source)])
            project = Project(title='W' * 80, duration=.1)
            project.add_video(services.inspect_media(source))
            project.scenes[0].name = 'W' * 60
            original = services.save_project(project, base / 'old.menu')
            _, before = render_font_evidence(lambda: motor.export_project(original, base / 'before.mp4', 'prova'),
                base / 'before-evidence', profile='prova', export_format='MP4')
            reopened = services.open_project(original)
            saved = services.save_project(reopened, base / 'saved.menu')
            data = json.loads(saved.read_text(encoding='utf-8'))
            self.assertNotIn('tamanho_titulo', data)
            self.assertNotIn('tamanho_fonte', data['cenas'][0])
            _, after = render_font_evidence(lambda: motor.export_project(saved, base / 'after.mp4', 'prova'),
                base / 'after-evidence', profile='prova', export_format='MP4')
            self.assertEqual(before['graph'], after['graph'])
            self.assertLess(after['sizes']['title'], round(44/3))
            self.assertLess(after['sizes']['scene'], 8)
            self.assertEqual(after['texts']['title'].split(), [project.title])
            self.assertEqual(after['texts']['scene'].split(), [project.scenes[0].name])
            reopened.title_font_size = 44
            with self.assertRaisesRegex(motor.ErroProjeto, 'Título.*não cabe'):
                services.render_project(reopened, None, base / 'fixed.mp4', profile='prova')
