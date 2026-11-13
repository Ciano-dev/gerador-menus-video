import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import menu_video as motor
import gui_servicos as services
from gui_autoria import Project, MediaInfo


class ExportacaoObjetivaTests(unittest.TestCase):
    def test_exporta_apenas_mp4_lendo_fontes_sem_copia_ou_hash_integral(self):
        from gerar_fixtures import generate
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            sources = generate(base / 'fontes')
            project = Project(title='João [100%];', duration=.4)
            project.add_video(services.inspect_media(sources / 'cores-com-audio.mp4'))
            output = base / 'Menu único.mp4'
            before = motor.sha256(project.scenes[0].source)
            with patch.object(motor, 'sha256', side_effect=AssertionError('hash integral proibido')):
                result = services.render_project(project, base, output, profile='prova')
            self.assertEqual(result, output)
            self.assertTrue(output.is_file())
            self.assertEqual(set(p.name for p in base.iterdir()), {'fontes', output.name})
            self.assertEqual(motor.sha256(project.scenes[0].source), before)
            with self.assertRaisesRegex(motor.ErroProjeto, 'existe'):
                motor.export_media(project.title, [], output, None, 'prova')

class DuracaoLivreTests(unittest.TestCase):
    def test_musica_e_menu_acima_de_180s_e_menu_maior_que_faixa(self):
        project = Project(duration=601)
        project.add_video(MediaInfo(Path('cena.mp4'), 1000))
        project.music = MediaInfo(Path('trilha.wav'), 181)
        project.validate()
        self.assertEqual(project.effective_duration(), 601)
        project.duration_mode = 'musica'
        project.validate()
        self.assertEqual(project.effective_duration(), 181)

class AudioRealTests(unittest.TestCase):
    def test_audio_longo_seguido_silencio_e_corte_reais(self):
        import array
        import math
        with tempfile.TemporaryDirectory(prefix='menu-unicode-') as temporary:
            base = Path(temporary)
            source = base / "vídeo [a]; ' %.mp4"
            track = base / 'música longa.wav'
            ffmpeg, ffprobe = motor.resolve_tool('ffmpeg'), motor.resolve_tool('ffprobe')
            motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
                'color=c=blue:s=32x32:r=30:d=0.2', '-c:v', 'libx264', str(source)])
            motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
                'sine=frequency=440:sample_rate=8000:duration=181', '-c:a', 'pcm_s16le', str(track)])
            project = Project(title='João 100% %{eif:1:d} [a];', duration=182)
            project.add_video(services.inspect_media(source))
            project.music = services.inspect_media(track, audio=True)
            before = [motor.sha256(p) for p in (source, track)]
            for mode, duration in [('musica', 181), ('manual', 182), ('manual', .8)]:
                project.duration_mode, project.duration = mode, duration
                target = base / f'menu-{duration}.mp4'
                services.render_project(project, None, target, profile='prova')
                streams = motor.probe_media(ffprobe, target)['streams']
                self.assertAlmostEqual(motor.media_duration(streams[0], 'menu'), duration, delta=1/30+.001)
                self.assertAlmostEqual(motor.media_duration(streams[1], 'áudio'), duration, delta=.001)
                raw = motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-i', str(target), '-map', '0:a:0',
                    '-ac', '1', '-ar', '8000', '-f', 's16le', '-']).stdout
                samples = array.array('h', raw)
                def rms(start, end):
                    part = samples[round(start*8000):round(end*8000)]
                    return math.sqrt(sum(x*x for x in part)/len(part))
                print('AUDIO_REAL', {'modo': mode, 'menu_s': duration,
                      'video_s': motor.media_duration(streams[0], 'menu'),
                      'audio_s': motor.media_duration(streams[1], 'áudio'),
                      'rms_inicio': rms(.05, .1),
                      'rms_final': rms(181.2, 181.8) if duration == 182 else rms(duration-.04, duration-.01)}, flush=True)
                self.assertGreater(rms(.05, .1), 500)
                if duration == 182:
                    self.assertLess(rms(181.2, 181.8), 2)
                elif duration == .8:
                    self.assertLess(rms(.76, .79), rms(.05, .1)*.3)
                digest = motor.sha256(target)
                with self.assertRaisesRegex(motor.ErroProjeto, 'existe'):
                    motor.export_media(project.title, [], target, None, 'prova')
                self.assertEqual(motor.sha256(target), digest)
            self.assertEqual([motor.sha256(p) for p in (source, track)], before)
            self.assertEqual(len(list(base.iterdir())), 5)
            print('FONTES_INTACTAS_MP4_UNICO_OVERWRITE_RECUSADO', True, flush=True)

class TimeoutTests(unittest.TestCase):
    def test_render_longo_recebe_timeout_proporcional(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            scene = {'source': work / 'fonte.mp4', 'nome': 'Cena',
                     'previa': {'inicio': 0, 'duracao_efetiva': .2}}
            settings = {'segundos': 601, 'visual': Project().colors,
                        'musica': None, 'exibir_rodape': False}
            with patch.object(motor, 'measure_text_pixels', return_value=(24, 8)), \
                    patch.object(motor, 'run_media') as run, \
                    patch.object(motor, 'probe_media', return_value={'streams': [
                        {'nb_read_frames': '6', 'duration': '.2'}]}):
                motor.render_animated('ffmpeg', work, work, 'Menu', [scene], 'prova', False, settings)
            self.assertGreaterEqual(run.call_args.kwargs['timeout'], 601 * 4)

class CamposTempoTests(unittest.TestCase):
    def test_inicio_fim_hora_min_seg_e_menu_inteiro_preservam_precisao_existente(self):
        from tests.test_gui_janela import WindowLogicTests, Var
        case = WindowLogicTests()
        case.setUp()
        app = case.app
        app.project.scenes[0].source_duration = 5000
        app.duration_minutes_var, app.duration_seconds_var = Var('61'), Var('2')
        app.finish_duration_edit()
        app.scene_start_minutes_var, app.scene_start_seconds_var = Var('3'), Var('0')
        app.scene_end_minutes_var, app.scene_end_seconds_var = Var('3'), Var('10')
        snapshot = app.capture(validate=True)
        self.assertEqual(snapshot.duration, 3662)
        self.assertEqual((snapshot.scenes[0].start, snapshot.scenes[0].duration), (180, 10))
        app.scene_start_minutes_var.set('0')
        app.project.scenes[0].start, app.project.scenes[0].duration = .9, .1
        app.scene_start_seconds_var.set('1')
        app.scene_end_minutes_var.set('0')
        app.scene_end_seconds_var.set('1')
        snapshot = app.capture(validate=True)
        self.assertAlmostEqual(snapshot.scenes[0].duration, .1, places=15)
        app.duration_seconds_var.set('60')
        with self.assertRaises(motor.ErroProjeto):
            app.capture()

class AutoriaSemSalvarTests(unittest.TestCase):
    def test_previa_e_exportacao_sem_salvar_projeto_usam_mp4(self):
        from tests.test_gui_janela import WindowLogicTests
        from unittest.mock import Mock
        case = WindowLogicTests()
        case.setUp()
        app, module = case.app, case.module
        app.project_path = None
        app.save_as = Mock()
        with patch.object(module.services, 'preview_project') as preview:
            app.make_preview()
            app.save_as.assert_not_called()
            self.assertEqual(len(case.jobs), 1)
            case.jobs.pop()[0](lambda msg: None)
            self.assertIsNone(preview.call_args.args[1])
        with patch.object(module.filedialog, 'asksaveasfilename', return_value='/local/menu.mp4') as dialog, \
                patch.object(module.services, 'render_project') as render:
            app.make_export()
            self.assertEqual(len(case.jobs), 1)
            case.jobs.pop()[0](lambda msg: None)
            self.assertEqual(render.call_args.args[2], Path('/local/menu.mp4'))
            self.assertIs(dialog.call_args.kwargs['confirmoverwrite'], False)
        app.save_as.assert_not_called()

class EscolhaMusicaTests(unittest.TestCase):
    def test_escolher_musica_preserva_manual_seguir_explicito_e_retirar_preserva_duracao(self):
        from tests.test_gui_janela import WindowLogicTests
        from unittest.mock import Mock
        case = WindowLogicTests()
        case.setUp()
        app, module = case.app, case.module
        track = MediaInfo(Path('/local/faixa.wav'), 402.25)
        with patch.object(module.filedialog, 'askopenfilename', return_value=str(track.source)), \
                patch.object(module.services, 'inspect_media', return_value=track):
            app.choose_music()
            result = case.jobs.pop()[0](lambda msg: None)
        self.assertEqual(result.duration_mode, 'manual')
        self.assertEqual(result.effective_duration(), 2)
        app.project = result
        app.duration_entries = []
        app.duration_mode_var.set('musica')
        app.update_duration_mode()
        self.assertEqual(app.project.effective_duration(), 402.25)
        app.adopt = Mock()
        app.clear_music()
        result = app.adopt.call_args.args[0]
        self.assertEqual(result.duration_mode, 'manual')
        self.assertEqual(result.duration, 402.25)
        self.assertIsNone(result.music)

class AvisosDuracaoTests(unittest.TestCase):
    def app(self):
        from tests.test_gui_janela import WindowLogicTests
        case = WindowLogicTests()
        case.setUp()
        case.app._refreshing = False
        case.app.duration_entries = []
        case.app.project.music = MediaInfo(Path('/local/trilha.wav'), 120)
        case.app.test_jobs = case.jobs
        return case.app, case.module

    def test_concluir_manual_avisa_sem_duplicar_e_igual_nao_avisa(self):
        app, module = self.app()
        with patch.object(module.messagebox, 'showinfo') as info:
            app.duration_minutes_var.set('3')
            app.duration_seconds_var.set('0')
            app.finish_duration_edit()
            info.assert_called_once()
            self.assertEqual(info.call_args.args[1], 'O menu continuará sem som após o fim da música.')
            app.finish_duration_edit()
            info.assert_called_once()
            app.duration_minutes_var.set('1')
            app.finish_duration_edit()
            self.assertEqual(info.call_args.args[1], 'A música será cortada no final do menu.')
            self.assertEqual(info.call_count, 2)
            app.duration_minutes_var.set('2')
            app.finish_duration_edit()
            self.assertEqual(info.call_count, 2)

    def test_acima_de_seis_minutos_confirma_e_voltar_nao_agenda(self):
        app, module = self.app()
        app.duration_minutes_var.set('6')
        app.duration_seconds_var.set('1')
        with patch.object(module.messagebox, 'showinfo'):
            app.finish_duration_edit()
        with patch.object(module.messagebox, 'askyesno', return_value=False) as confirm:
            app.make_preview()
            confirm.assert_called_once()
            self.assertEqual(app.test_jobs, [])
            self.assertIn('poderá levar mais tempo para gerar. Deseja continuar?', confirm.call_args.args[1])
        with patch.object(module.messagebox, 'askyesno', return_value=True) as confirm:
            app.make_preview()
            app.make_preview()
            confirm.assert_called_once()
        app.duration_seconds_var.set('0')
        with patch.object(module.messagebox, 'showinfo'):
            app.finish_duration_edit()
        with patch.object(module.messagebox, 'askyesno') as confirm:
            app.make_preview()
            confirm.assert_not_called()

class InterfaceConstruidaTests(unittest.TestCase):
    def test_cores_tempos_compactos_e_aplicar_por_botao_e_enter(self):
        import menu_gui as module
        from contextlib import ExitStack
        from unittest.mock import MagicMock
        from tests.test_gui_janela import Var
        app = module.App.__new__(module.App)
        app.root, app.controls, app.project = MagicMock(), [], Project()
        entries, labels, buttons, entry_widgets, masters = [], [], [], {}, []
        def variable(value='', **kwargs):
            masters.append(kwargs.get('master'))
            return Var(value)
        def widget(name):
            def create(*args, **kwargs):
                created = MagicMock()
                if name == 'Entry':
                    entries.append(kwargs)
                    if 'textvariable' in kwargs:
                        entry_widgets[kwargs['textvariable']] = created
                if name == 'Label':
                    labels.append(kwargs)
                if name == 'Button':
                    buttons.append(kwargs)
                return created
            return create
        with ExitStack() as stack:
            native_font = MagicMock()
            native_font.actual.return_value = 'Sans'
            native_font.metrics.return_value = 16
            stack.enter_context(patch.object(module.tkfont, 'nametofont', return_value=native_font))
            for name in ('Frame', 'LabelFrame', 'Label', 'Button', 'Entry', 'Treeview',
                         'Scrollbar', 'Checkbutton', 'Radiobutton', 'Progressbar', 'Style', 'Combobox', 'Spinbox'):
                stack.enter_context(patch.object(module.ttk, name, side_effect=widget(name)))
            stack.enter_context(patch.object(module.tk, 'Canvas', side_effect=widget('Canvas')))
            stack.enter_context(patch.object(module.tk, 'Label', side_effect=widget('Label')))
            for name in ('StringVar', 'BooleanVar'):
                stack.enter_context(patch.object(module.tk, name, side_effect=variable))
            app._build()
        self.assertTrue(masters and all(master is app.root for master in masters))
        color_variables = list(app.color_vars.values())
        self.assertFalse(any(e.get('textvariable') in color_variables for e in entries))
        self.assertEqual(set(app.color_swatches), {'fundo', 'texto', 'destaque'})
        self.assertTrue(any(l.get('text') == 'Gerador de menus em vídeo' for l in labels))
        for caption in ('Hora', 'Min', 'Seg'):
            self.assertEqual(sum(label.get('text') == caption for label in labels), 2)
        scene_variables = [getattr(app, f'scene_{key}_{unit}_var')
                           for key in ('start', 'end') for unit in ('hours', 'minutes', 'seconds')]
        self.assertEqual([entry['width'] for entry in entries if entry.get('textvariable') in scene_variables], [4]*6)
        for entry in app.duration_entries:
            events = [call.args[0] for call in entry.bind.call_args_list]
            self.assertIn('<Return>', events)
            self.assertNotIn('<FocusOut>', events)
        app.worker = MagicMock()
        app.worker.busy = False
        app.duration_minutes_var.set('0')
        app.duration_seconds_var.set('3')
        self.assertTrue(app.duration_pending())
        enter = app.duration_entries[1].bind.call_args.args[1]
        enter(None)
        self.assertEqual(app.project.duration, 3)
        self.assertFalse(app.duration_pending())
        app.duration_seconds_var.set('4')
        next(button for button in buttons if button['text'] == 'Aplicar duração')['command']()
        self.assertEqual(app.project.duration, 4)
        self.assertFalse(app.duration_pending())

class CaminhosAutoriaTests(unittest.TestCase):
    def test_projeto_em_unidade_distinta_preserva_caminho_local_absoluto(self):
        try:
            result = services.relative_media_path(r'D:\Vídeos com acento\cena [x];.mp4',
                                                   r'C:\Projetos', windows=True)
        except motor.ErroProjeto as exc:
            self.fail(f'Autoria não pode depender do volume do projeto: {exc}')
        self.assertEqual(result, 'D:/Vídeos com acento/cena [x];.mp4')

class ValidacaoFontesTests(unittest.TestCase):
    def test_fonte_repetida_em_cenas_e_decodificada_uma_vez_na_validacao(self):
        import copy
        from gerar_fixtures import generate
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = generate(base / 'fontes') / 'cores-com-audio.mp4'
            project = Project(duration=.4)
            project.add_video(services.inspect_media(source))
            project.scenes.append(copy.deepcopy(project.scenes[0]))
            with patch.object(motor, 'check_decode', wraps=motor.check_decode) as decode:
                services.render_project(project, None, base / 'menu.mp4', profile='prova')
            self.assertEqual(sum(call.args[1].resolve() == source.resolve()
                                 for call in decode.call_args_list), 1)

class VerificadorAtualTests(unittest.TestCase):
    def verify(self, with_music=False):
        import subprocess
        import sys
        from gerar_fixtures import generate
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = generate(base / 'fontes') / 'cores-com-audio.mp4'
            project = Project(duration=.4)
            project.add_video(services.inspect_media(source))
            if with_music:
                project.music = services.inspect_media(source.with_name('som-sem-video.m4a'), audio=True)
            path = services.save_project(project, base / 'projeto.menu')
            evidence = base / 'evidencias'
            target = base / 'menu.mp4'
            command = [sys.executable, '-B', str(Path(motor.__file__).with_name('verificar_demo.py')),
                       str(path), str(target), '--evidencias', str(evidence)]
            before = subprocess.run(command + ['--registrar-antes'], capture_output=True, timeout=30)
            self.assertEqual(before.returncode, 0, before.stderr)
            services.render_project(project, None, target)
            result = subprocess.run(command, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))

    def test_verificador_aceita_mp4_unico_sem_manifesto(self):
        self.verify()

    def test_verificador_aceita_trilha_sem_video(self):
        self.verify(with_music=True)

class ContagemLongaTests(unittest.TestCase):
    def test_contagem_de_frames_nao_mantem_timeout_curto_oculto(self):
        import json
        from types import SimpleNamespace
        reply = SimpleNamespace(stdout=json.dumps({'streams': [
            {'codec_type': 'video', 'duration': '601'}]}).encode())
        with patch.object(motor, 'run_media', return_value=reply) as run:
            motor.probe_media('ffprobe', Path('video.mp4'), count_frames=True)
        self.assertGreaterEqual(run.call_args.kwargs['timeout'], 601*4)

class RegressaoNovosFluxosTests(unittest.TestCase):
    def test_edicao_pendente_so_avisa_ao_aplicar_e_incompleta_nao_e_ignorada(self):
        from tests.test_gui_janela import WindowLogicTests
        from unittest.mock import Mock
        case = WindowLogicTests()
        case.setUp()
        app, module = case.app, case.module
        app._refreshing = False
        app.project.music = MediaInfo(Path('/local/trilha.wav'), 1)
        app.duration_entries = [Mock(), Mock()]
        app.root = Mock()
        with patch.object(module.messagebox, 'showinfo') as info:
            app.duration_seconds_var.set('3')
            app.update_duration_mode()
            info.assert_not_called()
            app.root.after_idle.assert_not_called()
            with self.assertRaisesRegex(motor.ErroProjeto, 'Aplicar duração'):
                app.capture()
            app.finish_duration_edit()
            info.assert_called_once()
            app.finish_duration_edit()
            info.assert_called_once()
            app.duration_seconds_var.set('')
            app.update_duration_mode()
            with self.assertRaises(motor.ErroProjeto):
                app.finish_duration_edit()
            with self.assertRaisesRegex(motor.ErroProjeto, 'Aplicar duração'):
                app.capture()
            info.assert_called_once()
            app._refreshing = True
            app.finish_duration_edit()
            info.assert_called_once()

    def test_projeto_v2_reabre_referencias_absolutas_locais_recusa_protocolos(self):
        import json
        from gerar_fixtures import generate
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = generate(base / 'fontes') / 'cores-com-audio.mp4'
            project = Project(duration=.4)
            project.add_video(services.inspect_media(source))
            path = base / 'referencia.menu'
            with patch.object(services, 'relative_media_path', return_value=str(source)):
                services.save_project(project, path)
            self.assertEqual(services.open_project(path), project)
            data = json.loads(path.read_text(encoding='utf-8'))
            for uri in ('https://example.invalid/a.mp4', 'file:///etc/passwd', 'concat:a.mp4|b.mp4', 'pipe:0'):
                data['cenas'][0]['arquivo'] = uri
                path.write_text(json.dumps(data), encoding='utf-8')
                with self.subTest(uri=uri), self.assertRaisesRegex(motor.ErroProjeto, 'local|relativo'):
                    services.open_project(path)

    def test_arquivo_surgido_durante_render_e_preservado(self):
        from gerar_fixtures import generate
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            project = Project(duration=.4)
            project.add_video(services.inspect_media(generate(base / 'fontes') / 'cores-com-audio.mp4'))
            target = base / 'menu.mp4'
            render = motor.render_animated
            def race(*args):
                render(*args)
                target.write_bytes(b'arquivo de terceiro')
            with patch.object(motor, 'render_animated', side_effect=race):
                with self.assertRaisesRegex(motor.ErroProjeto, 'existe'):
                    services.render_project(project, None, target, profile='prova')
            self.assertEqual(target.read_bytes(), b'arquivo de terceiro')
            self.assertEqual(set(p.name for p in base.iterdir()), {'fontes', 'menu.mp4'})

class PrecisaoCamposTests(unittest.TestCase):
    def test_um_frame_em_inicio_nao_zero_sobrevive_reexibicao(self):
        from tests.test_gui_janela import WindowLogicTests, Var
        case = WindowLogicTests()
        case.setUp()
        app = case.app
        scene = app.project.scenes[0]
        scene.source_duration, scene.start, scene.duration = 1000, 180, 1/30
        app.scene_path_var = Var('')
        app.load_scene_fields()
        result = app.capture(validate=True)
        self.assertEqual(result.scenes[0].start, 180)
        self.assertEqual(result.scenes[0].duration, 1/30)
