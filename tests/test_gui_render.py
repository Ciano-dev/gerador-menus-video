import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gerar_fixtures import generate
from gui_autoria import Project
import gui_servicos as services
from gui_trabalho import Worker
import menu_anisio as motor


class GuiRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='gui-render-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        cls.sources = generate(cls.base / 'fontes')

    def project(self):
        project = Project(title='Prévia real · João', duration=1)
        project.add_video(services.inspect_media(self.sources / 'cores-com-audio.mp4'))
        project.edit_scene(0, 'Cerimônia', .2, .5)
        return project

    def test_previa_real_assincrona_movimento_hashes_e_exportacao_nova(self):
        project = self.project()
        before = motor.sha256(project.scenes[0].source)
        original = copy.deepcopy(project)
        worker = Worker()
        worker.submit(lambda progress: services.preview_project(project, self.base, progress))
        events = []
        deadline = time.monotonic() + 60
        while worker.busy and time.monotonic() < deadline:
            events.extend(worker.poll())
            time.sleep(.01)
        self.assertFalse(worker.busy)
        self.assertEqual(events[-1].kind, 'done', str(events[-1].value))
        catalog = events[-1].value
        self.assertTrue(catalog.is_file())
        self.addCleanup(shutil.rmtree, catalog.parent)
        self.assertTrue(any(e.kind == 'progress' for e in events))
        self.assertEqual(project, original)
        self.assertEqual(list(catalog.parent.iterdir()), [catalog])
        self.assertEqual(before, motor.sha256(project.scenes[0].source))
        self.assertEqual(len(motor.probe_media(motor.resolve_tool('ffprobe'), catalog)['streams']), 1)
        frames = []
        for instant in [.1, .35]:
            frames.append(motor.run_media(motor.ffmpeg_base(shutil.which('ffmpeg')) + [
                '-ss', str(instant), '-i', str(catalog), '-frames:v', '1',
                '-pix_fmt', 'rgb24', '-threads', '1', '-f', 'rawvideo', '-']).stdout)
        self.assertNotEqual(frames[0], frames[1], 'A prévia deve conter movimento real.')
        destination = self.base / 'entrega-real.mp4'
        exported = services.render_project(project, self.base, destination, profile='prova')
        self.assertEqual(exported, destination)
        digest = motor.sha256(exported)
        with self.assertRaisesRegex(motor.ErroProjeto, 'existe'):
            motor.export_media(project.title, [], destination, None, 'prova')
        self.assertEqual(digest, motor.sha256(exported))
        self.assertFalse(list(self.base.glob('.anisio-gui-*')))

    def test_falha_do_motor_visivel_sem_publicar_pasta_ou_apagar_terceiros(self):
        project = self.project()
        destination = self.base / 'falha.mp4'
        sentinel = self.base / 'preservar.txt'
        sentinel.write_text('intacto', encoding='utf-8')
        with patch.object(motor, 'render_animated', side_effect=motor.ErroProjeto('falha induzida')):
            with self.assertRaisesRegex(motor.ErroProjeto, 'induzida'):
                services.render_project(project, self.base, destination, profile='prova')
        self.assertFalse(destination.exists())
        self.assertEqual(sentinel.read_text(), 'intacto')
        self.assertFalse(list(self.base.glob('.anisio-gui-*')))
        self.assertFalse(list(self.base.glob('.falha*')))

    def test_player_recebe_caminho_como_argumento_sem_shell(self):
        with patch.object(services.sys, 'platform', 'linux'), \
                patch.object(services.shutil, 'which', return_value='/usr/bin/xdg-open'), \
                patch.object(services.subprocess, 'run') as run:
            services.open_player(self.sources / 'cores-com-audio.mp4')
        args, kwargs = run.call_args
        self.assertEqual(args[0], ['/usr/bin/xdg-open', str(self.sources / 'cores-com-audio.mp4')])
        self.assertNotIn('shell', kwargs)


if __name__ == '__main__':
    unittest.main()
