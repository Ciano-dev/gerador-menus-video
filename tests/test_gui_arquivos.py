import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gerar_fixtures import generate
from gui_autoria import Project
import gui_servicos as services
from menu_anisio import ErroProjeto, sha256


class ProjectFilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='gui-arquivos-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        cls.sources = generate(cls.base / 'fontes')

    def project(self):
        project = Project(title='João e Lívia')
        project.add_video(services.inspect_media(self.sources / 'cores-com-audio.mp4'))
        project.edit_scene(0, 'Cerimônia', .2, .5)
        project.music = services.inspect_media(self.sources / 'som-sem-video.m4a', audio=True)
        project.duration_mode = 'musica'
        project.show_footer = False
        return project

    def test_salvar_reabrir_e_salvar_em_outra_pasta_rebaseia_caminhos(self):
        project = self.project()
        first = self.base / 'primeiro.json'
        services.save_project(project, first)
        data = json.loads(first.read_text(encoding='utf-8'))
        self.assertEqual(data['cenas'][0]['arquivo'], 'fontes/cores-com-audio.mp4')
        self.assertEqual(data['musica']['arquivo'], 'fontes/som-sem-video.m4a')
        reopened = services.open_project(first)
        self.assertEqual(reopened, project)
        subdir = self.base / 'outro'
        subdir.mkdir()
        second = subdir / 'segundo.json'
        services.save_project(reopened, second)
        self.assertEqual(json.loads(second.read_text())['cenas'][0]['arquivo'],
                         '../fontes/cores-com-audio.mp4')
        self.assertEqual(services.open_project(second), project)

    def test_salvar_por_alias_de_pasta_usa_caminhos_relativos_canonicos(self):
        project = self.project()
        with tempfile.TemporaryDirectory() as tmp:
            alias = Path(tmp) / 'alias'
            alias.symlink_to(self.base, target_is_directory=True)
            target = alias / 'alias-salvo.json'
            services.save_project(project, target)
            data = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(data['cenas'][0]['arquivo'], 'fontes/cores-com-audio.mp4')
            self.assertEqual(services.open_project(target), project)

    def test_salvar_recusa_sobrescrita_inclusive_fonte(self):
        project = self.project()
        source = project.scenes[0].source
        before = sha256(source)
        with self.assertRaisesRegex(ErroProjeto, 'existe'):
            services.save_project(project, source)
        self.assertEqual(before, sha256(source))

    def test_volumes_windows_distintos_guardam_referencia_local(self):
        self.assertEqual(services.relative_media_path(r'C:\\filmes\\cena.mp4',
                         r'C:\\projetos', windows=True), '../filmes/cena.mp4')
        self.assertEqual(services.relative_media_path(r'D:\\cena.mp4', r'C:\\projetos', windows=True), 'D:/cena.mp4')

    def test_probe_real_recusa_audio_como_video_e_video_mudo_como_musica(self):
        video = services.inspect_media(self.sources / 'cores-com-audio.mp4')
        self.assertAlmostEqual(video.duration, 1, places=3)
        for path, audio in [(self.sources / 'som-sem-video.m4a', False),
                            (self.sources / 'retrato-mudo.mov', True)]:
            with self.subTest(path=path), self.assertRaises(ErroProjeto):
                services.inspect_media(path, audio=audio)

    def test_reabrir_json_invalido_duplicado_ou_fonte_ausente(self):
        target = self.base / 'invalido.json'
        for content in ['{', '{"titulo":"a","titulo":"b"}',
                        '{"modo":"animado","titulo":"a","duracao":{"modo":"manual",'
                        '"segundos":1},"cenas":[{"arquivo":"ausente.mp4","nome":"a",'
                        '"previa":{"inicio":0,"duracao":1}}]}']:
            target.write_text(content, encoding='utf-8')
            with self.subTest(content=content), self.assertRaises(ErroProjeto):
                services.open_project(target)


if __name__ == '__main__':
    unittest.main()
