"""Contrato de duração explícita, tempos visíveis e tipografia por pixels."""
import copy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import menu_gui
from gui_autoria import MediaInfo
from menu_anisio import ErroProjeto
from tests import test_gui_janela as window_tests


def duration_app():
    case = window_tests.WindowLogicTests()
    case.setUp()
    app = case.app
    app.duration_seconds_var.set('2')
    app.duration_entries = [Mock(), Mock()]
    app.status_var = Mock()
    app._refreshing = False
    return app, case.jobs


class ExplicitDurationTests(unittest.TestCase):
    def test_long_menu_notice_compares_only_visible_seconds(self):
        app, _ = duration_app()
        app.project.duration = 360.25
        with patch.object(menu_gui.messagebox, 'askyesno', return_value=True) as notice:
            self.assertTrue(app.confirm_duration(app.project))
            notice.assert_not_called()
            for duration in (361.1, 361.25):
                app.project.duration = duration
                self.assertTrue(app.confirm_duration(app.project))
            notice.assert_called_once()
            self.assertEqual(app.project.duration, 361.25)

    def test_pending_duration_blocks_snapshot_preview_export_without_work(self):
        app, jobs = duration_app()
        app.duration_seconds_var.set('4')
        before = copy.deepcopy(app.project)
        with patch.object(menu_gui.filedialog, 'asksaveasfilename') as dialog:
            for action in (app.capture, app.make_preview, app.make_export):
                with self.subTest(action=action.__name__), self.assertRaisesRegex(
                        ErroProjeto, 'Aplicar duração'):
                    action()
            dialog.assert_not_called()
        self.assertEqual(jobs, [])
        self.assertEqual(app.project, before)

    def test_apply_commits_duration_even_without_music(self):
        app, _ = duration_app()
        app.duration_seconds_var.set('4')
        app.finish_duration_edit()
        self.assertEqual(app.project.duration, 4)
        self.assertEqual(app.capture(skip_scene=True).duration, 4)

    def test_warning_only_on_apply_and_compares_visible_seconds(self):
        app, _ = duration_app()
        app.project.music = MediaInfo(Path('/local/faixa.wav'), 2.25)
        with patch.object(menu_gui.messagebox, 'showinfo') as info:
            app.update_duration_mode()
            info.assert_not_called()
            app.finish_duration_edit()
            info.assert_not_called()
            app.duration_seconds_var.set('3')
            app.finish_duration_edit()
            self.assertIn('sem som', info.call_args.args[1])
            app.duration_seconds_var.set('1')
            app.finish_duration_edit()
            self.assertIn('cortada', info.call_args.args[1])
            self.assertEqual(info.call_count, 2)

    def test_automatic_and_manual_keep_actual_track_duration_and_return(self):
        app, _ = duration_app()
        app.project.music = MediaInfo(Path('/local/faixa.wav'), 120.25)
        app.duration_mode_var.set('musica')
        app.update_duration_mode()
        self.assertEqual(app.project.duration_mode, 'musica')
        self.assertEqual(app.capture(skip_scene=True).effective_duration(), 120.25)
        app.duration_mode_var.set('manual')
        app.update_duration_mode()
        self.assertEqual((app.project.duration_mode, app.project.duration), ('manual', 2))
        app.duration_minutes_var.set('2')
        app.duration_seconds_var.set('5')
        with patch.object(menu_gui.messagebox, 'showinfo'):
            app.finish_duration_edit()
        self.assertEqual(app.project.duration, 125)
        app.duration_mode_var.set('musica')
        app.update_duration_mode()
        self.assertEqual(app.project.effective_duration(), 120.25)


class IntegerTimeTests(unittest.TestCase):
    def app(self):
        from tests.test_gui_janela import Var
        app, _ = duration_app()
        app.scene_start_hours_var = Var('0')
        app.scene_end_hours_var = Var('0')
        app.scene_path_var = Var('')
        app.scene_start_seconds_var.set('1')
        app.scene_end_seconds_var.set('3')
        return app

    def test_display_rounds_whole_seconds_and_has_unbounded_hours(self):
        for value, hours, expected in ((3599, False, '59:59'), (3600, True, '01:00:00'),
                (3610, True, '01:00:10'), (1.7, False, '00:02'),
                (360000.25, True, '100:00:00')):
            with self.subTest(value=value):
                self.assertEqual(menu_gui.App.format_time(value, hours=hours), expected)
        self.assertEqual(menu_gui.App.split_time(120.25), ('2', '0'))

    def test_fractional_untouched_menu_and_one_frame_survive_name_edit_and_reload(self):
        app = self.app()
        scene = app.project.scenes[0]
        scene.source_duration, scene.start, scene.duration = 180 + 1/30, 180, 1/30
        app.project.duration = 2.25
        app.load_scene_fields()
        self.assertEqual(app.scene_end_seconds_var.get(), '0')
        app.scene_name_var.set('Renomeado')
        for _ in range(3):
            captured = app.capture(validate=True)
            self.assertEqual((captured.scenes[0].start, captured.scenes[0].duration), (180, 1/30))
            self.assertEqual(captured.duration, 2.25)
            app.project = captured
            app.load_scene_fields()

    def test_editing_only_start_preserves_real_end_and_rounding_end_clamps_to_source(self):
        app = self.app()
        scene = app.project.scenes[0]
        scene.source_duration, scene.start, scene.duration = 3.8, .25, 2
        app.load_scene_fields()
        app.scene_start_seconds_var.set('1')
        captured = app.capture(validate=True).scenes[0]
        self.assertEqual((captured.start, captured.duration), (1, 1.25))
        app.scene_end_seconds_var.set('4')
        captured = app.capture(validate=True).scenes[0]
        self.assertEqual((captured.start, captured.duration), (1, 2.8))
        app.scene_end_seconds_var.set('5')
        with self.assertRaisesRegex(ErroProjeto, 'ultrapassa'):
            app.capture(validate=True)

    def test_integer_fields_ranges_and_menu_minutes_over_59(self):
        app = self.app()
        from tests.test_gui_janela import Var
        for hours, minutes, seconds in (('-1', '0', '0'), ('1.5', '0', '0'),
                ('0', '60', '0'), ('0', '0', '60'), ('0', '0', '.5')):
            with self.subTest(h=hours, m=minutes, s=seconds), self.assertRaises(ErroProjeto):
                app.read_time(Var(minutes), Var(seconds), 'Início', hours=Var(hours))
        self.assertEqual(app.read_time(Var('59'), Var('59'), 'Fim', hours=Var('100')), 363599)
        self.assertEqual(app.read_time(Var('61'), Var('2'), 'Duração do menu'), 3662)
        app.duration_seconds_var.set('2,5')
        with self.assertRaises(ErroProjeto):
            app.finish_duration_edit()

    def test_list_all_three_columns_use_source_hour_context(self):
        app = self.app()
        app.title_var = Mock()
        app.footer_var = Mock()
        app.path_var = Mock()
        app.music_var = Mock()
        app.color_swatches = {key: Mock() for key in app.project.colors}
        app.tree = Mock()
        app.tree.get_children.return_value = []
        app.project_path = None
        for source_duration, expected in ((3599, ('00:01', '00:03', '59:59')),
                (3600, ('00:00:01', '00:00:03', '01:00:00')),
                (3610, ('00:00:01', '00:00:03', '01:00:10'))):
            app.project.scenes[0].source_duration = source_duration
            app.project.scenes[0].start = 1
            app.refresh()
            self.assertEqual(app.tree.insert.call_args.kwargs['values'][3:], expected)


class RealTypographyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile
        import menu_anisio as motor
        cls.motor = motor
        temporary = tempfile.TemporaryDirectory(prefix='tipografia-pixels-')
        cls.addClassCleanup(temporary.cleanup)
        cls.work = Path(temporary.name)
        cls.ffmpeg = motor.resolve_tool('ffmpeg')
        font = motor.find_font(None)
        if font:
            shutil.copyfile(font, cls.work / 'font.ttf')
        cls.font = 'fontfile=font.ttf' if font else 'font=sans-serif'

    def measure(self, text, size):
        return self.motor.measure_text_pixels(self.ffmpeg, self.work, self.font, text, size, 5)

    def test_real_font_width_depends_on_glyphs_and_keeps_medium_name_on_one_line(self):
        self.assertGreater(self.measure('MMMMM', 24)[0], self.measure('iiiii', 24)[0] * 2)
        text, size = self.motor.fit_text_pixels('Cerimônia e Festa', 348, 168, 24, self.measure)
        self.assertEqual((text, size), ('Cerimônia e Festa', 24))
        self.assertLessEqual(self.measure(text, size)[0], 348)

    def test_spaces_only_long_word_explicitly_shrinks_and_all_text_fits(self):
        for value in ('Cerimônia especial com toda a família reunida para celebrar',
                      'W' * 60, 'Ação 100% "João" %{eif:1:d} · Ω'):
            with self.subTest(value=value):
                text, size = self.motor.fit_text_pixels(value, 348, 168, 24, self.measure)
                self.assertEqual(text.split(), value.split())
                width, height = self.measure(text, size)
                self.assertLessEqual(width, 348)
                self.assertLessEqual(height, 168)
                if ' ' not in value:
                    self.assertEqual(text, value)
                    self.assertLess(size, 24)

    def test_drawtext_uses_real_width_and_requested_sizes_at_both_profiles(self):
        for profile, factor in (('1080p', 1), ('prova', 1/3)):
            def measure(ffmpeg, work, font, value, size, spacing):
                # A seam for graph inspection; real FreeType is tested above.
                return len(value) * size / 2, size
            scene = {'source': self.work / 'source.mp4', 'nome': 'Cerimônia e Festa',
                     'previa': {'inicio': 0, 'duracao_efetiva': .1}}
            settings = {'segundos': .1, 'visual': {'fundo': '#101f33', 'texto': '#f3ead6',
                        'destaque': '#c7a362'}, 'musica': None, 'exibir_rodape': False}
            with patch.object(self.motor, 'measure_text_pixels', side_effect=measure), \
                    patch.object(self.motor, 'run_media') as run, \
                    patch.object(self.motor, 'probe_media', return_value={'streams': [
                        {'nb_read_frames': '3', 'duration': '.1'}]}):
                self.motor.render_animated(self.ffmpeg, self.work, self.work, 'Título',
                                           [scene], profile, True, settings)
            graph = run.call_args.args[0][run.call_args.args[0].index('-filter_complex') + 1]
            self.assertIn(f'textfile=titre.txt:expansion=none:fontsize={round(44*factor)}:', graph)
            self.assertIn(f'textfile=number-1.txt:expansion=none:fontsize={round(32*factor)}:', graph)
            self.assertEqual((self.work / 'label-1.txt').read_text(encoding='utf-8'), 'Cerimônia e Festa')
            self.assertIn('fontcolor=0xc7a362', graph)


    def test_natural_multiline_render_has_measured_bounds_and_ten_pixel_number_gap(self):
        import re
        source = self.work / 'natural-source.mp4'
        self.motor.run_media(self.motor.ffmpeg_base(self.ffmpeg) + [
            '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=30:duration=0.2',
            '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(source)])
        before = self.motor.sha256(source)
        original = self.motor.run_media
        for profile, factor in (('1080p', 1), ('prova', 1/3)):
            graphs = []
            def observe(command, **kwargs):
                if '-filter_complex' in command:
                    graphs.append(command[command.index('-filter_complex')+1])
                return original(command, **kwargs)
            scene = {'source': source, 'nome': 'Cerimônia especial com a família Ação 100% "João"',
                     'previa': {'inicio': 0, 'duracao_efetiva': .2}}
            settings = {'segundos': .2, 'visual': {'fundo': '#101f33', 'texto': '#f3ead6',
                        'destaque': '#c7a362'}, 'musica': None, 'exibir_rodape': True}
            with patch.object(self.motor, 'run_media', side_effect=observe):
                self.motor.render_animated(self.ffmpeg, self.work, self.work, 'Título',
                                           [scene], profile, True, settings)
            graph = graphs[-1]
            self.assertIn('label-1-line1.txt', graph)
            wrapped = (self.work / 'label-1.txt').read_text(encoding='utf-8')
            self.assertEqual(wrapped.split(), scene['nome'].split())
            positions = [int(v) for v in re.findall(r'textfile=label-1-line\d+\.txt:[^,;]*:y=(\d+)', graph)]
            self.assertEqual(len(positions), len(wrapped.splitlines()))
            cell = self.motor.animated_layout(profile)[0]
            self.assertEqual(positions[0], cell['y'] + round(66*factor))
            heights = [self.motor.measure_text_pixels(self.ffmpeg, self.work, self.font, line,
                                                       max(1,round(24*factor)),0)[1]
                       for line in wrapped.splitlines()]
            self.assertTrue(all(y+h <= cell['y']+cell['h']-cell['pad'] for y,h in zip(positions,heights)))
            self.assertTrue(all(a+h <= b for a,h,b in zip(positions,heights,positions[1:])))
            legacy_height = self.motor.measure_text_pixels(self.ffmpeg,self.work,self.font,wrapped,
                                                           max(1,round(24*factor)),max(1,round(5*factor)))[1]
            self.assertLessEqual(positions[-1]-positions[0]+heights[-1],legacy_height)
        self.assertEqual(self.motor.sha256(source),before)


class ExplicitIntegerEditTests(unittest.TestCase):
    def test_half_second_float_boundary_does_not_turn_untouched_endpoint_into_edit(self):
        app = IntegerTimeTests().app()
        scene = app.project.scenes[0]
        scene.start, scene.duration, scene.source_duration = .019, .4809999999999999, 1.25
        app.load_scene_fields()
        captured = app.capture(validate=True).scenes[0]
        self.assertEqual((captured.start, captured.duration), (scene.start, scene.duration))
        self.assertEqual(app.visible_seconds(.49999999999999994), 0)
        self.assertEqual(app.visible_seconds(.5), 1)

    def test_touched_duration_same_visible_second_requires_apply_then_becomes_integer(self):
        app, _ = duration_app()
        app.project.duration = 2.25
        app._duration_edited = True
        with self.assertRaisesRegex(ErroProjeto, 'Aplicar duração'):
            app.capture(skip_scene=True)
        app.finish_duration_edit()
        self.assertEqual(app.project.duration, 2)
        self.assertFalse(app.duration_pending())

    def test_touched_start_same_visible_second_changes_only_that_endpoint(self):
        app = IntegerTimeTests().app()
        app.project.scenes[0].start, app.project.scenes[0].duration = .9, 2
        app.load_scene_fields()
        app._scene_edited = {'start'}
        captured = app.capture(validate=True).scenes[0]
        self.assertEqual((captured.start, captured.duration), (1, 1.9))

    def test_invalid_integer_interval_message_does_not_expose_fractional_seconds(self):
        app = IntegerTimeTests().app()
        app.scene_start_seconds_var.set('2')
        app.scene_end_seconds_var.set('2')
        with self.assertRaisesRegex(ErroProjeto, 'pelo menos um frame') as caught:
            app.capture(validate=True)
        self.assertNotRegex(str(caught.exception), r'\d[.,]\d')


class PrecisionRoundtripTests(unittest.TestCase):
    def test_one_frame_fractional_source_survives_save_reopen_selection_and_rename(self):
        import tempfile
        import menu_anisio as motor
        import gui_servicos as services
        from gui_autoria import Project
        with tempfile.TemporaryDirectory(prefix='tempos-reabrir-') as temporary:
            base = Path(temporary)
            source = base / 'fonte-curta.mp4'
            motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg')) + [
                '-f', 'lavfi', '-i', 'testsrc2=size=64x36:rate=30:duration=0.2',
                '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(source)])
            before = motor.sha256(source)
            project = Project(duration=1/30)
            project.add_video(services.inspect_media(source))
            project.edit_scene(0, 'Um frame', .1, 1/30)
            project.scenes.append(copy.deepcopy(project.scenes[0]))
            services.save_project(project, base / 'original.menu')
            loaded = services.open_project(base / 'original.menu')
            app = IntegerTimeTests().app()
            app.project = loaded
            app._refreshing = False
            app.set_time(app.duration_minutes_var, app.duration_seconds_var, loaded.duration)
            app.tree = Mock()
            def refresh(index):
                app.editing_index = index
                app.load_scene_fields()
            app.refresh = refresh
            app.load_scene_fields()
            for index in (1, 0, 1, 0):
                app.tree.selection.return_value = (str(index),)
                app.select_scene()
            app.scene_name_var.set('Renomeado')
            captured = app.capture(validate=True)
            self.assertEqual((captured.scenes[0].start, captured.scenes[0].duration), (.1, 1/30))
            self.assertEqual(captured.duration, 1/30)
            services.save_project(captured, base / 'renomeado.menu')
            self.assertEqual(services.open_project(base / 'renomeado.menu'), captured)
            self.assertEqual(motor.sha256(source), before)
