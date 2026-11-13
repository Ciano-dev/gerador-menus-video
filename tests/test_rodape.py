"""Rodapé removido: exportação real, pixels, compatibilidade e tipos JSON."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import menu_anisio as motor


def project_data():
    return {'modo': 'animado', 'titulo': 'Casamento · Demo',
            'duracao': {'modo': 'manual', 'segundos': .4},
            'cenas': [{'arquivo': 'fonte.mp4', 'nome': name,
                       'previa': {'inicio': 0, 'duracao': .2}}
                      for name in ('Cerimônia', 'Festa')]}


class RodapeRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory(prefix='anisio-rodape-')
        cls.addClassCleanup(temp.cleanup)
        cls.base = Path(temp.name)
        cls.ffmpeg = shutil.which('ffmpeg')
        motor.run_media(motor.ffmpeg_base(cls.ffmpeg) + [
            '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=30:duration=0.4',
            '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p',
            str(cls.base / 'fonte.mp4')])

    def export(self, name, value=None):
        data = project_data()
        if value is not None:
            data['exibir_rodape'] = value
        project = self.base / f'{name}.json'
        project.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        output = self.base / f'{name}.mp4'
        captured = {}
        rendered_settings = {}
        before = motor.sha256(self.base / 'fonte.mp4')
        render = motor.render_animated

        def observe(*args):
            render(*args)
            rendered_settings.update(args[7])
            captured.update({p.name: p.read_text(encoding='utf-8')
                             for p in args[1].glob('*.txt')})

        with patch.object(motor, 'render_animated', side_effect=observe):
            motor.export_project(project, output, 'prova')
        manifest = {'titulo': data['titulo'], 'catalogo': {**rendered_settings, 'sha256': motor.sha256(output)}}
        raw = motor.run_media(motor.ffmpeg_base(self.ffmpeg) + [
            '-i', str(output), '-map', '0:V:0',
            '-frames:v', '1', '-pix_fmt', 'rgb24', '-threads', '1',
            '-f', 'rawvideo', '-']).stdout
        self.assertEqual(len(raw), 640 * 360 * 3)
        self.assertTrue(output.is_file())
        self.assertEqual(motor.sha256(self.base / 'fonte.mp4'), before)
        return manifest, captured, raw

    def region(self, raw, x, y, width, height):
        return b''.join(raw[(row * 640 + x) * 3:(row * 640 + x + width) * 3]
                        for row in range(y, y + height))

    def ink(self, raw, region):
        background = raw[(350 * 640) * 3:(350 * 640) * 3 + 3]
        crop = self.region(raw, *region)
        return sum(max(abs(crop[i + c] - background[c]) for c in range(3)) > 40
                   for i in range(0, len(crop), 3))

    def test_false_remove_rodape_real_preserva_titulo_e_rotulos(self):
        manifest, texts, raw = self.export('sem-rodape', False)
        footer_ink = self.ink(raw, (20, 340, 600, 20))
        print('RODAPE_FALSE_PIXELS', footer_ink, flush=True)
        self.assertEqual(footer_ink, 0, 'false deve deixar o rodapé sem texto nos pixels reais')
        self.assertNotIn('footer.txt', texts)
        self.assertIs(manifest['catalogo']['exibir_rodape'], False)
        self.assertEqual(texts['titre.txt'], 'Casamento · Demo')
        self.assertEqual([texts[f'label-{i}.txt'] for i in (1, 2)], ['Cerimônia', 'Festa'])
        self.assertEqual(manifest['titulo'], 'Casamento · Demo')
        regions = [(20, 10, 600, 45)] + [
            (cell['x'] + cell['label'], cell['y'] + 17, 120, 30)
            for cell in motor.animated_layout('prova')[:2]]
        for region in regions:
            self.assertGreater(self.ink(raw, region), 20, 'título e nomes continuam visíveis')
        _, _, reference = self.export('referencia-com-rodape', True)
        for region in regions:
            a, b = self.region(raw, *region), self.region(reference, *region)
            self.assertLess(sum(abs(x - y) for x, y in zip(a, b)) / len(a), 3)
        print('TITULO_E_ROTULOS_PRESENTES', [self.ink(raw, r) for r in regions], flush=True)

    def test_default_e_true_nao_desenham_rodape(self):
        default, default_texts, default_raw = self.export('padrao')
        explicit, explicit_texts, explicit_raw = self.export('explicito', True)
        self.assertIs(default['catalogo']['exibir_rodape'], True)
        self.assertIs(explicit['catalogo']['exibir_rodape'], True)
        self.assertNotIn('footer.txt', default_texts)
        self.assertNotIn('footer.txt', explicit_texts)
        self.assertEqual(default_texts, explicit_texts)
        self.assertEqual(self.ink(default_raw, (20, 340, 600, 20)), 0)
        self.assertEqual(default_raw, explicit_raw)
        self.assertEqual(default['catalogo']['sha256'], explicit['catalogo']['sha256'])
        print('DEFAULT_IGUAL_TRUE', default['catalogo']['sha256'], flush=True)


class RodapeValidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='anisio-rodape-tipos-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        (self.base / 'fonte.mp4').write_bytes(b'validacao anterior a leitura de midia')
        self.project = self.base / 'projeto.json'
        self.output = self.base / 'entrega.mp4'

    def write(self, data):
        self.project.write_text(json.dumps(data), encoding='utf-8')

    def test_settings_aceita_apenas_booleanos_e_default_true(self):
        for value in (False, True, 'omitido'):
            with self.subTest(value=value):
                data = project_data()
                if value != 'omitido':
                    data['exibir_rodape'] = value
                self.write(data)
                settings = motor.load_project(self.project, self.output, with_settings=True)[3]
                self.assertIs(settings['exibir_rodape'], True if value == 'omitido' else value)

    def test_tipos_invalidos_recusados_antes_de_midia_e_publicacao(self):
        for value in (None, 0, 1, 0.0, 1.0, '', 'false', 'true', [], [False], {}, {'valor': False}):
            with self.subTest(value=value):
                data = project_data()
                data['exibir_rodape'] = value
                self.write(data)
                with patch.object(motor, 'probe_media') as probe:
                    with self.assertRaisesRegex(motor.ErroProjeto, 'exibir_rodape.*boolean'):
                        motor.export_project(self.project, self.output, 'prova')
                    probe.assert_not_called()
                self.assertFalse(self.output.exists())
                self.assertFalse(list(self.base.glob('.entrega*')))

    def test_legado_continua_ignorando_opcao_animada(self):
        data = project_data()
        data['exibir_rodape'] = 'ignorado'
        for mode in ('legado', None):
            with self.subTest(mode=mode):
                if mode is None:
                    data.pop('modo')
                else:
                    data['modo'] = mode
                self.write(data)
                self.assertIsNone(motor.load_project(self.project, self.output, with_settings=True)[3])


if __name__ == '__main__':
    unittest.main()
