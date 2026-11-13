"""Regressões do contrato final autorizado (sem substituir prova nativa)."""
import inspect
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import menu_gui


class FinalInterfaceTests(unittest.TestCase):
    def test_scene_labels_hint_and_single_apply_action(self):
        source = inspect.getsource(menu_gui.App._build)
        self.assertIn("'Nome da cena', 'Arquivo de vídeo'", source)
        self.assertIn('Nome exibido no menu; não altera o arquivo original.', source)
        self.assertEqual(source.count("'Aplicar nome e trecho'"), 1)
        self.assertNotIn("'Aplicar trecho'", source)

    def test_last_output_has_no_copyable_field_and_keeps_real_open_actions(self):
        self.assertNotIn('result_var', inspect.getsource(menu_gui.App))
        self.assertNotIn('caminho copiável', inspect.getsource(menu_gui.App._build))
        app = menu_gui.App.__new__(menu_gui.App)
        app.worker = Mock(busy=False)
        app.open_button, app.folder_button = Mock(), Mock()
        app.status_var = Mock()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'menu final.mp4'
            output.write_bytes(b'output')
            app.finished_render(output)
            self.assertEqual(app.last_catalog, output)
            app.open_button.state.assert_called_with(['!disabled'])
            app.folder_button.state.assert_called_with(['!disabled'])
            self.assertNotIn(temporary, app.status_var.set.call_args.args[0])
            app.worker.busy = True
            app.update_folder_button()
            app.open_button.state.assert_called_with(['disabled'])
            app.worker.busy = False
            output.unlink()
            app.update_folder_button()
            app.folder_button.state.assert_called_with(['disabled'])


class FinalTitleTests(unittest.TestCase):
    def test_engine_centers_measured_block_without_changing_line_advance(self):
        import re
        from unittest.mock import patch
        import menu_video as motor
        title = ' '.join(['MMMM'] * 18)
        for profile, factor in [('1080p', 1), ('prova', 1/3)]:
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                def measure(ffmpeg, work, font, value, size, spacing):
                    return len(value) * size/2, size - 3
                with patch.object(motor, 'measure_text_pixels', side_effect=measure), \
                        patch.object(motor, 'run_media') as run:
                    motor.render_animated('ffmpeg', base, base, title, [], profile, True,
                        {'segundos': .1, 'visual': {'fundo': '#101f33', 'texto': '#f3ead6',
                         'destaque': '#c7a362'}, 'musica': None})
                graph = run.call_args.args[0][run.call_args.args[0].index('-filter_complex')+1]
                ys = [int(y) for y in re.findall(r'textfile=titre(?:-line\d+)?\.txt:[^,;]*:y=(\d+)', graph)]
                size = round(44*factor)
                self.assertGreater(len(ys), 1)
                self.assertEqual(ys[1]-ys[0], size+max(1, round(2*factor)))
                self.assertAlmostEqual((ys[0]+ys[-1]+size-3)/2, round(198*factor)/2, delta=.5)

    def test_preview_centers_actual_bbox_and_validator_rejects_old_region(self):
        from tests.test_gui_cores import Canvas, Font, Var
        from gui_cores import ColorPreview, draw_model
        from native_color_checks import validate_item_geometry
        for alignment in ('esquerda', 'centralizado'):
            for title in ('Título', 'Título\nsegunda linha'):
                canvas = Canvas()
                preview = ColorPreview(canvas, Var(title), {}, lambda: [],
                                       font_factory=Font, alignment=Var(alignment))
                canvas.flush()
                item = next(i for i, row in enumerate(canvas.items, 1) if 'title' in row[2]['tags'])
                bbox = canvas.bbox(item)
                self.assertAlmostEqual((bbox[1]+bbox[3])/2, 198/5/2, delta=.5)
                row = canvas.items[item-1]
                measured = {'coords': list(row[1]), 'bbox': bbox,
                            'anchor': row[2]['anchor'], 'justify': row[2]['justify']}
                spec = next(i for i in draw_model(title, {}, [], 384, 216,
                            title_alignment=alignment)['items'] if i['tag'] == 'title')
                validate_item_geometry(measured, spec)
                old = dict(measured, coords=[measured['coords'][0], 52/5], bbox=[18, 10, 100, 34])
                with self.assertRaises(AssertionError):
                    validate_item_geometry(old, spec)
                preview.destroy()


class FinalDocumentationTests(unittest.TestCase):
    def test_current_user_instructions_and_direct_validation_have_no_activation_flow(self):
        root = Path(__file__).resolve().parents[1]
        readme = (root/'README.md').read_text(encoding='utf-8')
        build = (root/'BUILD-WINDOWS.md').read_text(encoding='utf-8')
        for text in (readme, build):
            self.assertNotIn('ativação', text.lower())
            self.assertNotIn('licença offline', text.lower())
            self.assertNotIn('licença permanente', text.lower())
            self.assertNotIn('ci_activation.py', text)
        self.assertIn('Aplicar nome e trecho', readme)
        self.assertIn('Python/Tcl/Tk', build)
        self.assertIn('GPLv3', build)


class FinalNativeContractTests(unittest.TestCase):
    def test_native_validator_requires_uncropped_measured_button_text_in_all_four_cases(self):
        import copy
        from test_etapa2 import interface_fixture
        from native_ui_checks import validate_interface_report
        valid = interface_fixture()
        validate_interface_report(valid)
        for row in range(4):
            for field in ('requested_size', 'text_width', 'bounds', 'parent'):
                invalid = copy.deepcopy(valid)
                invalid['cases'][row]['button_measures'][0].pop(field)
                with self.subTest(row=row, missing=field), self.assertRaises(ValueError):
                    validate_interface_report(invalid)
            invalid = copy.deepcopy(valid)
            invalid['cases'][row]['button_measures'][0]['bounds'][2] = 10
            with self.subTest(row=row, cropped=True), self.assertRaises(ValueError):
                validate_interface_report(invalid)
        invalid = copy.deepcopy(valid)
        invalid['cases'][0]['button_measures'].pop()
        with self.assertRaises(ValueError):
            validate_interface_report(invalid)

    def test_native_validator_rejects_old_title_bbox_even_with_new_selected_alignment(self):
        from test_etapa2 import interface_fixture
        from native_ui_checks import validate_interface_report
        valid = interface_fixture()
        for row in valid['cases']:
            for probe in row['alignment_probes']:
                probe['title']['coords'][1] = 10.4
                probe['title']['bbox'] = [18, 10, 60, 34]
        with self.assertRaises(ValueError):
            validate_interface_report(valid)

    def test_powershell_gate_demands_title_bbox_and_all_button_measurements(self):
        root = Path(__file__).resolve().parents[1]
        smoke = (root/'windows_build/smoke_windows.ps1').read_text(encoding='utf-8')
        for fragment in ('function Assert-TitleGeometry', '$Title.bbox', '$Title.coords',
                         '$row.button_measures', '$measure.requested_size', '$measure.text_width',
                         'Aplicar nome e trecho'):
            self.assertIn(fragment, smoke)
