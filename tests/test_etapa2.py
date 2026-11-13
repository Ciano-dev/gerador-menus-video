"""Regressões da etapa 2: lógica headless separada de aceite nativo."""
import copy
import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from gui_autoria import Project, MediaInfo
import menu_gui as gui
import gui_servicos as services

class DuracaoInicialTests(unittest.TestCase):
    def test_novo_projeto_manual_um_minuto(self):
        project = Project()
        self.assertEqual(project.duration_mode, 'manual')
        self.assertEqual(project.duration, 60)
        self.assertEqual(project.effective_duration(), 60)
        self.assertEqual(gui.App.format_time(project.effective_duration()), '01:00')

class MusicaTests(unittest.TestCase):
    def app(self):
        from tests.test_gui_janela import WindowLogicTests, Var
        case = WindowLogicTests(); case.setUp()
        app = case.app
        app.adopt = Mock(side_effect=lambda project: setattr(app, 'project', project))
        return case, app

    def test_selecionar_musica_preserva_manual_aplicado_e_pendente_sem_popup(self):
        for pending in (False, True):
            with self.subTest(pendente=pending):
                case, app = self.app()
                app.duration_seconds_var.set('7' if pending else '2')
                app._duration_edited = pending
                track = MediaInfo(Path('/local/faixa.wav'), 402.25)
                with patch.object(gui.filedialog, 'askopenfilename', return_value=str(track.source)), patch.object(services, 'inspect_media', return_value=track), patch.object(gui.messagebox, 'showinfo') as info, patch.object(gui.messagebox, 'askyesno') as confirm:
                    app.choose_music()
                    operation, done = case.jobs.pop()
                    done(operation(lambda _: None))
                    info.assert_not_called(); confirm.assert_not_called()
                self.assertEqual(app.project.duration_mode, 'manual')
                self.assertEqual(app.project.duration, 2)
                self.assertEqual(app.duration_seconds_var.get(), '7' if pending else '2')
                self.assertEqual(app.duration_pending(), pending)
                self.assertEqual(app.project.music, track)
                self.assertEqual(app.scene_name_var.get(), 'Nome editado')
                self.assertEqual(app.scene_start_seconds_var.get(), '1')
                self.assertEqual(app.scene_end_seconds_var.get(), '3')
                app.adopt.assert_not_called()

    def test_trocar_faixa_respeita_modo_seguir_explicito(self):
        case, app = self.app()
        app.project.duration_mode = 'musica'
        app.project.music = MediaInfo(Path('/local/primeira.wav'), 120.125)
        app.duration_mode_var.set('musica')
        track = MediaInfo(Path('/local/segunda.wav'), 402.25)
        with patch.object(gui.filedialog, 'askopenfilename', return_value=str(track.source)), patch.object(services, 'inspect_media', return_value=track):
            app.choose_music()
            operation, done = case.jobs.pop(); done(operation(lambda _: None))
        self.assertEqual(app.project.duration_mode, 'musica')
        self.assertEqual(app.project.effective_duration(), track.duration)

    def test_seguir_explicito_preserva_manual_inativo_e_pendencias_de_trecho(self):
        case, app = self.app()
        app.duration_entries = []
        app.project.music = MediaInfo(Path('/local/faixa.wav'), 120.125)
        app.duration_seconds_var.set('7')
        app._duration_edited = True
        app.duration_mode_var.set('musica')
        with patch.object(gui.messagebox, 'showinfo') as info:
            app.update_duration_mode()
            info.assert_not_called()
        self.assertEqual(app.project.duration, 2)
        self.assertEqual(app.project.effective_duration(), 120.125)
        self.assertFalse(app.duration_pending())
        self.assertEqual(app.scene_name_var.get(), 'Nome editado')
        self.assertEqual(app.scene_start_seconds_var.get(), '1')
        app.duration_mode_var.set('manual')
        app.update_duration_mode()
        self.assertEqual(app.project.effective_duration(), 2)
        self.assertEqual(app.duration_seconds_var.get(), '2')

class AberturaExternaTests(unittest.TestCase):
    def test_abrir_video_so_saida_valida_fora_de_job(self):
        from tests.test_gui_janela import WindowLogicTests
        case = WindowLogicTests(); case.setUp()
        app = case.app
        app.last_catalog = Path('/ausente.mp4')
        with self.assertRaises(gui.ErroProjeto):
            app.open_last()
        self.assertEqual(case.jobs, [])
        with tempfile.TemporaryDirectory() as tmp:
            app.last_catalog = Path(tmp) / 'menu & filme.avi'
            app.last_catalog.write_bytes(b'conferido')
            with patch.object(services, 'open_player') as opener:
                app.open_last()
                opener.assert_not_called()
                operation, done = case.jobs.pop(); done(operation(lambda _: None))
                opener.assert_called_once_with(app.last_catalog)
            with patch.object(app.worker, 'busy', True):
                app.open_last(); app.open_folder()
            self.assertEqual(case.jobs, [])

    def test_pasta_windows_delega_sem_shell_ao_sistema(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'menu & filme.avi'; output.write_bytes(b'conferido')
            with patch.object(services.sys, 'platform', 'win32'), patch.object(services.os, 'startfile', create=True) as start, patch.object(services.subprocess, 'run') as run:
                services.open_output_folder(output)
                start.assert_called_once_with(str(output.parent.resolve()))
                run.assert_not_called()

class InterfaceContratoTests(unittest.TestCase):
    def test_hint_contextual_preserva_projetos_carregados(self):
        from tests.test_gui_janela import WindowLogicTests, Var
        case = WindowLogicTests(); case.setUp(); app = case.app
        app.duration_hint_var = Var('')
        app.project_path = None
        app.project = Project()
        app.refresh_duration_hint()
        self.assertIn('Duração inicial: 1 minuto.', app.duration_hint_var.get())
        app.project_path = Path('/legado.menu')
        app.project.duration = 15
        app.refresh_duration_hint()
        self.assertIn('00:15', app.duration_hint_var.get())
        self.assertNotIn('inicial', app.duration_hint_var.get())

    def test_contratos_de_controles_e_paleta_ui_independente(self):
        source = inspect.getsource(gui.App._build)
        for text in ('1. Vídeos e trechos', '2. Título e cores', '3. Música e duração',
                     '4. Exportar vídeo', 'Aplicar nome e trecho', 'Usar duração da música',
                     'Abrir último vídeo', 'self.open_last'):
            self.assertIn(text, source)
        self.assertEqual(Project().colors, {'fundo': '#101f33', 'texto': '#f3ead6', 'destaque': '#c7a362'})
        self.assertEqual(gui.UI_COLORS['background'], '#252525')


def interface_fixture():
    """Fixture artificial do validador, nunca prova de captura/DPI."""
    rows = []
    for scale in (100, 150):
        for width in (1024, 1366):
            row = {'scale': scale, 'size': [width, 768], 'tk_scaling': 4/3 * scale/100,
                   'new_manual_seconds': 60, 'legacy_seconds': 15, 'music_manual_seconds': 37.25,
                   'music_mode': 'musica', 'format_values': ['MP4', 'AVI'], 'format_readonly': True,
                   'alignment_values': ['esquerda', 'centralizado'],
                   'alignment_probes': [
                       {'alignment': 'esquerda', 'canvas_size': [384, 216],
                        'title': {'coords': [17.6, 14.8], 'bbox': [18, 15, 60, 25], 'anchor': 'nw', 'justify': 'left'}},
                       {'alignment': 'centralizado', 'canvas_size': [384, 216],
                        'title': {'coords': [192, 14.8], 'bbox': [170, 15, 214, 25], 'anchor': 'n', 'justify': 'center'}}],
                   'selection_preserved': True,
                   'pending_preserved': True, 'scrollable': True, 'time_columns_aligned': True,
                   'external_disabled': True, 'disabled_legible': True, 'background': '#252525',
                   'field': '#1e1e1e', 'focus': '#78b8ed', 'views': []}
            # Artificial native-report boundary; no actual Windows measurement claimed.
            labels = ['Novo projeto', 'Abrir projeto…', 'Salvar projeto como…',
                'Adicionar vídeos…', 'Remover', 'Subir', 'Descer', 'Aplicar nome e trecho',
                *['Escolher cor…']*3, 'Escolher música…', 'Retirar música',
                'Aplicar duração', 'Exportar vídeo (1080p)…', 'Abrir último vídeo',
                'Abrir pasta do vídeo', 'Cancelar']
            row['button_window'] = [0, 0, width, 768]
            row['scene_headings'] = ['Nome da cena', 'Arquivo de vídeo']
            row['button_measures'] = [{'text': text, 'bounds': [20, 50, 300, 40],
                'parent': [10, 40, 700, 60], 'requested_size': [300, 40], 'text_width': 250}
                for text in labels]
            for group in ('scenes', 'visual', 'audio', 'export'):
                row['views'].append({'group': group, 'window': [0, 0, width, 768], 'viewport': [0, 0, width, 600],
                    'target': [20, 50, 700, 300], 'cancel': [900, 630, 100, 30], 'progress': [20, 630, 800, 20],
                    'status': [0, 610, width, 150], 'visible': True,
                    'controls': [[20, 50, 700, 300]],
                    'capture': {'method': 'Win32.GDI.BitBlt', 'width': width, 'height': 768,
                                'path': 'fixture-artificial.png', 'sha256': 'a'*64}})
            rows.append(row)
    return {'schema_version': 1, 'cases': rows}


class NativeInterfaceGateTests(unittest.TestCase):
    def test_gate_recusa_medidas_faltantes_e_controles_cortados(self):
        from native_ui_checks import validate_interface_report
        valid = interface_fixture()
        validate_interface_report(valid)
        for key in valid['cases'][0]:
            invalid = copy.deepcopy(valid); del invalid['cases'][0][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_interface_report(invalid)
        for key, value in [('new_manual_seconds', 15), ('legacy_seconds', 60), ('music_manual_seconds', 15),
                           ('scrollable', False), ('pending_preserved', False), ('time_columns_aligned', False),
                           ('tk_scaling', 1), ('format_values', ['MP4']), ('background', '#ffffff')]:
            invalid = copy.deepcopy(valid); invalid['cases'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_interface_report(invalid)
        for key, value in [('target', [0, 590, 900, 80]), ('cancel', [0, 790, 100, 30]),
                           ('capture', {'method': 'mock'})]:
            invalid = copy.deepcopy(valid); invalid['cases'][0]['views'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_interface_report(invalid)

    def test_smoke_windows_exige_gate_interface_antes_de_aprovar(self):
        import smoke_windows
        from test_smoke_windows import NativeSmokeContractTests
        valid = NativeSmokeContractTests().valid_result(); valid['platform'] = 'win32'
        valid.pop('interface', None)
        with self.assertRaisesRegex(ValueError, 'interface'):
            smoke_windows.validate_result(valid)
        valid['interface'] = interface_fixture()
        smoke_windows.validate_result(valid)
