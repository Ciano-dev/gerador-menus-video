"""Cancelamento por job, processos reais e publicação exclusiva."""
import errno
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media_runtime as runtime
import menu_video as motor


class TokenTests(unittest.TestCase):
    def test_precancelado_nao_cria_filho_e_tokens_sao_isolados(self):
        self.assertTrue(hasattr(runtime, 'CancellationToken'), 'Falta token de cancelamento por job')
        token = runtime.CancellationToken()
        other = runtime.CancellationToken()
        self.assertTrue(token.cancel())
        self.assertFalse(token.cancel())
        other.check()
        with patch.object(subprocess, 'Popen') as spawn:
            with self.assertRaises(runtime.Cancelled):
                motor.run_media([sys.executable, '-c', 'raise SystemExit(9)'], cancel=token)
        spawn.assert_not_called()
        with self.assertRaises(runtime.Cancelled):
            with runtime.cancellation_scope(token):
                motor.run_media([sys.executable, '-c', 'pass'])
        self.assertEqual(motor.run_media([sys.executable, '-c', 'pass']).returncode, 0)


class ProcessTests(unittest.TestCase):
    def test_ffmpeg_de_outro_job_permanece_vivo(self):
        arguments = [motor.resolve_tool('ffmpeg'), '-nostdin', '-v', 'error', '-re',
                     '-f', 'lavfi', '-i', 'testsrc2=s=64x64:r=30', '-t', '5', '-f', 'null', '-']
        other = subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, **runtime.no_console_options())
        token = runtime.CancellationToken()
        timer = threading.Timer(.15, token.cancel)
        timer.start()
        try:
            with self.assertRaises(runtime.Cancelled):
                motor.run_media(arguments, cancel=token)
            self.assertIsNone(other.poll(), 'Cancelar um job não pode encerrar outro FFmpeg')
        finally:
            timer.join()
            other.terminate()
            try:
                other.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                other.kill()
                other.communicate(timeout=2)
            other.stdout.close()
            other.stderr.close()

    def test_ffmpeg_real_cancelado_rapido_com_pipes_e_reap(self):
        token = runtime.CancellationToken()
        children = []
        original = subprocess.Popen
        def spawn(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        timer = threading.Timer(.2, token.cancel)
        started = time.monotonic()
        timer.start()
        try:
            with patch.object(subprocess, 'Popen', side_effect=spawn):
                with self.assertRaises(runtime.Cancelled):
                    motor.run_media([motor.resolve_tool('ffmpeg'), '-nostdin', '-v', 'info',
                        '-re', '-f', 'lavfi', '-i', 'testsrc2=s=64x64:r=30',
                        '-t', '2', '-f', 'null', '-'], timeout=5, cancel=token)
        finally:
            timer.join()
        elapsed = time.monotonic() - started
        print(f'FFmpeg cancel real: {elapsed:.3f}s; pid={children[0].pid}; rc={children[0].returncode}', flush=True)
        self.assertLess(elapsed, 1.5)
        self.assertIsNotNone(children[0].poll())
        self.assertTrue(children[0].stdout.closed)
        self.assertTrue(children[0].stderr.closed)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cancel-publish-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.catalog = self.base / 'ready.mp4'
        self.catalog.write_bytes(b'complete' * 200000)
        self.output = self.base / 'output.mp4'

    def test_precancelado_impede_link_e_fallback(self):
        token = runtime.CancellationToken()
        token.cancel()
        for fallback in (False, True):
            with self.subTest(fallback=fallback), patch.object(motor.os, 'link',
                    side_effect=OSError(errno.EXDEV, 'FAT') if fallback else None) as link:
                with self.assertRaises(runtime.Cancelled):
                    motor.publish_mp4(self.catalog, self.output, cancel=token)
                link.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_sucesso_link_ou_fallback_vence_clique_tardio(self):
        for fallback in (False, True):
            token = runtime.CancellationToken()
            output = self.base / f'final-{fallback}.mp4'
            if fallback:
                with patch.object(motor.os, 'link', side_effect=OSError(errno.EXDEV, 'FAT')):
                    motor.publish_mp4(self.catalog, output, cancel=token)
            else:
                motor.publish_mp4(self.catalog, output, cancel=token)
            self.assertFalse(token.cancel())
            self.assertEqual(output.read_bytes(), self.catalog.read_bytes())


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gui_servicos as services
        from gui_autoria import Project
        cls.temp = tempfile.TemporaryDirectory(prefix='cancel-render-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        # Parent real + '..' reproduz aliases sem exigir symlink no Windows.
        alias_parent = cls.base / 'alias-parent'
        alias_parent.mkdir()
        cls.source = alias_parent / '..' / 'original.mp4'
        cls.music = alias_parent / '..' / 'original.wav'
        ffmpeg = motor.resolve_tool('ffmpeg')
        motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
            'testsrc2=s=64x64:r=30:duration=0.4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(cls.source)])
        motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
            'sine=duration=0.4:sample_rate=48000', str(cls.music)])
        cls.project = Project(title='Cancelar', duration=600)
        cls.project.add_video(services.inspect_media(cls.source))
        cls.project.edit_scene(0, 'Sintético', 0, .2)
        cls.project.music = services.inspect_media(cls.music, audio=True)
        cls.project_path = services.save_project(cls.project, cls.base / 'original.menu')
        cls.historical = cls.base / 'historico.mp4'
        cls.historical.write_bytes(b'preservar historico')
        cls.hashes = {p: motor.sha256(p) for p in (cls.source, cls.music, cls.project_path, cls.historical)}

    def test_render_real_cancelado_na_composicao_limpa_e_repete(self):
        import copy
        import gui_servicos as services
        token = runtime.CancellationToken()
        output = self.base / 'cancelado.mp4'
        composition = threading.Event()
        original = subprocess.Popen
        children = []
        def spawn(args, **kwargs):
            child = original(args, **kwargs)
            children.append(child)
            if '-filter_complex' in args:
                composition.set()
            return child
        def request():
            if composition.wait(5):
                time.sleep(.1)
                token.cancel()
        thread = threading.Thread(target=request)
        thread.start()
        started = time.monotonic()
        try:
            with patch.object(subprocess, 'Popen', side_effect=spawn):
                with self.assertRaises(runtime.Cancelled):
                    services.render_project(copy.deepcopy(self.project), None, output, profile='prova', cancel=token)
        finally:
            thread.join(6)
        self.assertTrue(composition.is_set())
        elapsed = time.monotonic() - started
        print(f'Render real cancel + cleanup: {elapsed:.3f}s; filhos={len(children)}', flush=True)
        self.assertLess(elapsed, 6)
        self.assertTrue(all(c.poll() is not None for c in children))
        self.assertFalse(output.exists())
        self.assertFalse(list(self.base.glob('.cancelado.mp4*')))
        self.assertEqual({p: motor.sha256(p) for p in self.hashes}, self.hashes)
        retry = copy.deepcopy(self.project)
        retry.duration = .2
        result = services.render_project(retry, None, self.base / 'repetido.mp4', profile='prova',
                                         cancel=runtime.CancellationToken())
        self.assertTrue(result.is_file())
        result.unlink()


    def test_cancel_antes_e_durante_cada_fase_de_midia_e_publicacao(self):
        import copy
        import gui_servicos as services
        for source in (self.source, self.music):
            self.assertNotEqual(str(source), str(source.resolve()))
            self.assertTrue(source.samefile(source.resolve()))
        def phase(args):
            probe = Path(args[0]).stem.lower() == 'ffprobe'
            last = Path(args[-1]).name
            inputs = ([Path(args[-1])] if probe else
                      [Path(args[index + 1]) for index, arg in enumerate(args[:-1]) if arg == '-i'])
            def has_input(source):
                return any(path.is_file() and path.samefile(source) for path in inputs)
            if probe:
                if has_input(self.source):
                    return 'probe-video'
                if has_input(self.music):
                    return 'probe-musica'
                if last == 'trecho-01.mp4':
                    return 'contagem-segmento' if '-count_frames' in args else 'probe-segmento'
                if last == '00-APRESENTACAO.mp4':
                    return 'probe-final'
            if 'measure.txt' in ' '.join(args):
                return 'drawtext'
            if last == 'trecho-01.mp4':
                return 'segmento'
            if '-filter_complex' in args:
                return 'composicao'
            if '-f' in args and args[-2:] == ['null', '-']:
                if has_input(self.source):
                    return 'decode-video'
                if has_input(self.music):
                    return 'decode-musica'
                if any(path.name == '00-APRESENTACAO.mp4' for path in inputs):
                    return 'decode-final'
            return None
        stages = ('probe-video', 'decode-video', 'probe-musica', 'decode-musica', 'drawtext',
                  'segmento', 'probe-segmento', 'contagem-segmento', 'composicao', 'decode-final', 'probe-final')
        real_run, real_spawn = motor.run_media, subprocess.Popen
        for stage in stages:
            for when in ('antes', 'durante'):
                token = runtime.CancellationToken()
                reached, children, temporary = [], [], []
                output = self.base / f'fase-{stage}-{when}.mp4'
                project = copy.deepcopy(self.project)
                project.duration = .2
                original = copy.deepcopy(project)
                def run(args, **kwargs):
                    if when == 'antes' and phase(args) == stage:
                        reached.append(stage)
                        token.cancel()
                    return real_run(args, **kwargs)
                def spawn(args, **kwargs):
                    child = real_spawn(args, **kwargs)
                    children.append(child)
                    if when == 'durante' and phase(args) == stage:
                        reached.append(stage)
                        token.cancel()
                    return child
                mkdtemp = tempfile.mkdtemp
                def temporary_dir(*args, **kwargs):
                    path = mkdtemp(*args, **kwargs)
                    temporary.append(Path(path))
                    return path
                with self.subTest(stage=stage, when=when), \
                        patch.object(motor, 'run_media', side_effect=run), \
                        patch.object(subprocess, 'Popen', side_effect=spawn), \
                        patch.object(tempfile, 'mkdtemp', side_effect=temporary_dir):
                    with self.assertRaises(runtime.Cancelled):
                        services.render_project(project, None, output, profile='prova', cancel=token)
                    self.assertEqual(reached, [stage])
                    self.assertTrue(all(child.poll() is not None for child in children))
                    self.assertTrue(all(not path.exists() for path in temporary))
                    self.assertFalse(output.exists())
                    self.assertFalse(list(self.base.glob(f'.{output.name}*')))
                    self.assertEqual(project, original)
        output = self.base / 'fase-publicacao.mp4'
        token = runtime.CancellationToken()
        real_publish = motor.publish_mp4
        def publish(*args, **kwargs):
            token.cancel()
            return real_publish(*args, **kwargs)
        with patch.object(motor, 'publish_mp4', side_effect=publish):
            with self.assertRaises(runtime.Cancelled):
                services.render_project(project, None, output, profile='prova', cancel=token)
        self.assertFalse(output.exists())
        self.assertEqual({p: motor.sha256(p) for p in self.hashes}, self.hashes)

    def test_lock_substituido_nao_e_apagado_no_cancelamento(self):
        import copy
        import gui_servicos as services
        token = runtime.CancellationToken()
        output = self.base / 'lock-substituido.mp4'
        lock_path = self.base / '.lock-substituido.mp4.menu-video.lock'
        def render(*args):
            if os.name == 'nt':
                # Handle aberto impede substituição no Windows.
                with self.assertRaises(PermissionError):
                    lock_path.unlink()
            else:
                lock_path.unlink()
                lock_path.write_bytes(b'lock de terceiro')
            token.cancel()
            token.check()
        with patch.object(motor, 'render_animated', side_effect=render):
            with self.assertRaises(runtime.Cancelled):
                services.render_project(copy.deepcopy(self.project), None, output, profile='prova', cancel=token)
        if os.name != 'nt':
            self.assertEqual(lock_path.read_bytes(), b'lock de terceiro')
            lock_path.unlink()
        else:
            self.assertFalse(lock_path.exists())

    def test_previa_precancelada_remove_diretorio_exclusivo(self):
        import gui_servicos as services
        token = runtime.CancellationToken()
        token.cancel()
        created = []
        original = tempfile.mkdtemp
        def mkdtemp(*args, **kwargs):
            path = original(*args, **kwargs)
            created.append(Path(path))
            return path
        with patch.object(tempfile, 'mkdtemp', side_effect=mkdtemp), \
                patch.object(subprocess, 'Popen') as spawn:
            with self.assertRaises(runtime.Cancelled):
                services.preview_project(self.project, cancel=token)
        spawn.assert_not_called()
        self.assertTrue(created)
        self.assertTrue(all(not p.exists() for p in created))



class ProcessEscalationTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'SIGTERM ignorável só no POSIX; FFmpeg real também roda no Windows.')
    def test_filho_real_ignora_terminate_e_kill_drena_pipes_e_reap(self):
        with tempfile.TemporaryDirectory(prefix='cancel-pipes-') as tmp:
            ready = Path(tmp) / 'ready'
            token = runtime.CancellationToken()
            code = ('import signal,sys,time,pathlib; '
                    'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                    'sys.stdout.buffer.write(b"o"*262144); sys.stdout.flush(); '
                    'sys.stderr.buffer.write(b"e"*262144); sys.stderr.flush(); '
                    'pathlib.Path(sys.argv[1]).touch(); time.sleep(10)')
            children = []
            original = subprocess.Popen
            def spawn(*args, **kwargs):
                child = original(*args, **kwargs)
                children.append(child)
                return child
            def request():
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.005)
                token.cancel()
            requester = threading.Thread(target=request)
            requester.start()
            started = time.monotonic()
            try:
                with patch.object(subprocess, 'Popen', side_effect=spawn):
                    with self.assertRaises(runtime.Cancelled):
                        motor.run_media([sys.executable, '-c', code, str(ready)], timeout=5, cancel=token)
            finally:
                requester.join(4)
            self.assertTrue(ready.is_file(), 'Ambos pipes devem ter sido drenados antes do cancel')
            self.assertEqual(children[0].returncode, -9)
            self.assertTrue(children[0].stdout.closed and children[0].stderr.closed)
            self.assertLess(time.monotonic() - started, 2)
            print(f'Terminate ignorado -> kill + reap/drain: {time.monotonic() - started:.3f}s', flush=True)

    def test_api_cancelavel_preserva_erro_timeout_e_opcao_windows(self):
        with self.assertRaisesRegex(motor.ErroProjeto, 'tempo limite'):
            motor.run_media([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=.1,
                            cancel=runtime.CancellationToken())
        real_spawn = subprocess.Popen
        def spawn_cp1252(args, **kwargs):
            # Somente este filho usa cp1252; o decoder real continua UTF-8.
            kwargs['env'] = dict(os.environ, PYTHONIOENCODING='cp1252')
            return real_spawn(args, **kwargs)
        with patch.object(subprocess, 'Popen', side_effect=spawn_cp1252), \
                self.assertRaisesRegex(motor.ErroProjeto, 'mídia inválida.*erro sintético'):
            motor.run_media([sys.executable, '-c',
                             'import sys; sys.stderr.buffer.write("erro sintético".encode("utf-8")); sys.exit(2)'],
                            cancel=runtime.CancellationToken())
        child = unittest.mock.MagicMock()
        child.returncode = 0
        child.communicate.return_value = (b'', b'')
        with patch.object(runtime.sys, 'platform', 'win32'), \
                patch.object(subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), \
                patch.object(subprocess, 'Popen', return_value=child) as spawn:
            motor.run_media(['ffmpeg', '-version'], cancel=runtime.CancellationToken())
        self.assertEqual(spawn.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(spawn.call_args.kwargs['creationflags'], 0x08000000)


class PublicationEdgesTests(PublicationTests):
    def test_cancel_durante_copy_chunked_rollback_so_proprio_inclusive_substituicao(self):
        path_open = Path.open
        for substitute in (False, True):
            token = runtime.CancellationToken()
            chunks = []
            output = self.base / f'chunk-{substitute}.mp4'
            class Target:
                def __init__(self, stream):
                    self.stream = stream
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    self.stream.close()
                    if substitute:
                        output.unlink()
                        output.write_bytes(b'substituto externo')
                def fileno(self):
                    return self.stream.fileno()
                def write(self, chunk):
                    chunks.append(len(chunk))
                    result = self.stream.write(chunk)
                    token.cancel()
                    return result
            def opened(path, mode='r', *args, **kwargs):
                stream = path_open(path, mode, *args, **kwargs)
                return Target(stream) if path == output and mode == 'xb' else stream
            with self.subTest(substitute=substitute), \
                    patch.object(motor.os, 'link', side_effect=OSError(errno.EXDEV, 'FAT')), \
                    patch.object(Path, 'open', new=opened):
                with self.assertRaises(runtime.Cancelled):
                    motor.publish_mp4(self.catalog, output, cancel=token)
            self.assertEqual(chunks, [1024 * 1024])
            if substitute:
                self.assertEqual(output.read_bytes(), b'substituto externo')
            else:
                self.assertFalse(output.exists())
            self.assertEqual(len(self.catalog.read_bytes()), 1600000)

    def test_cancel_apos_copy_antes_commit_e_falha_cleanup_honesta(self):
        token = runtime.CancellationToken()
        path_open, unlink = Path.open, Path.unlink
        output = self.output
        class Target:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self.stream
            def __exit__(self, *args):
                self.stream.close()
                token.cancel()
            def fileno(self):
                return self.stream.fileno()
            def write(self, chunk):
                return self.stream.write(chunk)
        def opened(path, mode='r', *args, **kwargs):
            stream = path_open(path, mode, *args, **kwargs)
            return Target(stream) if path == output and mode == 'xb' else stream
        def cleanup(path, *args, **kwargs):
            if path == output:
                raise PermissionError('cleanup negado')
            return unlink(path, *args, **kwargs)
        with patch.object(motor.os, 'link', side_effect=OSError(errno.EXDEV, 'FAT')), \
                patch.object(Path, 'open', new=opened), patch.object(Path, 'unlink', new=cleanup):
            with self.assertRaisesRegex(PermissionError, 'cleanup negado'):
                motor.publish_mp4(self.catalog, output, cancel=token)
        self.assertTrue(output.exists(), 'Falha de cleanup deve ser reportada honestamente')

    def test_link_exclusivo_concorrente_preservado_e_commit_serializa_cancel(self):
        self.output.write_bytes(b'terceiro')
        with self.assertRaisesRegex(motor.ErroProjeto, 'existe'):
            motor.publish_mp4(self.catalog, self.output, cancel=runtime.CancellationToken())
        self.assertEqual(self.output.read_bytes(), b'terceiro')
        self.output.unlink()
        token = runtime.CancellationToken()
        original = os.link
        cancel_started = threading.Event()
        accepted = []
        def request():
            cancel_started.set()
            accepted.append(token.cancel())
        def link(*args):
            thread.start()
            self.assertTrue(cancel_started.wait(1))
            original(*args)
        thread = threading.Thread(target=request)
        with patch.object(motor.os, 'link', side_effect=link):
            motor.publish_mp4(self.catalog, self.output, cancel=token)
        thread.join(2)
        self.assertEqual(accepted, [False])
        self.assertEqual(self.output.read_bytes(), self.catalog.read_bytes())
