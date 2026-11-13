from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch
from test_license_gates import deny_legacy_licensing
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import media_runtime as runtime
from gui_trabalho import Worker
import menu_gui
from test_gui_janela import Var


class WorkerCancelTests(unittest.TestCase):
    def test_cancel_tem_evento_distinto_so_apos_cleanup_e_worker_repete(self):
        worker = Worker()
        token = runtime.CancellationToken()
        entered, release, cleaned = threading.Event(), threading.Event(), threading.Event()
        def operation(progress):
            try:
                entered.set()
                release.wait(2)
                token.check()
            finally:
                cleaned.set()
        worker.submit(operation)
        self.assertTrue(entered.wait(1))
        token.cancel()
        self.assertTrue(worker.busy)
        self.assertFalse(cleaned.is_set())
        release.set()
        events = []
        deadline = time.monotonic() + 2
        while worker.busy and time.monotonic() < deadline:
            events.extend(worker.poll())
            time.sleep(.005)
        self.assertTrue(cleaned.is_set())
        self.assertEqual([e.kind for e in events], ['cancelled'])
        worker.submit(lambda progress: 42)
        while worker.busy and time.monotonic() < deadline:
            events.extend(worker.poll())
            time.sleep(.005)
        self.assertEqual(events[-1].value, 42)


class UICancelTests(unittest.TestCase):
    def setUp(self):
        self.app = menu_gui.App.__new__(menu_gui.App)
        app = self.app
        app.root = Mock()
        app.worker = Worker()
        app.controls = [Mock()]
        app.progress = Mock()
        app.cancel_button = Mock()
        app.status_var = Var('Pronto')
        app._cancel_token = None
        app._cancel_requested = False
        app._close_after_job = False
        app._closed = False
        app.update_duration_mode = Mock()
        app.last_catalog = Path('historico.mp4')
        app.show_error = Mock()
        app.confirm_discard = Mock(return_value=False)
        app.shutdown = Mock()

    def drain(self):
        deadline = time.monotonic() + 3
        while self.app.worker.busy and time.monotonic() < deadline:
            self.app.poll()
            time.sleep(.005)
        self.assertFalse(self.app.worker.busy)

    def test_cancelar_importacao_parcial_musica_reabertura_preserva_estado_e_retry(self):
        import copy
        from gui_autoria import Project, MediaInfo
        for route in ('videos', 'music', 'open'):
            with self.subTest(route=route):
                self.setUp()
                app = self.app
                app.project = Project(duration=7, music=MediaInfo(Path('anterior.wav'), 8))
                app.project.add_video(MediaInfo(Path('anterior.mp4'), 4))
                before = copy.deepcopy(app.project)
                app.project_path = Path('anterior.menu')
                app.duration_seconds_var = Var('23.5')
                app.scene_name_var = Var('Edição pendente')
                app.capture = lambda: copy.deepcopy(app.project)
                app.adopt = Mock()
                app.confirm_discard = Mock(return_value=True)
                calls = []
                def inspect(path, **kwargs):
                    calls.append(path)
                    if route != 'videos' or len(calls) == 2:
                        runtime.report_progress('Analisando duração da faixa')
                        runtime.current_cancel().cancel()
                    return MediaInfo(Path(path), 3)
                def reopen(path):
                    runtime.current_cancel().cancel()
                    return Project(title='Outro projeto')
                with patch.object(menu_gui.filedialog, 'askopenfilenames', return_value=('primeiro.mkv', 'segundo.mkv')), \
                        patch.object(menu_gui.filedialog, 'askopenfilename', return_value='novo.menu'), \
                        patch.object(menu_gui.services, 'inspect_media', side_effect=inspect), \
                        patch.object(menu_gui.services, 'open_project', side_effect=reopen):
                    getattr(app, {'videos': 'choose_videos', 'music': 'choose_music', 'open': 'choose_open'}[route])()
                    self.drain()
                self.assertEqual(app.project, before)
                self.assertEqual(app.project_path, Path('anterior.menu'))
                self.assertEqual(app.last_catalog, Path('historico.mp4'))
                self.assertEqual(app.duration_seconds_var.get(), '23.5')
                self.assertEqual(app.scene_name_var.get(), 'Edição pendente')
                self.assertEqual(app.status_var.get(), 'Operação cancelada.')
                app.adopt.assert_not_called()
                app.show_error.assert_not_called()
                done = Mock()
                app.run_render(lambda progress: before, done, analysis=True)
                self.drain()
                done.assert_called_once_with(before)

    def test_callback_direto_busy_status_uma_vez_sem_popup_nem_resultado(self):
        release = threading.Event()
        self.addCleanup(release.set)
        done = Mock()
        def operation(progress):
            release.wait(2)
            progress('Progresso atrasado')
            runtime.current_cancel().check()
        self.assertTrue(hasattr(self.app, 'run_render'), 'Falta operação cancelável de geração na UI')
        self.app.run_render(operation, done)
        self.app.cancel_render()
        self.app.cancel_render()
        self.assertEqual(self.app.status_var.get(), 'Cancelando…')
        self.assertTrue(self.app.worker.busy)
        self.app.cancel_button.state.assert_called_with(['disabled'])
        self.app.action(lambda: self.fail('Outra operação durante cleanup'))()
        release.set()
        self.drain()
        self.assertEqual(self.app.status_var.get(), 'Geração cancelada.')
        done.assert_not_called()
        self.app.show_error.assert_not_called()
        self.assertEqual(self.app.last_catalog, Path('historico.mp4'))
        self.app.run_render(lambda progress: 'repetido', done)
        self.drain()
        done.assert_called_once_with('repetido')

    def test_submissao_repetida_preserva_token_ativo(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def operation(progress):
            release.wait(2)
            runtime.current_cancel().check()
        self.app.run_render(operation, Mock())
        token = self.app._cancel_token
        with self.assertRaises(RuntimeError):
            self.app.run_render(lambda progress: None, Mock())
        self.assertIs(self.app._cancel_token, token)
        self.app.cancel_render()
        release.set()
        self.drain()

    def test_close_aguarda_cancel_cleanup_depois_confirma_dirty(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def operation(progress):
            release.wait(2)
            runtime.current_cancel().check()
        self.assertTrue(hasattr(self.app, 'run_render'), 'Falta geração cancelável')
        self.app.run_render(operation, Mock())
        with patch.object(menu_gui.messagebox, 'askyesno', return_value=True) as confirm:
            self.app.action(self.app.close)()
        confirm.assert_called_once()
        self.assertTrue(self.app.worker.busy)
        self.app.shutdown.assert_not_called()
        self.app.confirm_discard.assert_not_called()
        release.set()
        self.drain()
        self.app.confirm_discard.assert_called_once()
        self.app.shutdown.assert_not_called()

    def test_close_recusado_nao_cancela_e_close_tardio_reporta_sucesso(self):
        release = threading.Event()
        self.addCleanup(release.set)
        done = Mock()
        def operation(progress):
            release.wait(2)
            runtime.current_cancel().commit(lambda: None)
            return 'completo'
        self.app.run_render(operation, done)
        started = time.monotonic()
        with patch.object(menu_gui.messagebox, 'askyesno', return_value=False):
            self.app.close()
        self.assertLess(time.monotonic() - started, .2)
        self.assertFalse(self.app._close_after_job)
        self.app._cancel_token.check()
        release.set()
        deadline = time.monotonic() + 2
        while not self.app.worker._events.qsize() and time.monotonic() < deadline:
            time.sleep(.005)
        with patch.object(menu_gui.messagebox, 'askyesno', return_value=True) as ask:
            self.app.close()
            self.app.close()
        ask.assert_called_once()
        self.assertNotEqual(self.app.status_var.get(), 'Cancelando…')
        self.drain()
        done.assert_called_once_with('completo')
        self.app.confirm_discard.assert_called_once()
        self.app.shutdown.assert_not_called()

    def test_close_se_job_termina_dentro_da_confirmacao_modal(self):
        release = threading.Event()
        self.addCleanup(release.set)
        done = Mock()
        self.app.run_render(lambda progress: release.wait(2), done)
        def confirm(*args, **kwargs):
            # askyesno executa um loop Tk aninhado: poll pode concluir o job.
            release.set()
            self.drain()
            return True
        with patch.object(menu_gui.messagebox, 'askyesno', side_effect=confirm):
            self.app.close()
        done.assert_called_once()
        self.app.confirm_discard.assert_called_once()
        self.assertFalse(self.app._close_after_job)
        self.app.shutdown.assert_not_called()

    def test_clique_tardio_preserva_sucesso_e_job_generico_nao_cancela(self):
        self.assertTrue(hasattr(self.app, 'run_render'), 'Falta geração cancelável')
        done = Mock()
        self.app.run_render(lambda progress: runtime.current_cancel().commit(lambda: None) or 'completo', done)
        deadline = time.monotonic() + 2
        while not self.app.worker._events.qsize() and time.monotonic() < deadline:
            time.sleep(.005)
        self.app.cancel_render()
        self.assertNotEqual(self.app.status_var.get(), 'Cancelando…')
        self.drain()
        done.assert_called_once_with('completo')
        self.app.run_job(lambda progress: 'salvo', Mock())
        self.app.cancel_render()
        self.app.cancel_button.state.assert_called_with(['disabled'])
        self.drain()


class UIConstructionTests(unittest.TestCase):
    def test_sem_label_validade_e_cancel_button_direto_fora_dos_controles(self):
        from unittest.mock import MagicMock
        from gui_autoria import Project
        app = menu_gui.App.__new__(menu_gui.App)
        app.root = MagicMock()
        app.project = Project()
        app.controls = []
        app._refreshing = False
        native_font = MagicMock()
        native_font.actual.return_value = 'Sans'
        native_font.metrics.return_value = 16
        with patch.object(menu_gui, 'ttk') as ttk, patch.object(menu_gui, 'tk') as tk, \
                patch.object(menu_gui.tkfont, 'nametofont', return_value=native_font), \
                deny_legacy_licensing() as imports:
            ttk.Button.side_effect = lambda *args, **kwargs: Mock()
            app._build()
        self.assertNotIn(app.cancel_button, app.controls)
        imports.assert_not_called()
        self.assertFalse(any(call.kwargs.get('text') == 'Validade persistente' for call in ttk.Label.call_args_list))
        cancel_calls = [call for call in ttk.Button.call_args_list if call.kwargs.get('text') == 'Cancelar']
        self.assertEqual(len(cancel_calls), 1)
        self.assertEqual(cancel_calls[0].kwargs['command'], app.cancel_render)
        self.assertEqual(cancel_calls[0].kwargs['state'], 'disabled')


class RealTkCancellationTests(unittest.TestCase):
    def test_event_loop_real_botao_cancela_ffmpeg_e_gera_novamente(self):
        import os
        import tempfile
        import tkinter as tk
        import gui_servicos as services
        import menu_video as motor
        import smoke_windows
        from gui_autoria import Project
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            if sys.platform.startswith('linux') and not os.environ.get('DISPLAY') and 'display' in str(exc).lower():
                self.skipTest(f'Display Linux realmente ausente: {exc}')
            raise
        with tempfile.TemporaryDirectory(prefix='cancel-tk-real-') as tmp:
            directory = Path(tmp)
            source = directory / 'sintetico.mp4'
            motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg')) + [
                '-f', 'lavfi', '-i', 'testsrc2=s=64x64:r=30:duration=0.4',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)])
            project = Project(title='Tk real', duration=.2)
            project.add_video(services.inspect_media(source))
            project.edit_scene(0, 'Sintético', 0, .2)
            app = menu_gui.App(root)
            threads, results, errors = [], [], []
            main_thread = threading.get_ident()
            app.status_var.trace_add('write', lambda *args: threads.append(threading.get_ident()))
            def done(result):
                results.append(result)
                app.shutdown()
            def failed(error):
                errors.append(str(error))
                if not app.worker.busy:
                    app.shutdown()
                else:
                    app.cancel_render()
                    root.after(50, finish_failure)
            def finish_failure():
                if app.worker.busy:
                    root.after(50, finish_failure)
                else:
                    app.shutdown()
            app.show_error = failed
            root.after(10, lambda: smoke_windows.cancel_then_render(
                app, directory / 'cancelamento', project, done, failed))
            root.after(15000, lambda: failed(RuntimeError('Tk real excedeu 15s')))
            try:
                root.mainloop()
            finally:
                if not app._closed and not app.worker.busy:
                    app.shutdown()
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 1)
            self.assertTrue(all(t == main_thread for t in threads))
            record = results[0]
            self.assertTrue(record['children_reaped'] and record['no_partials'] and record['retry_verified'])
            self.assertTrue(record['button_main_thread'] and record['sources_intact'])
            self.assertLess(record['seconds'], 12)
            print(f'Tk real cancel + cleanup: {record["seconds"]:.3f}s; heartbeats={record["heartbeats"]}', flush=True)
