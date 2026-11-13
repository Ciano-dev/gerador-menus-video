"""Famílias aprovadas: modelo, fronteiras nativas, assets e render real."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import gui_servicos as services
from gui_autoria import MediaInfo, Project
import menu_anisio as motor

ROOT = Path(__file__).resolve().parents[1]
IDS = ('arimo', 'montserrat', 'oswald', 'merriweather', 'playfairdisplay', 'cinzel', 'dancingscript', 'lobster')


class FamilyModelTests(unittest.TestCase):
    def setUp(self):
        from tests.test_font_size import FontProjectTests
        case = FontProjectTests(); case.setUp()
        self.addCleanup(case.doCleanups)
        self.project, self.base, self.source = case.project, case.base, case.source

    def test_legacy_absence_and_return_to_current_omit_fields(self):
        self.assertIsNone(self.project.title_font_family)
        self.assertIsNone(self.project.scenes[0].font_family)
        self.project.title_font_family = 'arimo'
        self.project.title_font_family = None
        data = services.project_data(self.project, self.base)
        self.assertNotIn('familia_titulo', data)
        self.assertNotIn('familia_fonte', data['cenas'][0])
        self.assertNotIn('tamanho_titulo', data)

    def test_roundtrip_reorder_remove_sizes_and_autofit_independent(self):
        self.project.title_font_family = 'cinzel'
        self.project.scenes[0].font_family = 'arimo'
        self.project.scenes[1].font_family = 'lobster'
        self.project.scenes[0].font_size = 37
        self.project.move_scene(0, 1)
        path = services.save_project(self.project, self.base / 'families.menu')
        with patch.object(services, 'inspect_media', return_value=MediaInfo(self.source, 10)):
            self.assertEqual(services.open_project(path), self.project)
        self.assertIsNone(self.project.title_font_size)
        self.assertEqual([s.font_family for s in self.project.scenes], ['lobster', 'arimo'])
        self.project.edit_scene(1, 'Editada', 1, 2)
        self.project.remove_scene(0)
        self.assertEqual((self.project.scenes[0].font_family, self.project.scenes[0].font_size), ('arimo', 37))

    def test_untrusted_ids_types_rejected_model_json_and_export_before_media(self):
        data = services.project_data(self.project, self.base)
        for value in ('Arial', '../../font.ttf', '', True, 1, [], {}):
            for scope in ('title', 'scene'):
                with self.subTest(value=value, scope=scope):
                    obj, field = ((self.project, 'title_font_family') if scope == 'title'
                                  else (self.project.scenes[0], 'font_family'))
                    setattr(obj, field, value)
                    with self.assertRaisesRegex(motor.ErroProjeto, 'família'):
                        self.project.validate()
                    setattr(obj, field, None)
                    trial = copy.deepcopy(data)
                    if scope == 'title': trial['familia_titulo'] = value
                    else: trial['cenas'][0]['familia_fonte'] = value
                    path = self.base / 'invalid.menu'; path.write_text(json.dumps(trial), encoding='utf-8')
                    with self.assertRaisesRegex(motor.ErroProjeto, 'família'):
                        motor.load_project(path, self.base / 'out.mp4')
                    with patch.object(motor, 'resolve_tool', side_effect=AssertionError('media forbidden')), \
                            self.assertRaisesRegex(motor.ErroProjeto, 'família'):
                        motor.export_media('Título', [{'familia_fonte': value if scope == 'scene' else None}],
                            self.base / 'out.mp4', {'familia_titulo': value if scope == 'title' else None})

    def test_services_pass_family_roles_for_both_formats(self):
        self.project.title_font_family, self.project.scenes[0].font_family = 'cinzel', 'lobster'
        for fmt in ('MP4', 'AVI'):
            self.project.export_format = fmt
            with patch.object(motor, 'export_media', return_value=self.base / ('out.' + fmt.lower())) as render:
                services.render_project(self.project, None, self.base / ('out.' + fmt.lower()))
            self.assertEqual(render.call_args.args[3]['familia_titulo'], 'cinzel')
            self.assertEqual(render.call_args.args[1][0]['familia_fonte'], 'lobster')


class FamilyAssetTests(unittest.TestCase):
    def test_fixed_catalog_pins_licenses_and_source_frozen_paths(self):
        import font_families as fonts
        self.assertEqual(tuple(fonts.FAMILIES), IDS)
        self.assertEqual(len(fonts.OPTIONS), 9)
        manifest = fonts.audit_assets()
        self.assertEqual(len(manifest['families']), 8)
        for identity in IDS:
            path = fonts.font_path(identity)
            self.assertEqual(path, ROOT / 'font_assets' / identity / 'Regular.ttf')
            self.assertTrue(path.with_name('OFL.txt').read_text(encoding='utf-8').strip())
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', '/frozen/resources', create=True):
            self.assertEqual(fonts.resource_root(), Path('/frozen/resources'))

    def test_assets_missing_tampered_and_manifest_changed_block_audit(self):
        import font_families as fonts
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp); shutil.copytree(ROOT / 'font_assets', base / 'font_assets')
            for relative in ('arimo/Regular.ttf', 'lobster/OFL.txt', 'manifest.json'):
                path = base / 'font_assets' / relative; original = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(motor.ErroProjeto, 'font_assets'):
                    fonts.audit_assets(base)
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(motor.ErroProjeto, 'font_assets'):
                    fonts.audit_assets(base)
                path.write_bytes(original)
            fonts.audit_assets(base)

    def test_windows_private_registration_before_tk_family_and_no_silent_fallback(self):
        import font_families as fonts
        events = []
        add = Mock(side_effect=lambda path, flags, reserved: events.append(('register', path, flags)) or 1)
        factory = Mock(side_effect=lambda **kw: events.append(('tk', kw['family'])) or Mock(actual=lambda field: kw['family']))
        with patch.object(fonts, '_windows_add_font', return_value=add), patch.object(fonts.sys, 'platform', 'win32'):
            registry = fonts.PreviewFonts(Mock(), factory=factory, available=lambda: tuple(fonts.FAMILIES.values()))
            result = registry.font('cinzel', size=-20)
            self.assertEqual(result.actual('family'), 'Cinzel')
            self.assertEqual(events[0][0], 'register')
            self.assertEqual(events[0][2], 0x10)
            self.assertEqual(events[1], ('tk', 'Cinzel'))
            factory.side_effect = None; factory.return_value = Mock(actual=lambda field: 'Segoe UI')
            with self.assertRaisesRegex(motor.ErroProjeto, 'fallback'):
                registry.font('arimo', size=-20)

    def test_registration_failure_is_diagnostic_and_never_requests_tk_font(self):
        import font_families as fonts
        factory = Mock()
        with patch.object(fonts, '_windows_add_font', return_value=Mock(return_value=0)), patch.object(fonts.sys, 'platform', 'win32'):
            registry = fonts.PreviewFonts(Mock(), factory=factory, available=lambda: ())
            with self.assertRaisesRegex(motor.ErroProjeto, 'arimo'):
                registry.font('arimo', size=-20)
        factory.assert_not_called()


class FamilyGuiTests(unittest.TestCase):
    def setUp(self):
        from tests.test_font_size import FontGuiTests
        from tests.test_gui_janela import Var
        case = FontGuiTests(); case.setUp(); self.app = case.app
        self.app.family_vars = {'title': Var('Atual (Arial no Windows)'), 'scene': Var('Atual (Arial no Windows)')}
        self.app.family_combos = {'title': Mock(), 'scene': Mock()}
        self.app.preview_fonts = Mock()
        self.app.saved_state = copy.deepcopy(self.app.project)
        self.app.controls = []

    def test_family_event_changes_only_target_preserving_invalid_and_pending(self):
        app = self.app
        app.scene_name_var.set('Nome pendente'); app.scene_start_seconds_var.set('5')
        app.duration_seconds_var.set('17'); app.title_font_var.set('')
        app.finish_font_edit('title')
        before = copy.deepcopy(app.project); saved = copy.deepcopy(app.saved_state)
        app.family_vars['scene'].set('Lobster')
        self.assertTrue(app.finish_family_edit('scene'))
        before.scenes[0].font_family = 'lobster'
        self.assertEqual(app.project, before); self.assertEqual(app.saved_state, saved)
        self.assertEqual((app.scene_name_var.get(), app.scene_start_seconds_var.get(), app.duration_seconds_var.get(), app.title_font_var.get()), ('Nome pendente', '5', '17', ''))
        self.assertIn('title', app._font_edited)
        self.assertIn('Título', app.font_notice_var.get())
        self.assertIsNone(app.project.scenes[0].font_size)
        app.family_vars['scene'].set('Atual (Arial no Windows)')
        self.assertTrue(app.finish_family_edit('scene'))
        self.assertIsNone(app.project.scenes[0].font_family)

    def test_busy_refresh_and_no_scene_guard(self):
        app = self.app; before = copy.deepcopy(app.project)
        app.family_vars['scene'].set('Arimo')
        app.editing_index = None
        self.assertFalse(app.finish_family_edit('scene'))
        app.editing_index = 0; app._refreshing = True
        self.assertFalse(app.finish_family_edit('scene'))
        self.assertEqual(app.project, before)

    def test_combo_readonly_bindings_enter_and_selected(self):
        import menu_gui
        widget = Mock()
        with patch.object(menu_gui.ttk, 'Combobox', return_value=widget) as factory:
            self.app._family_combo(Mock(), 'title')
        self.assertEqual(factory.call_args.kwargs['state'], 'readonly')
        bindings = {c.args[0]: c.args[1] for c in widget.bind.call_args_list}
        for event in ('<<ComboboxSelected>>', '<Return>', '<KP_Enter>'):
            self.app.family_vars['title'].set('Cinzel'); bindings[event](None)
            self.assertEqual(self.app.project.title_font_family, 'cinzel')


class FamilyGraphTests(unittest.TestCase):
    def test_graph_measurement_use_same_pinned_files_per_role_numbers_legacy(self):
        import font_families as fonts
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp); calls = []
            def measure(ffmpeg, directory, font, value, size, spacing):
                calls.append((font, hashlib.sha256((directory / font.split('=')[1]).read_bytes()).hexdigest(), value))
                return len(value)*size/2, size
            shutil.copyfile(motor.find_font(None), work / 'font.ttf')
            scenes = [{'source': work/'source.mp4', 'nome': 'Cena', 'familia_fonte': 'lobster', 'previa': {'inicio': 0, 'duracao_efetiva': .1}}]
            settings = {'segundos': .1, 'visual': {'fundo': '#101f33', 'texto': '#f3ead6', 'destaque': '#c7a362'}, 'musica': None, 'familia_titulo': 'cinzel'}
            with patch.object(motor, 'measure_text_pixels', side_effect=measure), patch.object(motor, 'run_media') as run, patch.object(motor, 'probe_media', return_value={'streams': [{'nb_read_frames':'3','duration':'.1'}]}):
                motor.render_animated('ffmpeg', work, work, 'Título', scenes, 'prova', True, settings)
            graph = run.call_args.args[0][run.call_args.args[0].index('-filter_complex')+1]
            for text, identity in (('Título', 'cinzel'), ('Cena', 'lobster')):
                entry = next(c for c in calls if c[2] == text)
                self.assertEqual(entry[1], motor.sha256(fonts.font_path(identity)))
                self.assertIn('drawtext=' + entry[0] + ':textfile=', graph)
            self.assertIn('drawtext=fontfile=font.ttf:textfile=number-1.txt:', graph)

    def test_all_approved_files_measured_with_real_freetype(self):
        import font_families as fonts
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp); ffmpeg = motor.resolve_tool('ffmpeg'); results = {}
            for identity in IDS:
                shutil.copyfile(fonts.font_path(identity), work/'approved.ttf')
                results[identity] = motor.measure_text_pixels(ffmpeg, work, 'fontfile=approved.ttf', 'Uma noite para recordar', 44, 0)
            self.assertEqual(len(results), 8)
            self.assertGreater(len(set(results.values())), 6)


class FamilyPackageTests(unittest.TestCase):
    def test_package_audit_missing_and_tampered_fonts_or_license(self):
        from tests.test_windows_build import BuildContractTests
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'package'; module = BuildContractTests().package(root)
            module.audit_package(root)
            for relative in ('arimo/Regular.ttf', 'cinzel/OFL.txt', 'manifest.json'):
                path = root/'_internal/font_assets'/relative
                original = path.read_bytes(); path.unlink()
                with self.assertRaisesRegex((ValueError, motor.ErroProjeto), 'font_assets'):
                    module.audit_package(root)
                path.write_bytes(original+b'changed')
                with self.assertRaisesRegex((ValueError, motor.ErroProjeto), 'font_assets'):
                    module.audit_package(root)
                path.write_bytes(original)
            module.audit_package(root)


def family_native_fixture(captures=False):
    """Double de relatório para gates negativos; NUNCA evidência de Tk nativo."""
    import font_families as fonts
    from native_color_checks import FAMILY_CHECKS
    manifest = fonts.audit_assets()
    rows = [{'id': None, 'actual': 'Segoe UI', 'expected': 'Segoe UI', 'file': 'Arial-local.ttf', 'sha256': 'a'*64}]
    for row in manifest['families']:
        rows.append({'id': row['id'], 'actual': fonts.FAMILIES[row['id']], 'expected': fonts.FAMILIES[row['id']],
                     'file': str(fonts.font_path(row['id'])), 'sha256': row['files'][0]['sha256']})
    return {'schema_version': 1, 'manifest_sha256': fonts.MANIFEST_SHA256, 'success': True, 'errors': [], 'checks': {key: True for key in FAMILY_CHECKS},
            'media_calls': 0, 'canvas_size': [384,216],
            'manual_fonts': [{'tag':'title','actual':'Cinzel','reference':60,'effective':12},
                             {'tag':'name-2','actual':'Lobster','reference':31,'effective':6}],
            'families': rows, 'persisted': {'title': 'cinzel', 'scenes': ['arimo', 'lobster']},
            'controls': [{'scope': scope, 'values': list(fonts.OPTIONS), 'readonly': True,
                          'bounds': [0,0,200,30], 'parent': [0,0,600,40], 'text_width': 170, 'requested_width': 200}
                         for scope in ('title', 'scene')],
            'captures': [{'phase': scope, 'method': 'Win32.GDI.BitBlt', 'path': scope+'.png', 'sha256': str(i)*64,
                          'width': 600, 'height': 300} for i,scope in enumerate(('title','scene'),1)] if captures else []}


class FamilyNativeGateTests(unittest.TestCase):
    def test_native_gate_rejects_each_failed_scalar_check_and_fallback(self):
        from native_color_checks import validate_family_report
        valid = family_native_fixture()
        validate_family_report(valid, require_captures=False)
        for key in valid['checks']:
            for value in (False, 0, 1, None, 'True'):
                bad = copy.deepcopy(valid); bad['checks'][key] = value
                with self.subTest(check=key, value=value), self.assertRaisesRegex(ValueError, 'checks.'+key):
                    validate_family_report(bad, require_captures=False)
        for actual in ('Times New Roman', 'Segoe UI'):
            bad=copy.deepcopy(valid); bad['families'][1]['actual']=actual
            with self.assertRaisesRegex(ValueError, 'families.arimo.actual'):
                validate_family_report(bad, require_captures=False)

    def test_native_gate_rejects_missing_pin_clipped_combo_and_missing_capture(self):
        from native_color_checks import validate_family_report
        valid=family_native_fixture()
        for field, value in (('sha256', 'a'*64), ('file', ''), ('id','unknown')):
            bad=copy.deepcopy(valid);bad['families'][1][field]=value
            with self.assertRaises(ValueError): validate_family_report(bad, require_captures=False)
        bad=copy.deepcopy(valid);bad['controls'][0]['bounds']=[550,0,200,30]
        with self.assertRaises(ValueError): validate_family_report(bad, require_captures=False)
        with self.assertRaises(ValueError): validate_family_report(valid, require_captures=True)


class StopFamilyNativeMeasurement(Exception):
    """O modelo para antes de medir Tk; doubles não geram evidência nativa."""


class FamilyProbeFocusTests(unittest.TestCase):
    def prepare_probe(self, selected=0):
        from tests.test_font_size import FontProbeSelectionTests
        from tests.test_gui_janela import Var
        import font_families as fonts
        case = FontProbeSelectionTests(); case.setUp(); case.prepare_probe(0)
        app = self.app = case.app
        app.editing_index = selected
        app.family_vars = {scope: Var(fonts.CURRENT) for scope in ('title', 'scene')}
        app.family_combos = {}
        app.preview_fonts = Mock()
        app.preview_fonts.records = {}
        app.ui_font = ('Double UI', 10)
        self.events, self.edits = [], []
        focused, requested, bindings = [Mock(name='other-panel-widget')], [], {}
        self.focused = focused
        app.root.focus_get.side_effect = lambda: focused[0]

        def update():
            if requested:
                target = requested.pop()
                previous, focused[0] = focused[0], target
                if previous is not target:
                    callback = bindings.get(previous, {}).get('<FocusOut>')
                    if callback:
                        callback(None)
        app.root.update.side_effect = update

        def wire(widget, name):
            callbacks = bindings[widget] = {call.args[0]: call.args[1] for call in widget.bind.call_args_list}
            states = set()
            def state(changes=()):
                for change in changes:
                    if change.startswith('!'):
                        states.discard(change[1:])
                    else:
                        states.add(change)
                return tuple(sorted(states))
            widget.state.side_effect = state
            widget.instate.side_effect = lambda query: all(
                value[1:] not in states if value.startswith('!') else value in states for value in query)
            widget.focus_force.side_effect = lambda: requested.append(widget)
            def event(event_name):
                # Eventos virtuais não dependem de foco; teclado depende.
                delivered = event_name.startswith('<<') or focused[0] is widget
                self.events.append((name, event_name, delivered, app.editing_index))
                if delivered:
                    callbacks[event_name](None)
            widget.event_generate.side_effect = event

        for scope in ('title', 'scene'):
            wire(app.font_entries[scope], 'size.' + scope)
            widget = Mock()
            with patch.object(case.module.ttk, 'Combobox', return_value=widget):
                app._family_combo(Mock(), scope)
            wire(widget, 'family.' + scope)
            variable = getattr(app, scope + '_font_var')
            def edit(*args, scope=scope, variable=variable):
                self.edits.append((scope, variable.get(), focused[0] is app.font_entries[scope]))
            variable.trace_add('write', edit)
        app.color_canvas.itemcget.side_effect = lambda tag, option: tag
        app.color_canvas.winfo_width.return_value = 384
        app.color_canvas.winfo_height.return_value = 216
        self.observed = {}
        def font(**kwargs):
            tag = kwargs['name']
            identity = (app.project.title_font_family if tag == 'title' else
                        app.project.scenes[int(tag[5:])-1].font_family if tag.startswith('name-') else None)
            def measure(option):
                self.observed.update(project=copy.deepcopy(app.project), edited=set(app._font_edited),
                                     title=app.title_font_var.get(), scene=app.scene_font_var.get())
                raise StopFamilyNativeMeasurement('limite entre modelo e medição Tk real')
            return Mock(actual=lambda option: fonts.FAMILIES[identity] if identity else 'Legacy double', cget=measure)
        return font

    def test_unfocused_keyboard_is_dropped_and_never_confirms_invalid_size(self):
        self.prepare_probe()
        app = self.app
        app.scene_font_var.set('')
        app.font_entries['scene'].event_generate('<Return>'); app.root.update()
        self.assertEqual(self.events[-1], ('size.scene', '<Return>', False, 0))
        self.assertEqual(app.scene_font_var.get(), '')
        self.assertIn('scene', app._font_edited)
        self.assertEqual(app.font_notice_var.get(), '')
        app.family_combos['title'].focus_force(); app.root.update()
        self.assertEqual(app.font_notice_var.get(), '')
        app.font_entries['scene'].focus_force(); app.root.update()
        app.font_entries['scene'].event_generate('<Return>'); app.root.update()
        self.assertTrue(app.font_notice_var.get())
        self.assertEqual(app.project.scenes[0].font_size, 19)

    def test_probe_focuses_each_edit_and_return_preserves_pending_and_restores(self):
        from native_color_checks import probe_font_families
        for selected in (0, 1, None):
            with self.subTest(selected=selected):
                font = self.prepare_probe(selected)
                app = self.app
                original, saved = app.project, app.saved_state
                before = copy.deepcopy(original)
                with tempfile.TemporaryDirectory() as tmp, patch('native_color_checks.show_panel'), \
                        patch('tkinter.font.Font', side_effect=font):
                    try:
                        with self.assertRaises(StopFamilyNativeMeasurement):
                            probe_font_families(app, Path(tmp))
                        self.assertEqual(json.loads((Path(tmp) / 'font-families.json').read_text(encoding='utf-8')),
                                         app.last_family_report)
                    finally:
                        self.assertIs(app.project, original); self.assertEqual(app.project, before)
                        self.assertIs(app.saved_state, saved); self.assertEqual(app.editing_index, selected)
                report = app.last_family_report
                for key in ('invalid_prepared', 'invalid_preserved', 'pending_preserved', 'sizes_preserved', 'independence',
                            'current_restored', 'selected_event', 'enter_event', 'preview_roles'):
                    self.assertIs(report['checks'][key], True, key)
                self.assertFalse(report['success'])
                self.assertEqual(set(report['invalid_states']), {'before_families', 'after_families'})
                for state in report['invalid_states'].values():
                    self.assertEqual(state['entry'], '')
                    self.assertIs(state['edited'], True); self.assertIs(state['notice_present'], True)
                    self.assertIs(state['widget_invalid'], True); self.assertIsNone(state['model_size'])
                    self.assertGreater(state['notice_length'], 0)
                self.assertTrue(all(delivered for _, _, delivered, _ in self.events))
                self.assertEqual([row for row in self.events if row[0].startswith('size.')],
                                 [('size.scene', '<Return>', True, 0), ('size.title', '<Return>', True, 1),
                                  ('size.scene', '<Return>', True, 1)])
                for scope, value in (('scene', ''), ('title', '60'), ('scene', '31')):
                    self.assertIn((scope, value, True), self.edits)
                project = self.observed['project']
                self.assertEqual((project.title_font_family, project.title_font_size), ('cinzel', 60))
                self.assertEqual([(s.font_family, s.font_size) for s in project.scenes], [('arimo', None), ('lobster', 31)])
                self.assertEqual(self.observed['edited'], set())

    def test_failed_focus_stops_before_each_size_edit_and_restores(self):
        from native_color_checks import probe_font_families
        for scope, attempt, value in (('scene', 1, ''), ('title', 1, '60'), ('scene', 2, '31')):
            with self.subTest(scope=scope, attempt=attempt):
                font = self.prepare_probe(1)
                app = self.app
                original, saved = app.project, app.saved_state
                widget = app.font_entries[scope]
                request = widget.focus_force.side_effect
                attempts = []
                def deny_focus():
                    attempts.append(None)
                    if len(attempts) != attempt:
                        request()
                widget.focus_force.side_effect = deny_focus
                with tempfile.TemporaryDirectory() as tmp, patch('native_color_checks.show_panel'), \
                        patch('tkinter.font.Font', side_effect=font):
                    with self.assertRaisesRegex(ValueError, 'checks.focus.*"matches": false'):
                        probe_font_families(app, Path(tmp))
                    report = json.loads((Path(tmp) / 'font-families.json').read_text(encoding='utf-8'))
                self.assertEqual(report['focus_events'][-1]['target'], 'size.' + scope)
                self.assertIs(report['checks']['focus'], False)
                self.assertFalse(any(s == scope and v == value for s, v, _ in self.edits))
                self.assertIs(app.project, original); self.assertIs(app.saved_state, saved)
                self.assertEqual(app.editing_index, 1)

    def test_invalid_setup_failure_is_reported_before_family_loop(self):
        from native_color_checks import probe_font_families
        font = self.prepare_probe()
        app = self.app
        original, saved = app.project, app.saved_state
        # Mesmo com foco, simula confirmação perdida para testar a pré-condição.
        app.font_entries['scene'].event_generate.side_effect = lambda event: None
        with tempfile.TemporaryDirectory() as tmp, patch('native_color_checks.show_panel'), \
                patch('tkinter.font.Font', side_effect=font):
            with self.assertRaisesRegex(ValueError, 'checks.invalid_prepared.*"notice_present": false'):
                probe_font_families(app, Path(tmp))
            report = json.loads((Path(tmp) / 'font-families.json').read_text(encoding='utf-8'))
        self.assertEqual(report['families'], [])
        self.assertEqual(report['invalid_states']['before_families'],
                         {'entry': '', 'edited': True, 'notice_present': False, 'notice_length': 0,
                          'widget_invalid': False, 'model_size': None})
        self.assertIs(app.project, original); self.assertIs(app.saved_state, saved)
        self.assertEqual(app.editing_index, 0)

    def test_each_invalid_subcondition_still_blocks_after_family_changes(self):
        from native_color_checks import probe_font_families
        mutations = {'entry': lambda app: app.scene_font_var.set('24'),
                     'edited': lambda app: app._font_edited.discard('scene'),
                     'notice_present': lambda app: app.font_notice_var.set('')}
        for key, mutate in mutations.items():
            with self.subTest(key=key):
                font = self.prepare_probe(None)
                app = self.app
                original, saved = app.project, app.saved_state
                finish = app.finish_family_edit
                def change(scope):
                    result = finish(scope)
                    if scope == 'scene':
                        mutate(app)
                    return result
                app.finish_family_edit = change
                with tempfile.TemporaryDirectory() as tmp, patch('native_color_checks.show_panel'), \
                        patch('tkinter.font.Font', side_effect=font):
                    with self.assertRaisesRegex(ValueError, 'checks.invalid_preserved.*"' + key + '"'):
                        probe_font_families(app, Path(tmp))
                    report = json.loads((Path(tmp) / 'font-families.json').read_text(encoding='utf-8'))
                self.assertEqual(len(report['families']), 9)
                self.assertIs(report['checks']['invalid_prepared'], True)
                self.assertIs(report['checks']['invalid_preserved'], False)
                state = report['invalid_states']['after_families']
                self.assertTrue(all(type(value) in (str, bool, int, type(None)) for value in state.values()))
                self.assertNotEqual(state[key], '' if key == 'entry' else True)
                self.assertIs(app.project, original); self.assertIs(app.saved_state, saved)
                self.assertIsNone(app.editing_index)


@unittest.skipUnless(sys.platform == 'win32', 'Registro e captura Tk nativos exigem Windows; sem DISPLAY artificial.')
class FamilyNativeWindowsTests(unittest.TestCase):
    def test_nine_families_real_events_focus_pending_persistence_and_capture(self):
        import os
        import tkinter as tk
        from menu_gui import App
        from native_color_checks import probe_font_families, validate_family_report
        with tempfile.TemporaryDirectory(prefix='families-native-') as tmp:
            base=Path(tmp); source=base/'synthetic.mp4'
            motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg'))+[
                '-f','lavfi','-i','testsrc2=size=64x36:rate=30:duration=0.2',
                '-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(source)])
            app=App(tk.Tk()); self.addCleanup(app.shutdown)
            app.project.add_video(services.inspect_media(source));app.refresh(0)
            original,selected=app.project,app.editing_index
            directory=Path(os.environ.get('ANISIO_NATIVE_EVIDENCE_DIR',tmp))/'font-families'
            report=probe_font_families(app,directory,capture=True)
            validate_family_report(report,require_captures=True)
            self.assertIs(app.project,original);self.assertEqual(app.editing_index,selected)
            self.assertEqual(json.loads((directory/'font-families.json').read_text(encoding='utf-8')),report)


class FamilyRenderEvidenceTests(unittest.TestCase):
    def test_render_gate_rejects_wrong_file_pin_body_and_role_graph(self):
        from native_render_checks import validate_family_render
        from tests.test_font_size import font_render_fixture
        valid=font_render_fixture('prova','MP4')
        validate_family_render(valid)
        for field,value in (('id','lobster'),('sha256','a'*64),('text',''),('measured_width',0)):
            bad=copy.deepcopy(valid);bad['font_roles']['titre.txt'][field]=value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError,'titre.txt'):
                validate_family_render(bad)
        bad=copy.deepcopy(valid);bad['graph']=bad['graph'].replace('family-cinzel.ttf','font.ttf')
        with self.assertRaisesRegex(ValueError,'titre.txt'): validate_family_render(bad)



def family_samples_fixture():
    """Relatório artificial para testes de contrato; não demonstra FreeType real."""
    import font_families as fonts
    pins={r['id']:r['files'][0]['sha256'] for r in fonts.audit_assets()['families']}
    return {'manifest_sha256':fonts.MANIFEST_SHA256,'samples':[
        {'id':identity,'file':str(fonts.font_path(identity) if identity else motor.find_font(None)),
         'sha256':pins[identity] if identity else motor.sha256(motor.find_font(None)),
         'text':'Uma noite para recordar','size':44,'measured_width':400,'measured_height':44,
         'frame':'synthetic.png','frame_sha256':f'{i:064x}','exit_code':0}
        for i,identity in enumerate((None,*fonts.FAMILIES),1)]}


class FamilyAdditionalRegressionTests(unittest.TestCase):
    def test_partial_native_report_on_failure_restores_project_and_none_selection(self):
        from types import SimpleNamespace
        from native_color_checks import probe_font_families
        from gui_autoria import Scene
        original=Project(scenes=[Scene(Path('synthetic.mp4'),12,'Original')])
        saved=copy.deepcopy(original)
        app=SimpleNamespace(project=original, saved_state=saved, editing_index=None, root=Mock(),
                            tree=Mock(), refresh=Mock(), load_scene_fields=Mock())
        app.tree.selection.return_value=()
        app.adopt=lambda seed:setattr(app,'project',seed)
        with tempfile.TemporaryDirectory() as tmp, patch('native_color_checks.show_panel',side_effect=RuntimeError('panel failure')):
            with self.assertRaisesRegex(RuntimeError,'panel failure'):
                probe_font_families(app,Path(tmp))
            report=json.loads((Path(tmp)/'font-families.json').read_text(encoding='utf-8'))
            self.assertFalse(report['success']);self.assertIn('panel failure',report['errors'][0])
            self.assertIs(app.project,original);self.assertIs(app.saved_state,saved)
            self.assertIsNone(app.editing_index)

    def test_freetype_samples_gate_rejects_pin_body_and_duplicate_frame(self):
        from native_render_checks import validate_family_samples
        valid=family_samples_fixture();validate_family_samples(valid)
        for field,value in (('sha256','0'*64),('text',''),('measured_width',0),('exit_code',True)):
            bad=copy.deepcopy(valid);bad['samples'][1][field]=value
            with self.assertRaisesRegex(ValueError,'samples.arimo'):
                validate_family_samples(bad)
        bad=copy.deepcopy(valid);bad['samples'][1]['frame_sha256']=bad['samples'][2]['frame_sha256']
        with self.assertRaisesRegex(ValueError,'distinct_frames'):
            validate_family_samples(bad)

    def test_explicit_cli_file_overrides_families_and_keeps_number_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp);shutil.copyfile(motor.find_font(None),work/'font.ttf')
            scene={'source':work/'source.mp4','nome':'Cena','familia_fonte':'lobster',
                   'previa':{'inicio':0,'duracao_efetiva':.1}}
            settings={'segundos':.1,'visual':{'fundo':'#101f33','texto':'#f3ead6','destaque':'#c7a362'},
                      'musica':None,'familia_titulo':'cinzel'}
            with patch.object(motor,'measure_text_pixels',return_value=(20,10)) as measured, \
                    patch.object(motor,'font_path',side_effect=AssertionError('explicit precedence')), \
                    patch.object(motor,'run_media') as run, \
                    patch.object(motor,'probe_media',return_value={'streams':[{'nb_read_frames':'3','duration':'.1'}]}):
                motor.render_animated('ffmpeg',work,work,'Título',[scene],'prova',True,settings,font_override=True)
            self.assertTrue(all(c.args[2]=='fontfile=font.ttf' for c in measured.call_args_list))
            graph=run.call_args.args[0][run.call_args.args[0].index('-filter_complex')+1]
            self.assertNotIn('family-',graph)


class FamilyResourceAndPreviewTests(unittest.TestCase):
    def test_frozen_uses_own_pinned_assets_and_never_source_fallback(self):
        import font_families as fonts
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);shutil.copytree(ROOT/'font_assets',root/'font_assets')
            with patch.object(sys,'frozen',True,create=True), patch.object(sys,'_MEIPASS',str(root),create=True):
                self.assertEqual(fonts.font_path('arimo'),root/'font_assets/arimo/Regular.ttf')
                (root/'font_assets/arimo/Regular.ttf').unlink()
                with self.assertRaisesRegex(motor.ErroProjeto,'font_assets/arimo/Regular.ttf'):
                    fonts.font_path('arimo')

    def test_preview_assigns_only_title_and_names_families_without_changing_sizes(self):
        from tests.test_gui_cores import Canvas, Font, Var
        from gui_cores import ColorPreview
        canvas=Canvas(); registry=Mock(); calls=[]
        def selected(identity,**kw):
            calls.append((identity,kw['size']));return Font(**kw)
        registry.font.side_effect=selected
        preview=ColorPreview(canvas,Var('Título'),{},lambda:['Cena','Outra'],font_factory=Font,
            families=lambda:('cinzel',['arimo','lobster']),preview_fonts=registry,
            sizes=lambda:(None,[None,31]))
        canvas.flush()
        self.assertEqual({identity for identity,_ in calls},{'cinzel','arimo','lobster'})
        items={item['tag']:item for item in preview.model['items']}
        self.assertTrue(items['title']['autofit']);self.assertTrue(items['name-1']['autofit'])
        self.assertFalse(items['name-2']['autofit'])
        self.assertIsNone(items['number-1'].get('font_family'))

    def test_family_restore_refresh_reorder_remove_and_empty_controls(self):
        from tests.test_font_size import FontWorkflowTests
        from tests.test_gui_janela import Var
        case=FontWorkflowTests();case.setUp();app=case.app
        app.family_vars={'title':Var(''),'scene':Var('')};app.family_combos={'title':Mock(),'scene':Mock()}
        app.project.add_video(MediaInfo(Path('/second.mp4'),10))
        app.project.title_font_family='cinzel';app.project.scenes[0].font_family='arimo';app.project.scenes[1].font_family='lobster'
        app.project_path=None;app.path_var=Var('');app.music_var=Var('');app.duration_entries=[Mock(),Mock()]
        app.color_swatches={key:Mock() for key in app.project.colors};app.tree=Mock();app.tree.get_children.return_value=[]
        app.refresh(1)
        self.assertEqual((app.family_vars['title'].get(),app.family_vars['scene'].get()),('Cinzel','Lobster'))
        app.move_scene(-1)
        self.assertEqual([s.font_family for s in app.project.scenes],['lobster','arimo'])
        self.assertEqual(app.family_vars['scene'].get(),'Lobster')
        app.remove_scene();self.assertEqual(app.family_vars['scene'].get(),'Arimo')
        app.project.scenes=[];app.refresh()
        app.family_combos['scene'].state.assert_called_with(['disabled','!invalid'])
