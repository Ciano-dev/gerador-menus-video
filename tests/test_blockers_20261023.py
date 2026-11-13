"""Regressões dos blockers; integração lógica Tk sem display e mídia sintética."""
import copy
import errno
import math
import os
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

import menu_gui
import menu_anisio as motor
import gui_servicos as services
from gui_autoria import MediaInfo, Project
from gui_trabalho import Worker
from menu_anisio import ErroProjeto
from tests.test_gui_janela import Var


class RootDouble:
    def __init__(self):
        self.pending = {}
        self.sequence = 0

    def after(self, delay, callback):
        self.sequence += 1
        token = str(self.sequence)
        self.pending[token] = callback
        return token

    def after_cancel(self, token):
        self.pending.pop(token, None)

    def tick(self):
        token = next(iter(self.pending))
        self.pending.pop(token)()


class OpenProjectPollingTests(unittest.TestCase):
    def setUp(self):
        self.app = app = menu_gui.App.__new__(menu_gui.App)
        app.root = RootDouble()
        app.worker = Worker()
        app.cancel_button = Mock()
        app._cancel_token = None
        app._cancel_requested = app._close_after_job = app._closed = False
        app.project = Project(music=MediaInfo(Path('/local/faixa.wav'), 120))
        app.saved_state = copy.deepcopy(app.project)
        app.project_path = app.last_catalog = app.editing_index = None
        app._refreshing = False
        app._duration_notice_token = None
        for name in ('title', 'duration_mode', 'duration_minutes', 'duration_seconds',
                     'footer', 'scene_name', 'scene_start_hours', 'scene_start_minutes', 'scene_start_seconds',
                     'scene_end_hours', 'scene_end_minutes', 'scene_end_seconds', 'path', 'music',
                     'scene_path', 'status', 'result'):
            setattr(app, name + '_var', Var(''))
        app.color_vars = {key: Var(value) for key, value in app.project.colors.items()}
        app.color_swatches = {key: Mock() for key in app.project.colors}
        app.duration_entries = [Mock(), Mock()]
        app.controls = [Mock(), *app.duration_entries]
        app.progress = Mock()
        app.tree = Mock()
        app.tree.get_children.return_value = []
        app.refresh()
        app.duration_seconds_var.set('')
        app._poll_token = app.root.after(100, app.poll)

    def finish_job(self):
        deadline = time.monotonic() + 3
        while self.app.worker.busy and time.monotonic() < deadline:
            self.app.root.tick()
            time.sleep(.001)
        self.assertFalse(self.app.worker.busy)

    def assert_next_job_works(self):
        received = []
        self.app.run_job(lambda progress: 'seguinte', received.append)
        self.finish_job()
        self.assertEqual(received, ['seguinte'])
        self.assertEqual(len(self.app.root.pending), 1)

    def test_abrir_descarta_manual_incompleto_e_continua_poll_e_jobs(self):
        app = self.app
        loaded = Project(title='Projeto novo', duration=7)
        loaded.add_video(MediaInfo(Path('/local/nova.mp4'), 10))
        with patch.object(menu_gui.messagebox, 'askyesno', return_value=True) as discard, \
                patch.object(menu_gui.filedialog, 'askopenfilename', return_value='/local/novo.menu'), \
                patch.object(menu_gui.services, 'open_project', return_value=loaded) as opened, \
                patch.object(menu_gui.messagebox, 'showinfo') as notice:
            app.choose_open()
            self.assertTrue(app.worker.busy)
            self.finish_job()
        discard.assert_called_once()
        opened.assert_called_once_with(Path('/local/novo.menu'))
        notice.assert_not_called()
        self.assertEqual(app.project, loaded)
        self.assertEqual(app.saved_state, loaded)
        self.assertEqual(app.title_var.get(), 'Projeto novo')
        self.assertEqual(app.duration_seconds_var.get(), '7')
        self.assertEqual(app.project_path, Path('/local/novo.menu'))
        self.assertEqual(app.status_var.get(), 'Projeto aberto.')
        for widget in app.controls:
            self.assertEqual(widget.state.call_args.args[0], ['!disabled'])
        app.progress.stop.assert_called_once()
        self.assertEqual(len(app.root.pending), 1)
        self.assert_next_job_works()

    def test_callback_erro_conhecido_reporta_e_reagenda(self):
        self.app.project.music = None
        def done(value):
            raise ErroProjeto('erro no callback')
        with patch.object(self.app, 'show_error') as error:
            self.app.run_job(lambda progress: 42, done)
            self.finish_job()
        self.assertEqual(str(error.call_args.args[0]), 'erro no callback')
        self.assertEqual(len(self.app.root.pending), 1)
        self.assert_next_job_works()

    def test_callback_erro_inesperado_reagenda_mesmo_propagando(self):
        self.app.project.music = None
        def done(value):
            raise RuntimeError('erro inesperado no callback')
        self.app.run_job(lambda progress: 42, done)
        with self.assertRaisesRegex(RuntimeError, 'erro inesperado no callback'):
            self.finish_job()
        self.assertEqual(len(self.app.root.pending), 1)
        self.assert_next_job_works()


class ExclusivePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='blocker2-real-')
        cls.base = Path(cls.temporary.name)
        cls.source = cls.base / 'filmagem original.mp4'
        cls.track = cls.base / 'trilha original.wav'
        cls.ffmpeg, cls.ffprobe = motor.resolve_tool('ffmpeg'), motor.resolve_tool('ffprobe')
        motor.run_media(motor.ffmpeg_base(cls.ffmpeg) + [
            '-f', 'lavfi', '-i', 'testsrc2=size=64x36:rate=30:duration=0.2',
            '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p', str(cls.source)])
        with wave.open(str(cls.track), 'wb') as stream:
            stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\x00\x10\x00\x10' * 4800)
        cls.hashes = {p: motor.sha256(p) for p in (cls.source, cls.track)}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=self.base)
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.output = self.directory / 'menu.mp4'
        self.project = Project(title='Fallback real', duration=.1)
        self.project.add_video(services.inspect_media(self.source))
        self.project.music = services.inspect_media(self.track, audio=True)

    def tearDown(self):
        self.assertEqual({p: motor.sha256(p) for p in self.hashes}, self.hashes)
        self.assertFalse(list(self.directory.glob('.menu.mp4*')))

    def export(self):
        return services.render_project(self.project, None, self.output, profile='prova')

    def unsupported(self, *args, **kwargs):
        raise OSError(errno.EOPNOTSUPP, 'hardlink não suportado (simulado)')

    def test_sem_hardlink_exporta_mp4_real_exclusivo_sem_copiar_fontes(self):
        copied = []
        copyfileobj = motor.shutil.copyfileobj
        def observe(source, target, *args, **kwargs):
            self.assertTrue(self.output.is_file())
            self.assertEqual(self.output.stat().st_size, 0)
            self.assertNotIn(Path(source.name), self.hashes)
            copied.append((Path(source.name).name, motor.sha256(source.name)))
            return copyfileobj(source, target, *args, **kwargs)
        with patch.object(motor.os, 'link', side_effect=self.unsupported) as link, \
                patch.object(motor.shutil, 'copyfileobj', side_effect=observe):
            self.assertEqual(self.export(), self.output)
        link.assert_called_once()
        self.assertEqual(copied, [('00-APRESENTACAO.mp4', motor.sha256(self.output))])
        streams = motor.probe_media(self.ffprobe, self.output, count_frames=True)['streams']
        self.assertEqual([s['codec_type'] for s in streams], ['video', 'audio'])
        self.assertEqual((streams[0]['codec_name'], streams[0]['pix_fmt'], streams[0]['nb_read_frames']),
                         ('h264', 'yuv420p', '3'))
        motor.check_decode(self.ffmpeg, self.output)
        digest = motor.sha256(self.output)
        with self.assertRaisesRegex(ErroProjeto, 'existe'):
            motor.export_media(self.project.title, [], self.output, None, 'prova')
        self.assertEqual(motor.sha256(self.output), digest)
        self.assertEqual(list(self.directory.iterdir()), [self.output])
        print('FALLBACK_REAL', {'frames': 3, 'copias': copied,
              'fontes_sha256_antes_depois': {str(p.name): motor.sha256(p) for p in self.hashes}}, flush=True)

    def test_concorrente_surgido_durante_render_nao_e_truncado(self):
        render = motor.render_animated
        def race(*args):
            render(*args)
            self.output.write_bytes(b'terceiro durante render')
        with patch.object(motor.os, 'link', side_effect=self.unsupported), \
                patch.object(motor, 'render_animated', side_effect=race):
            with self.assertRaisesRegex(ErroProjeto, 'existe'):
                self.export()
        self.assertEqual(self.output.read_bytes(), b'terceiro durante render')

    def test_concorrente_surgido_ao_entrar_no_fallback_nao_e_truncado(self):
        def race(*args):
            self.output.write_bytes(b'terceiro antes da reserva')
            self.unsupported()
        with patch.object(motor.os, 'link', side_effect=race):
            with self.assertRaisesRegex(ErroProjeto, 'existe'):
                self.export()
        self.assertEqual(self.output.read_bytes(), b'terceiro antes da reserva')

    def test_symlink_quebrado_recusado_antes_de_abertura_xb_do_windows(self):
        target = self.directory / 'terceiro'
        path_open = Path.open
        opened = []
        def race(*args):
            self.output.symlink_to(target)
            self.unsupported()
        def windows_open(path, mode='r', *args, **kwargs):
            # Seam restrito ao xb da saída: emula seguir o symlink quebrado
            # no Windows; link, alvo, render e demais aberturas continuam reais.
            if path == self.output and mode == 'xb':
                self.assertTrue(path.is_symlink())
                self.assertFalse(target.exists())
                stream = path_open(target, mode, *args, **kwargs)
                opened.append(path)
                self.assertTrue(target.is_file())
                print('WINDOWS_XB_SEAM: opener chamado e alvo ausente criado', flush=True)
                return stream
            return path_open(path, mode, *args, **kwargs)
        with patch.object(motor.os, 'link', side_effect=race), \
                patch.object(Path, 'open', new=windows_open):
            with self.assertRaisesRegex(ErroProjeto, 'existe'):
                self.export()
        self.assertEqual(opened, [])
        self.assertTrue(self.output.is_symlink())
        self.assertTrue(os.path.samefile(self.output.readlink().parent, target.parent))
        self.assertEqual(self.output.readlink().name, target.name)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.directory.iterdir()), [self.output])
        self.output.unlink()

    def test_symlink_no_fallback_preserva_link_e_alvo_inclusive_quebrado(self):
        for existing in (False, True):
            with self.subTest(alvo_existe=existing):
                target = self.directory / 'terceiro'
                if existing:
                    target.write_bytes(b'alvo de terceiro')
                def race(*args):
                    self.output.symlink_to(target)
                    self.unsupported()
                with patch.object(motor.os, 'link', side_effect=race):
                    with self.assertRaisesRegex(ErroProjeto, 'existe'):
                        self.export()
                self.assertTrue(self.output.is_symlink())
                # Pais existem: identidade funciona com aliases e prefixo //?/.
                self.assertTrue(os.path.samefile(self.output.readlink().parent, target.parent))
                self.assertEqual(self.output.readlink().name, target.name)
                self.assertEqual(target.exists(), existing)
                if existing:
                    self.assertEqual(target.read_bytes(), b'alvo de terceiro')
                self.output.unlink()

    def test_falha_parcial_remove_apenas_arquivo_criado_pelo_fallback(self):
        def fail(source, target):
            self.assertEqual(Path(source.name).name, '00-APRESENTACAO.mp4')
            target.write(b'parcial')
            raise OSError(errno.EIO, 'falha parcial simulada')
        with patch.object(motor.os, 'link', side_effect=self.unsupported), \
                patch.object(motor.shutil, 'copyfileobj', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'falha parcial simulada'):
                self.export()
        self.assertFalse(os.path.lexists(self.output))

    def test_falha_parcial_nao_remove_terceiro_que_substituiu_a_reserva(self):
        third_party = self.directory / 'terceiro-preservado'
        third_party.write_bytes(b'outro arquivo de terceiro')
        handles = []
        def fail(source, target):
            handles.append(target)
            target.write(b'parcial')
            target.flush()
            if os.name == 'nt':
                handle = target.fileno()
                reserved = os.fstat(handle)
                with self.assertRaises(PermissionError) as raised:
                    self.output.unlink()
                self.assertEqual(raised.exception.winerror, 32)
                self.assertFalse(target.closed)
                self.assertEqual(target.fileno(), handle)
                current = self.output.lstat()
                self.assertEqual((current.st_dev, current.st_ino),
                                 (reserved.st_dev, reserved.st_ino))
                self.assertEqual(os.fstat(handle).st_size, len(b'parcial'))
                self.assertEqual(self.output.read_bytes(), b'parcial')
            else:
                self.output.unlink()
                self.output.write_bytes(b'terceiro substituiu reserva')
            raise OSError(errno.EIO, 'falha parcial controlada')
        with patch.object(motor.os, 'link', side_effect=self.unsupported), \
                patch.object(motor.shutil, 'copyfileobj', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'falha parcial controlada'):
                self.export()
        self.assertEqual(len(handles), 1)
        self.assertTrue(handles[0].closed)
        if os.name == 'nt':
            self.assertFalse(os.path.lexists(self.output))
            self.assertEqual(list(self.directory.iterdir()), [third_party])
        else:
            self.assertEqual(self.output.read_bytes(), b'terceiro substituiu reserva')
        self.assertEqual(third_party.read_bytes(), b'outro arquivo de terceiro')

    def test_erro_io_de_link_nao_dispara_fallback(self):
        for error in (errno.EIO, errno.EACCES, errno.EPERM, errno.ENOSPC):
            with self.subTest(errno=error), \
                    patch.object(motor.os, 'link', side_effect=OSError(error, 'erro de link')), \
                    patch.object(motor.shutil, 'copyfileobj') as copied:
                with self.assertRaises(OSError) as raised:
                    self.export()
                self.assertEqual(raised.exception.errno, error)
                copied.assert_not_called()
                self.assertFalse(os.path.lexists(self.output))


class NewOneFrameSelectionTests(unittest.TestCase):
    def setUp(self):
        from tests.test_gui_janela import WindowLogicTests
        case = WindowLogicTests()
        case.setUp()
        self.app = case.app
        self.scene = self.app.project.scenes[0]
        # A UI exibe inteiros: um corte já existente de um frame deve sobreviver.
        self.scene.source_duration = 181
        self.scene.start, self.scene.duration = 180, 1/30
        self.app.scene_start_minutes_var.set('3')
        self.app.scene_start_seconds_var.set('00')
        self.app.scene_end_minutes_var.set('3')
        self.app.scene_end_seconds_var.set('00')

    def test_captura_preserva_um_frame_em_03min_sem_edicao_dos_tempos(self):
        captured = self.app.capture(validate=True).scenes[0]
        self.assertEqual((captured.start, captured.duration), (180, 1/30))
        self.assertEqual((self.scene.start, self.scene.duration), (180, 1/30))
        # A criação precisa segue permitida no modelo, inclusive ao reabrir.
        self.app.project.edit_scene(0, 'Novo corte', 180, 1/30)
        self.assertEqual(self.app.project.scenes[0].duration, 1/30)

    def test_duracao_realmente_inferior_um_frame_recusada(self):
        for seconds in ('0', '0.03333333333333332', repr(math.nextafter(1/30, 0)),
                        repr(1599/48000)):
            with self.subTest(seconds=seconds):
                self.app.scene_end_seconds_var.set(seconds)
                if seconds == '0':
                    # 03:00 é a exibição inalterada do corte de um frame.
                    self.assertEqual(self.app.capture(validate=True).scenes[0].duration, 1/30)
                else:
                    with self.assertRaises(ErroProjeto):
                        self.app.capture(validate=True)
                with self.assertRaises(ErroProjeto):
                    self.app.project.edit_scene(0, 'Subframe', 180, float(seconds))
        self.app.scene_end_seconds_var.set('0')
        self.app.scene_end_minutes_var.set('2')
        with self.assertRaises(ErroProjeto):
            self.app.capture(validate=True)

    def test_edicao_inferior_nao_se_confunde_com_reexibicao_original(self):
        self.scene.start, self.scene.duration = 180, 1/30
        self.app.scene_end_seconds_var.set(repr(math.nextafter(1/30, 0)))
        with self.assertRaises(ErroProjeto):
            self.app.capture(validate=True)

    def test_fim_alem_da_fonte_e_fonte_insuficiente_recusados(self):
        for duration in (180, 180 + 1599/48000,
                         math.nextafter(180 + 1/30, 0)):
            with self.subTest(source_duration=duration):
                self.scene.source_duration = duration
                with self.assertRaises(ErroProjeto):
                    self.app.capture(validate=True)

    def test_modelo_recusa_fonte_menor_que_um_frame_sem_epsilon(self):
        for duration in (1599/48000, math.nextafter(1/30, 0)):
            with self.subTest(source_duration=duration):
                self.scene.source_duration = duration
                with self.assertRaises(ErroProjeto):
                    self.app.project.edit_scene(0, 'Um frame', 0, 1/30)

    def test_um_frame_terminando_exatamente_na_fonte_e_valido(self):
        self.scene.source_duration = 180 + 1/30
        captured = self.app.capture(validate=True).scenes[0]
        self.assertEqual((captured.start, captured.duration), (180, 1/30))

    def test_soma_decimal_no_limite_da_fonte_permanece_valida(self):
        self.scene.source_duration = .3
        self.scene.start, self.scene.duration = .1, .2
        self.app.scene_start_minutes_var.set('0')
        self.app.scene_start_seconds_var.set('0')
        self.app.scene_end_minutes_var.set('0')
        self.app.scene_end_seconds_var.set('0')
        captured = self.app.capture(validate=True).scenes[0]
        self.assertEqual((captured.start, captured.duration), (.1, .2))
