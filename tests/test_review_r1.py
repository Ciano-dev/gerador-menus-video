"""Regressões de geometria; fixtures artificiais não são capturas nativas."""
import copy
import unittest
from unittest.mock import Mock, patch

from native_color_checks import snapshot, validate_color_report
from native_ui_checks import validate_interface_report
from test_gui_cores import Canvas, ColorPreview, Font, Var, report_fixture
from test_etapa2 import interface_fixture


class ColorGeometryTests(unittest.TestCase):
    def test_snapshot_reads_anchor_and_justify_from_canvas(self):
        app = Mock()
        canvas = app.color_canvas
        canvas.find_withtag.return_value = [7]
        canvas.gettags.return_value = ('palette', 'title')
        canvas.type.return_value = 'text'
        canvas.itemcget.side_effect = lambda item, key: {
            'fill': '#ffffff', 'text': 'Título real', 'anchor': 'n', 'justify': 'center'}[key]
        canvas.coords.return_value = [192, 10.4]
        canvas.bbox.return_value = [150, 10, 234, 20]
        app.color_vars = {}
        app.project.scenes = []
        app.root.winfo_screenwidth.return_value = 1920
        app.root.winfo_screenheight.return_value = 1080
        with patch('native_color_checks.bounds', return_value=[0, 0, 384, 216]):
            item = snapshot(app, 'before', [1])['items'][0]
        self.assertEqual((item.get('anchor'), item.get('justify')), ('n', 'center'))

    def test_centered_drawn_geometry_is_accepted_and_left_is_still_valid(self):
        for alignment in ('esquerda', 'centralizado'):
            with self.subTest(alignment=alignment):
                report = report_fixture(alignment)
                canvas = Canvas()
                probe = report['probes'][0]
                preview = ColorPreview(canvas, Var(probe['title']),
                    {key: Var(value) for key, value in probe['palette'].items()},
                    lambda: probe['names'], font_factory=Font, alignment=Var(alignment))
                canvas.flush()
                drawn = next(item for item in canvas.items if 'title' in item[2]['tags'])
                title = next(item for item in probe['items'] if item['tag'] == 'title')
                self.assertEqual(tuple(title['coords']), drawn[1])
                self.assertEqual((title['anchor'], title['justify']),
                                 (drawn[2]['anchor'], drawn[2]['justify']))
                preview.destroy()
                validate_color_report(report)

    def test_declared_center_with_left_geometry_is_rejected(self):
        report = report_fixture()
        for probe in report['probes']:
            probe['alignment'] = 'centralizado'
        with self.assertRaises(ValueError):
            validate_color_report(report)

    def test_coordinates_anchor_and_justify_fail_closed_for_every_text(self):
        for alignment in ('esquerda', 'centralizado'):
            valid = report_fixture(alignment)
            for index, item in enumerate(valid['probes'][0]['items']):
                if item['kind'] != 'text':
                    continue
                for key, value in [('coords', [0, 0]), ('anchor', 's'), ('justify', 'right')]:
                    invalid = copy.deepcopy(valid)
                    invalid['probes'][0]['items'][index][key] = value
                    with self.subTest(alignment=alignment, tag=item['tag'], field=key), self.assertRaises(ValueError):
                        validate_color_report(invalid)
                for key in ('anchor', 'justify'):
                    invalid = copy.deepcopy(valid)
                    del invalid['probes'][0]['items'][index][key]
                    with self.subTest(alignment=alignment, tag=item['tag'], missing=key), self.assertRaises(ValueError):
                        validate_color_report(invalid)


class InterfaceGeometryTests(unittest.TestCase):
    def test_alignment_strings_without_canvas_measurements_are_rejected(self):
        invalid = interface_fixture()
        for row in invalid['cases']:
            row.pop('alignment_probes', None)
        with self.assertRaises(ValueError):
            validate_interface_report(invalid)

    def test_drawn_coords_anchor_and_justify_are_required_for_both_alignments(self):
        valid = interface_fixture()
        # Actual geometry contract, independent from the validator's calculations.
        for row in valid['cases']:
            row['alignment_probes'] = [
                {'alignment': 'esquerda', 'canvas_size': [384, 216],
                 'title': {'coords': [17.6, 14.8], 'bbox': [18, 15, 60, 25], 'anchor': 'nw', 'justify': 'left'}},
                {'alignment': 'centralizado', 'canvas_size': [384, 216],
                 'title': {'coords': [192, 14.8], 'bbox': [170, 15, 214, 25], 'anchor': 'n', 'justify': 'center'}}]
        validate_interface_report(valid)
        for index in (0, 1):
            for key, value in [('coords', [0, 0]), ('anchor', 's'), ('justify', 'right')]:
                invalid = copy.deepcopy(valid)
                invalid['cases'][0]['alignment_probes'][index]['title'][key] = value
                with self.subTest(index=index, field=key), self.assertRaises(ValueError):
                    validate_interface_report(invalid)


if __name__ == '__main__':
    unittest.main()
