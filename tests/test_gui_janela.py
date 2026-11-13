import copy
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui_autoria import MediaInfo, Project
from menu_video import ErroProjeto


class Var:
    def __init__(self, value):
        self.value = value
        self.callbacks = []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for callback in self.callbacks:
            callback('', '', 'write')

    def trace_add(self, mode, callback):
        self.callbacks.append(callback)


class WindowLogicTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('menu_gui')
        self.app = self.module.App.__new__(self.module.App)
        from gui_trabalho import Worker
        self.app.worker = Worker()
        self.app.root = None
        self.app.status_var = Var('')
        self.app.scene_path_var = Var('')
        self.app.project = Project(duration=2)
        self.app.project.add_video(MediaInfo(Path('/local/cena.mp4'), 12))
        self.app.title_var = Var('Título pela janela')
        self.app.color_vars = {key: Var(value) for key, value in self.app.project.colors.items()}
        self.app.duration_mode_var = Var('manual')
        self.app.duration_minutes_var = Var('0')
        self.app.duration_seconds_var = Var('2')
        self.app.footer_var = Var(False)
        self.app.scene_name_var = Var('Nome editado')
        self.app.scene_start_hours_var = Var('0')
        self.app.scene_start_minutes_var = Var('0')
        self.app.scene_start_seconds_var = Var('1')
        self.app.scene_end_hours_var = Var('0')
        self.app.scene_end_minutes_var = Var('0')
        self.app.scene_end_seconds_var = Var('3')
        self.app.editing_index = 0
        self.jobs = []
        self.app.run_job = lambda operation, on_done: self.jobs.append((operation, on_done))

    def test_campos_pendentes_entram_no_snapshot_sem_mutar_modelo(self):
        before = copy.deepcopy(self.app.project)
        snapshot = self.app.capture(validate=True)
        self.assertEqual(snapshot.title, 'Título pela janela')
        self.assertEqual(snapshot.duration, 2)
        self.assertFalse(snapshot.show_footer)
        self.assertEqual((snapshot.scenes[0].name, snapshot.scenes[0].start), ('Nome editado', 1))
        self.assertEqual(self.app.project, before)

    def test_entrada_numerica_invalida_tem_erro_legivel(self):
        self.app.scene_start_seconds_var.set('abc')
        with self.assertRaisesRegex(ErroProjeto, 'número|segundos'):
            self.app.capture(validate=True)

    def test_adicionar_apenas_agenda_probe_no_worker(self):
        with patch.object(self.module.filedialog, 'askopenfilenames', return_value=('/local/novo.mp4',)), \
                patch.object(self.module.services, 'inspect_media') as inspect:
            self.app.choose_videos()
            inspect.assert_not_called()
            self.assertEqual(len(self.jobs), 1)
            inspect.return_value = MediaInfo(Path('/local/novo.mp4'), 4)
            result = self.jobs[0][0](lambda message: None)
            self.assertEqual(len(result.scenes), 2)
            self.assertEqual(result.scenes[0].name, 'Nome editado')

    def test_salvar_apenas_agenda_io_com_edicoes_pendentes(self):
        self.app.project_path = None
        with patch.object(self.module.filedialog, 'asksaveasfilename', return_value='/local/novo.json'), \
                patch.object(self.module.services, 'save_project') as save:
            self.app.save_as()
            save.assert_not_called()
            self.jobs[0][0](lambda message: None)
            self.assertEqual(save.call_args.args[0].scenes[0].name, 'Nome editado')
            self.assertEqual(save.call_args.args[1], Path('/local/novo.json'))

    def test_previa_e_exportacao_agendam_motor_e_nao_chamam_na_thread_ui(self):
        self.app.project_path = Path('/local/projeto.json')
        with patch.object(self.module.services, 'preview_project') as preview:
            self.app.make_preview()
            preview.assert_not_called()
            self.jobs.pop()[0](lambda message: None)
            self.assertEqual(preview.call_args.args[0].scenes[0].name, 'Nome editado')
        with patch.object(self.module.filedialog, 'asksaveasfilename', return_value='/local/entrega.mp4'), \
                patch.object(self.module.services, 'render_project') as render:
            self.app.make_export()
            render.assert_not_called()
            self.jobs.pop()[0](lambda message: None)
            self.assertEqual(render.call_args.args[2], Path('/local/entrega.mp4'))
            self.assertEqual(render.call_args.kwargs['profile'], '1080p')

    def test_reabrir_apenas_agenda_probe_no_worker(self):
        self.app.confirm_discard = lambda: True
        with patch.object(self.module.filedialog, 'askopenfilename', return_value='/local/projeto.json'), \
                patch.object(self.module.services, 'open_project') as reopen:
            self.app.choose_open()
            reopen.assert_not_called()
            self.jobs[0][0](lambda message: None)
            reopen.assert_called_once_with(Path('/local/projeto.json'))

    def test_sondagens_importacao_e_reabertura_tem_cancelamento_e_progresso(self):
        import media_runtime as runtime
        from unittest.mock import Mock
        for route in ('videos', 'music', 'open'):
            with self.subTest(route=route):
                before = copy.deepcopy(self.app.project)
                pending = self.app.scene_name_var.get(), self.app.duration_seconds_var.get()
                self.app.confirm_discard = lambda: True
                self.jobs.clear()
                with patch.object(self.module.filedialog, 'askopenfilenames', return_value=('/local/novo.mkv',)), \
                        patch.object(self.module.filedialog, 'askopenfilename', return_value='/local/novo.menu'):
                    getattr(self.app, {'videos': 'choose_videos', 'music': 'choose_music', 'open': 'choose_open'}[route])()
                token = getattr(self.app, '_cancel_token', None)
                self.assertIsNotNone(token, 'Sondagem de entrada precisa ativar Cancelar')
                progress = Mock()
                def inspect(*args, **kwargs):
                    self.assertIs(runtime.current_cancel(), token)
                    runtime.report_progress('Analisando duração da faixa')
                    token.cancel()
                    return MediaInfo(Path('/local/novo.mkv'), 4)
                with patch.object(self.module.services, 'inspect_media', side_effect=inspect), \
                        patch.object(self.module.services, 'open_project', side_effect=inspect), \
                        self.assertRaises(runtime.Cancelled):
                    self.jobs[0][0](progress)
                self.assertEqual(self.app.project, before)
                self.assertEqual((self.app.scene_name_var.get(), self.app.duration_seconds_var.get()), pending)
                self.assertEqual(progress.call_args.args[0].fraction, None)
                self.assertIsNone(runtime.current_cancel())
                self.assertIsNone(runtime.current_progress())

    def test_render_concluido_aplica_edicoes_a_lista_sem_marcar_json_salvo(self):
        self.app.project_path = Path('/local/projeto.json')
        self.app.saved_state = copy.deepcopy(self.app.project)
        before = copy.deepcopy(self.app.saved_state)
        self.app.adopt = unittest.mock.Mock()
        self.app.finished_render = unittest.mock.Mock()
        self.app.make_preview()
        self.jobs[0][1](Path('/local/previa/00-APRESENTACAO.mp4'))
        self.assertEqual(self.app.adopt.call_args.args[0].scenes[0].name, 'Nome editado')
        self.assertEqual(self.app.saved_state, before)

    def test_remover_descarta_edicao_invalida_da_cena_removida(self):
        self.app.scene_start_seconds_var.set('inválido')
        self.app.adopt = unittest.mock.Mock()
        self.app.remove_scene()
        result = self.app.adopt.call_args.args[0]
        self.assertEqual(result.scenes, [])
        self.assertEqual(result.title, 'Título pela janela')

    def test_cor_digitada_invalida_pode_ser_corrigida_no_seletor(self):
        self.app.color_vars['fundo'].set('inválido')
        with patch.object(self.module.colorchooser, 'askcolor',
                          return_value=((255, 255, 255), '#ffffff')) as chooser:
            self.app.choose_color('fundo')
        self.assertEqual(chooser.call_args.kwargs['color'], '#ffffff')
        self.assertEqual(self.app.color_vars['fundo'].get(), '#ffffff')

    def test_save_dialog_nao_oferece_confirmar_sobrescrita_que_sera_recusada(self):
        with patch.object(self.module.filedialog, 'asksaveasfilename', return_value='') as dialog:
            self.app.save_as()
        self.assertIs(dialog.call_args.kwargs.get('confirmoverwrite'), False)

    def test_nomes_de_dispositivo_windows_recusados_antes_do_motor(self):
        self.app.project_path = Path('/local/projeto.json')
        for name in ('CON', 'nul.txt', 'COM1', 'LPT9.entrega'):
            with self.subTest(name=name), \
                    patch.object(self.module.filedialog, 'asksaveasfilename', return_value='/local/'+name+'.mp4'), \
                    self.assertRaisesRegex(ErroProjeto, 'nome|reservad'):
                self.app.make_export()
        self.assertEqual(self.jobs, [])


class WindowSmokeTests(unittest.TestCase):
    def test_cria_janela_real_quando_existe_display(self):
        module = importlib.import_module('menu_gui')
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f'Sem display Tk disponível: {exc}')
        root.withdraw()
        app = module.App(root)
        try:
            root.update_idletasks()
            self.assertTrue(app.tree.winfo_exists())
            self.assertEqual(str(app.progress.cget('mode')), 'indeterminate')
            self.assertEqual(root.cget('background'), '#252525')
        finally:
            app.shutdown()


if __name__ == '__main__':
    unittest.main()
