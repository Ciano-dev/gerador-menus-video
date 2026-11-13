import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui_autoria import MediaInfo, Project
from menu_anisio import ErroProjeto


class AuthoringTests(unittest.TestCase):
    def project(self):
        project = Project()
        project.add_video(MediaInfo(Path('/local/cerimonia.mp4'), 12))
        return project

    def test_adicionar_renomear_trecho_reordenar_remover_sem_tocar_fontes(self):
        project = self.project()
        project.add_video(MediaInfo(Path('/local/festa.mov'), 4))
        project.edit_scene(0, 'Cerimônia de João', 2, 3)
        project.move_scene(0, 1)
        self.assertEqual([s.name for s in project.scenes], ['festa', 'Cerimônia de João'])
        self.assertEqual((project.scenes[1].start, project.scenes[1].duration), (2, 3))
        self.assertEqual(project.scenes[1].source, Path('/local/cerimonia.mp4'))
        project.remove_scene(0)
        self.assertEqual(len(project.scenes), 1)

    def test_limite_seis_e_fonte_curta_tem_previa_utilizavel(self):
        project = Project()
        for _ in range(6):
            project.add_video(MediaInfo(Path('/local/curto.mp4'), .5))
        self.assertEqual(project.scenes[0].duration, .5)
        with self.assertRaisesRegex(ErroProjeto, '6'):
            project.add_video(MediaInfo(Path('/local/excesso.mp4'), 1))
        self.assertEqual(len(project.scenes), 6)

    def test_edicao_invalida_e_atomica(self):
        project = self.project()
        before = copy.deepcopy(project.scenes)
        for name, start, duration in [('', 0, 1), ('nome', -1, 1), ('nome', 11, 2),
                                      ('nome', 0, math.nan), ('nome', 0, True),
                                      ('nome', 0, .01), ('nome', 0, 181)]:
            with self.subTest(value=(name, start, duration)):
                with self.assertRaises(ErroProjeto):
                    project.edit_scene(0, name, start, duration)
                self.assertEqual(project.scenes, before)

    def test_titulo_cores_rodape_e_duracao_manual(self):
        project = self.project()
        project.title = 'Memórias de João e Lívia'
        project.colors = {'fundo': '#ffffff', 'texto': '#102030', 'destaque': '#ab12ef'}
        project.show_footer = False
        project.duration = 2
        project.validate()
        for field, value in [('title', ''), ('duration', math.inf), ('show_footer', 'false'),
                             ('colors', {'fundo': 'white'})]:
            previous = getattr(project, field)
            setattr(project, field, value)
            with self.subTest(field=field), self.assertRaises(ErroProjeto):
                project.validate()
            setattr(project, field, previous)

    def test_musica_opcional_manual_ou_duracao_da_faixa_sem_loop(self):
        project = self.project()
        project.duration = 2
        project.validate()
        project.duration_mode = 'musica'
        with self.assertRaisesRegex(ErroProjeto, 'música'):
            project.validate()
        project.music = MediaInfo(Path('/local/trilha.wav'), 2.5)
        self.assertEqual(project.effective_duration(), 2.5)
        project.validate()
        project.duration_mode = 'manual'
        project.duration = 3
        project.validate()
        project.duration = 1
        project.music = MediaInfo(Path('/local/longa.wav'), 181)
        project.validate()

    def test_lista_vazia_e_movimento_fora_da_lista_recusados(self):
        with self.assertRaises(ErroProjeto):
            Project().validate()
        project = self.project()
        with self.assertRaises(ErroProjeto):
            project.move_scene(0, -1)
        self.assertEqual(len(project.scenes), 1)


if __name__ == '__main__':
    unittest.main()
