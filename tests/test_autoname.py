"""Nome automático só no serviço GUI; motor explícito continua exclusivo."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import gui_servicos as services
import menu_anisio as motor
from gui_autoria import MediaInfo, Project


class AutoNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='autoname-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / 'fonte.mp4'
        self.source.write_bytes(b'original')
        self.project = Project(duration=.2)
        self.project.add_video(MediaInfo(self.source, 1))

    def render(self, requested):
        messages = []
        with patch.object(motor, 'export_media', side_effect=lambda title, scenes, output, *a, **k: output) as export:
            result = services.render_project(self.project, None, requested, progress=messages.append)
        self.assertEqual(export.call_args.args[2], result)
        self.assertIn(result.name, messages[0])
        self.assertIn(str(result), messages[-1])
        return result

    def test_dois_ocupados_escolhe_proximo_e_anuncia_nome_real(self):
        requested = self.base / 'menu.mp4'
        requested.write_bytes(b'antigo')
        (self.base / 'menu (1).mp4').write_bytes(b'antigo 1')
        self.assertEqual(self.render(requested), self.base / 'menu (2).mp4')
        self.assertEqual(requested.read_bytes(), b'antigo')
        self.assertEqual((self.base / 'menu (1).mp4').read_bytes(), b'antigo 1')

    def test_sufixo_sem_cascata_case_extensao_e_pastas_independentes(self):
        (self.base / 'MENU.MP4').touch()
        (self.base / 'menu (1).MP4').touch()
        self.assertEqual(self.render(self.base / 'menu (1).mp4'), self.base / 'menu (2).mp4')
        self.assertEqual(self.render(self.base / 'MENU.MP4'), self.base / 'MENU (2).MP4')
        other = self.base / 'outra'
        other.mkdir()
        self.assertEqual(self.render(other / 'menu.mp4'), other / 'menu.mp4')

    def test_dangling_symlink_conta_ocupado_sem_criar_alvo(self):
        missing = self.base / 'inexistente'
        output = self.base / 'menu.mp4'
        output.symlink_to(missing)
        self.assertEqual(self.render(output), self.base / 'menu (1).mp4')
        self.assertTrue(output.is_symlink())
        self.assertFalse(missing.exists())

    def test_corrida_antes_motor_erro_amigavel_sem_substituir(self):
        output = self.base / 'menu.mp4'
        def progress(message):
            output.write_bytes(b'terceiro')
        with patch.object(motor, 'export_media', return_value=output) as export:
            with self.assertRaisesRegex(motor.ErroProjeto, 'existe.*exportar novamente'):
                services.render_project(self.project, None, output, progress=progress)
        export.assert_not_called()
        self.assertEqual(output.read_bytes(), b'terceiro')

    def test_conflito_tardio_amigavel_sem_mascarar_io_ou_permissao(self):
        import errno
        output = self.base / 'menu.mp4'
        def conflict(*args, **kwargs):
            output.write_bytes(b'terceiro tardio')
            cause = FileExistsError(errno.EEXIST, 'ocupado')
            raise motor.ErroProjeto('a saída já existe; exportação cancelada') from cause
        with patch.object(motor, 'export_media', side_effect=conflict):
            with self.assertRaisesRegex(motor.ErroProjeto, 'existe.*exportar novamente'):
                services.render_project(self.project, None, output)
        self.assertEqual(output.read_bytes(), b'terceiro tardio')
        for error in (PermissionError('acesso negado'), OSError(errno.EIO, 'I/O real')):
            with self.subTest(error=error), patch.object(motor, 'export_media', side_effect=error):
                with self.assertRaises(type(error)) as raised:
                    services.render_project(self.project, None, output)
                self.assertIs(raised.exception, error)


class RealAutoNameTests(unittest.TestCase):
    def test_fontes_musica_projeto_ocupados_preservados_em_cancel_e_sucesso(self):
        import copy
        import subprocess
        import time
        import media_runtime as runtime
        with tempfile.TemporaryDirectory(prefix='autoname-real-') as tmp:
            base = Path(tmp)
            source, music, saved = [base / f'menu{suffix}.mp4' for suffix in ('', ' (1)', ' (2)')]
            ffmpeg = motor.resolve_tool('ffmpeg')
            motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
                'testsrc2=s=64x64:r=30:duration=0.3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)])
            motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
                'sine=duration=0.3', '-c:a', 'aac', str(music)])
            project = Project(title='Nome automático', duration=600)
            project.add_video(services.inspect_media(source))
            project.edit_scene(0, 'Sintético', 0, .2)
            project.music = services.inspect_media(music, audio=True)
            services.save_project(project, saved)
            before = {path: motor.sha256(path) for path in (source, music, saved)}
            token = runtime.CancellationToken()
            original = subprocess.Popen
            children = []
            reached = []
            def spawn(arguments, **kwargs):
                child = original(arguments, **kwargs)
                children.append(child)
                if '-filter_complex' in arguments:
                    reached.append(True)
                    token.cancel()
                return child
            messages = []
            started = time.monotonic()
            with patch.object(subprocess, 'Popen', side_effect=spawn):
                with self.assertRaises(runtime.Cancelled):
                    services.render_project(project, None, source, profile='prova', progress=messages.append, cancel=token)
            self.assertTrue(reached)
            self.assertTrue(all(c.poll() is not None for c in children))
            self.assertIn('menu (3).mp4', messages[0])
            self.assertEqual(set(base.iterdir()), set(before))
            self.assertEqual({path: motor.sha256(path) for path in before}, before)
            print(f'Autoname cancel real + cleanup: {time.monotonic() - started:.3f}s', flush=True)
            small = copy.deepcopy(project)
            small.duration = .2
            result = services.render_project(small, None, source, profile='prova')
            self.assertEqual(result, base / 'menu (3).mp4')
            self.assertTrue(result.is_file())
            self.assertEqual({path: motor.sha256(path) for path in before}, before)
            render = motor.render_animated
            late = base / 'tardio.mp4'
            def race(*args):
                render(*args)
                late.write_bytes(b'terceiro tardio real')
            with patch.object(motor, 'render_animated', side_effect=race):
                with self.assertRaisesRegex(motor.ErroProjeto, 'existe.*exportar novamente'):
                    services.render_project(small, None, late, profile='prova')
            self.assertEqual(late.read_bytes(), b'terceiro tardio real')
            self.assertFalse(list(base.glob('.tardio.mp4*')))


class AutoNameGUIResultTests(unittest.TestCase):
    def test_callback_gui_mostra_caminho_real_sem_oferecer_substituir(self):
        from tests.test_gui_janela import WindowLogicTests, Var
        from unittest.mock import Mock
        import menu_gui
        case = WindowLogicTests()
        case.setUp()
        app = case.app
        app.adopt = Mock()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        selected = Path(temporary.name) / 'menu (2).mp4'
        selected.write_bytes(b'saida do servico simulado; sem janela real')
        with patch.object(menu_gui.filedialog, 'asksaveasfilename', return_value=str(selected.with_name('menu.mp4'))) as dialog, \
                patch.object(services, 'render_project', return_value=selected):
            app.make_export()
            operation, done = case.jobs[0]
            done(operation(lambda message: None))
        self.assertEqual(app.last_catalog, selected)
        self.assertEqual(app.last_catalog, selected)
        self.assertIs(dialog.call_args.kwargs['confirmoverwrite'], False)
