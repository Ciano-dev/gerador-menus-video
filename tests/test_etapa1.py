"""Aceite funcional da etapa 1; adaptadores Tk são testes sem janela real."""
import copy
import inspect
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gui_autoria as autoria
import gui_cores as cores
import gui_servicos as services
import menu_anisio as motor
import menu_gui as gui


class Etapa1Tests(unittest.TestCase):
    def test_01_paleta_ffmpeg_convertida_sem_trocar_azul_ou_bege(self):
        palette = {'fundo': '0x101f33', 'texto': '0xf3ead6', 'destaque': '0xc7a362'}
        self.assertEqual(cores.safe_color(palette['texto'], 'texto'), '#f3ead6')
        self.assertEqual(cores.safe_color('0x234567'), '#234567')
        model = cores.draw_model('Título', palette, [], 384, 216)
        self.assertEqual(model['palette'], autoria.Project().colors)
        self.assertIn('safe_color(self.project.colors', inspect.getsource(gui.App._build))

    def test_02_fluxo_tem_exportacao_unica_e_abertura_externa(self):
        source = inspect.getsource(gui.App._build)
        self.assertNotIn('self.make_preview', source)
        self.assertIn('self.open_last', source)
        self.assertIn('Abrir último vídeo', source)
        self.assertIn('Exportar vídeo (1080p)…', source)
        self.assertNotIn('640 × 360', source)

    def project(self):
        project = autoria.Project(duration=.4)
        project.add_video(autoria.MediaInfo(Path('/local/video.mp4'), 1))
        project.edit_scene(0, 'Cena', 0, .2)
        return project

    def test_03_formato_estrito_roundtrip_e_padrao_legado(self):
        project = self.project()
        self.assertEqual(getattr(project, 'export_format', None), 'MP4')
        project.export_format = 'AVI'
        data = services.project_data(project, Path('/local'))
        self.assertEqual(data['formato'], 'AVI')
        self.assertEqual(motor.animated_settings(data, Path('/local/menu.json'), Path('/local/out'))['formato'], 'AVI')
        del data['formato']
        self.assertEqual(motor.animated_settings(data, Path('/local/menu.json'), Path('/local/out'))['formato'], 'MP4')
        for value in ('avi', 'MOV', '', None, 1, [], {}):
            with self.subTest(value=value):
                project.export_format = value
                with self.assertRaises(motor.ErroProjeto):
                    project.validate()
                data['formato'] = value
                with self.assertRaises(motor.ErroProjeto):
                    motor.animated_settings(data, Path('/local/menu.json'), Path('/local/out'))
        project.export_format = 'AVI'
        with tempfile.TemporaryDirectory() as tmp:
            project.scenes[0].source = Path(tmp) / 'video.mp4'
            project.scenes[0].source.touch()
            saved = services.save_project(project, Path(tmp) / 'menu.json')
            with patch.object(services, 'inspect_media', return_value=autoria.MediaInfo(project.scenes[0].source, 1)):
                self.assertEqual(services.open_project(saved), project)

    def test_04_formato_readonly_dialogo_extensao_e_nomes_exclusivos(self):
        from test_gui_janela import WindowLogicTests, Var
        harness = WindowLogicTests(); harness.setUp()
        app = harness.app
        app.format_var = Var('AVI')
        self.assertEqual(getattr(app.capture(validate=True), 'export_format', None), 'AVI')
        source = inspect.getsource(gui.App._build)
        self.assertIn('ttk.Combobox', source)
        self.assertIn("values=('MP4', 'AVI')", source)
        self.assertIn("state='readonly'", source)
        with patch.object(gui.filedialog, 'asksaveasfilename', return_value='/local/menu.avi') as dialog:
            app.make_export()
        self.assertEqual(dialog.call_args.kwargs['defaultextension'], '.avi')
        self.assertEqual(dialog.call_args.kwargs['filetypes'], [('Vídeo AVI', '*.avi')])
        with patch.object(gui.filedialog, 'asksaveasfilename', return_value='/local/menu.mp4'):
            with self.assertRaises(motor.ErroProjeto):
                app.make_export()
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / 'MENU.AVI'; first.write_bytes(b'original')
            self.assertEqual(services.available_export_path(Path(tmp) / 'menu.avi').name, 'menu (1).avi')
            self.assertEqual(first.read_bytes(), b'original')

    def test_05_avi_reel_mpeg4_mp3_decode_extensao_e_sources_intactes(self):
        with tempfile.TemporaryDirectory(prefix='etape1-avi-') as tmp:
            base = Path(tmp); source = base / 'source.mp4'; music = base / 'music.wav'
            ffmpeg = motor.resolve_tool('ffmpeg')
            motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
                'testsrc2=s=64x64:r=30:d=0.4', '-c:v', 'libx264', str(source)])
            motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
                'sine=duration=0.4:sample_rate=48000', str(music)])
            project = self.project(); project.export_format = 'AVI'
            project.scenes[0].source = source
            project.music = services.inspect_media(music, audio=True)
            before = {p: motor.sha256(p) for p in (source, music)}
            result = services.render_project(project, None, base / 'out.avi', profile='prova')
            media = motor.probe_media(motor.resolve_tool('ffprobe'), result)
            self.assertEqual(media['format']['format_name'], 'avi')
            self.assertEqual([s['codec_name'] for s in media['streams']], ['mpeg4', 'mp3'])
            self.assertEqual(media['streams'][1]['channels'], 2)
            motor.check_decode(ffmpeg, result, duration=.4)
            self.assertEqual({p: motor.sha256(p) for p in before}, before)
            with self.assertRaises(motor.ErroProjeto):
                services.render_project(project, None, base / 'wrong.mp4', profile='prova')
            self.assertFalse((base / 'wrong.mp4').exists())

    def test_06_pasta_saida_valida_argv_windows_e_erro_amigavel(self):
        opener = getattr(services, 'open_output_folder', None)
        self.assertTrue(callable(opener), 'Falta abrir a pasta da saída real')
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'menu & teste.avi'; output.write_bytes(b'validado')
            with patch.object(services.sys, 'platform', 'win32'), \
                    patch.object(services.os, 'startfile', create=True) as start, \
                    patch.object(services.subprocess, 'run') as run:
                opener(output)
            start.assert_called_once_with(str(output.parent.resolve()))
            run.assert_not_called()
            with patch.object(services.sys, 'platform', 'linux'), \
                    patch.object(services.shutil, 'which', return_value='/usr/bin/xdg-open'), \
                    patch.object(services.subprocess, 'run', side_effect=OSError('fixture')):
                with self.assertRaisesRegex(motor.ErroProjeto, 'Não foi possível abrir a pasta'):
                    opener(output)
            output.unlink()
            with self.assertRaisesRegex(motor.ErroProjeto, 'disponível|Exporte'):
                opener(output)
            with self.assertRaises(motor.ErroProjeto):
                opener(None)
        self.assertIn('Abrir pasta do vídeo', inspect.getsource(gui.App._build))
        self.assertIn('open_output_folder', inspect.getsource(gui.App.open_folder))

    def test_07_alinhamento_validacao_save_open_e_legado(self):
        project = self.project()
        self.assertEqual(getattr(project, 'title_alignment', None), 'esquerda')
        project.title_alignment = 'centralizado'
        data = services.project_data(project, Path('/local'))
        self.assertEqual(data['alinhamento_titulo'], 'centralizado')
        self.assertEqual(motor.animated_settings(data, Path('/local/p'), Path('/local/out'))['alinhamento_titulo'], 'centralizado')
        del data['alinhamento_titulo']
        self.assertEqual(motor.animated_settings(data, Path('/local/p'), Path('/local/out'))['alinhamento_titulo'], 'esquerda')
        for value in ('direita', 'centro', '', None, 1, [], {}):
            project.title_alignment = value
            with self.subTest(value=value), self.assertRaises(motor.ErroProjeto):
                project.validate()
            data['alinhamento_titulo'] = value
            with self.subTest(value=value), self.assertRaises(motor.ErroProjeto):
                motor.animated_settings(data, Path('/local/p'), Path('/local/out'))
        project.title_alignment = 'centralizado'
        with tempfile.TemporaryDirectory() as tmp:
            project.scenes[0].source = Path(tmp) / 'video.mp4'; project.scenes[0].source.touch()
            saved = services.save_project(project, Path(tmp) / 'menu.json')
            with patch.object(services, 'inspect_media', return_value=autoria.MediaInfo(project.scenes[0].source, 1)):
                self.assertEqual(services.open_project(saved), project)

    def test_08_alinhamento_da_ui_a_miniatura_e_frame_real(self):
        from test_gui_janela import WindowLogicTests, Var
        harness = WindowLogicTests(); harness.setUp(); app = harness.app
        app.alignment_var = Var('centralizado')
        self.assertEqual(app.capture(validate=True).title_alignment, 'centralizado')
        model = cores.draw_model('MMMM', autoria.Project().colors, [], 640, 360,
                                 title_alignment='centralizado')
        title = next(i for i in model['items'] if i['tag'] == 'title')
        self.assertEqual(title['alignment'], 'centralizado')
        with tempfile.TemporaryDirectory(prefix='etapa1-align-') as tmp:
            base = Path(tmp); source = base / 'source.mp4'
            ffmpeg = motor.resolve_tool('ffmpeg')
            motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
                'testsrc2=s=64x64:r=30:d=0.4', '-c:v', 'libx264', str(source)])
            project = self.project(); project.title = 'MMMM'; project.scenes[0].source = source
            boxes = []
            for alignment in ('esquerda', 'centralizado'):
                project.title_alignment = alignment
                result = services.render_project(project, None, base / (alignment+'.mp4'), profile='prova')
                frame = motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-i', str(result),
                    '-frames:v', '1', '-pix_fmt', 'rgb24', '-threads', '1', '-f', 'rawvideo', '-']).stdout
                xs = [x for y in range(10, 63) for x in range(640)
                      if frame[(y*640+x)*3] > 160 and frame[(y*640+x)*3+1] > 150
                      and frame[(y*640+x)*3+2] > 100]
                self.assertTrue(xs)
                boxes.append((min(xs), max(xs)))
            self.assertLess(boxes[0][0], 40)
            self.assertAlmostEqual(sum(boxes[1])/2, 320, delta=3)
            self.assertGreater(boxes[1][0], boxes[0][1])

    def test_09_progresso_real_ffmpeg_pela_fila_fora_da_thread_tk(self):
        import threading
        import time
        import media_runtime as runtime
        from gui_trabalho import Worker
        self.assertTrue(hasattr(runtime, 'MediaProgress'), 'Falta progresso mensurado por etapa')
        worker = Worker(); caller = threading.get_ident(); threads = []
        with tempfile.TemporaryDirectory(prefix='etapa1-progress-') as tmp:
            base = Path(tmp); source = base / 'source.mp4'
            ffmpeg = motor.resolve_tool('ffmpeg')
            motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
                'testsrc2=s=64x64:r=30:d=0.4', '-c:v', 'libx264', str(source)])
            project = self.project(); project.scenes[0].source = source
            def operation(progress):
                def publish(event):
                    threads.append(threading.get_ident()); progress(event)
                return services.render_project(project, None, base / 'out.mp4', profile='prova', progress=publish)
            worker.submit(operation)
            events = []; deadline = time.monotonic() + 10
            while worker.busy and time.monotonic() < deadline:
                events.extend(worker.poll()); time.sleep(.005)
            self.assertFalse(worker.busy)
            self.assertEqual(events[-1].kind, 'done', str(events[-1].value))
            measured = [e.value for e in events if isinstance(e.value, runtime.MediaProgress)
                        and e.value.fraction is not None]
            self.assertTrue(measured, 'Nenhum valor FFmpeg mensurado')
            self.assertTrue(any(e.stage == 'Compondo vídeo' and e.seconds > 0 for e in measured))
            self.assertTrue(all(0 <= e.fraction <= 1 for e in measured))
            self.assertTrue(any(isinstance(e.value, runtime.MediaProgress) and e.value.fraction is None for e in events))
            self.assertTrue(threads and all(t != caller for t in threads))

    def test_10_ui_exibe_percentual_da_etapa_e_indeterminado_sem_medida(self):
        import media_runtime as runtime
        from gui_trabalho import Event
        from test_cancelamento_ui import UICancelTests
        harness = UICancelTests(); harness.setUp(); app = harness.app
        app.worker._events.put(Event('progress', runtime.MediaProgress('Compondo vídeo', 2, 8)))
        app.poll()
        app.progress.configure.assert_called_with(mode='determinate', maximum=100, value=25)
        self.assertIn('25% da etapa', app.status_var.get())
        app.worker._events.put(Event('progress', runtime.MediaProgress('Ajustando textos')))
        app.poll()
        app.progress.configure.assert_called_with(mode='indeterminate', value=0)
        app.progress.start.assert_called_with(12)
        app.root.after.assert_called_with(100, app.poll)
        app._cancel_requested = True
        app.status_var.set('Cancelando…')
        app.worker._events.put(Event('progress', runtime.MediaProgress('Atrasado', 8, 8)))
        app.poll()
        self.assertEqual(app.status_var.get(), 'Cancelando…')

    def test_11_poll_limita_lote_sem_perder_terminal_ou_ordenacao(self):
        from gui_trabalho import Worker, Event
        worker = Worker(); worker.busy = True
        for i in range(250):
            worker._events.put(Event('progress', i))
        worker._events.put(Event('done', 'final'))
        first = worker.poll()
        self.assertLessEqual(len(first), 100, 'Um poll não deve drenar fila sem limite')
        self.assertTrue(worker.busy)
        events = first + worker.poll() + worker.poll()
        self.assertEqual([e.value for e in events[:-1]], list(range(250)))
        self.assertEqual(events[-1], Event('done', 'final'))
        self.assertFalse(worker.busy)

    def test_13_progresso_antes_do_exit_com_pipes_bufferizados_como_windows(self):
        import subprocess
        import time
        import media_runtime as runtime
        token = runtime.CancellationToken(); measured = []
        communicate = subprocess.Popen.communicate
        def buffered(child, *args, **kwargs):
            try:
                return communicate(child, *args, **kwargs)
            except subprocess.TimeoutExpired as exc:
                # O communicate Windows não fornece stdout parcial durante timeout.
                exc.output = None
                raise
        def progress(event):
            if event.fraction is not None and event.seconds > 0:
                measured.append(event.seconds); token.cancel()
        started = time.monotonic()
        with runtime.progress_scope(progress), patch.object(subprocess.Popen, 'communicate', buffered):
            with self.assertRaises(runtime.Cancelled):
                motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg')) + [
                    '-re', '-f', 'lavfi', '-i', 'testsrc2=s=64x64:r=30:d=4',
                    '-f', 'null', '-'], cancel=token, stage='Streaming', progress_duration=4)
        self.assertTrue(measured)
        self.assertLess(time.monotonic()-started, 2.5, 'Progresso chegou apenas depois do exit')
        self.assertLess(measured[0], 4)

    def test_14_smoke_preserva_mp4_e_exige_avi_real_sem_afrouxar_gates(self):
        import smoke_windows
        from test_smoke_windows import NativeSmokeContractTests
        with tempfile.TemporaryDirectory(prefix='etapa1-smoke-') as tmp:
            result = smoke_windows.synthetic_services(Path(tmp), lambda message: None)
            pairs = {(row['profile'], row.get('format', 'MP4')) for row in result['outputs']}
            self.assertEqual(pairs, {('prova','MP4'), ('1080p','MP4'), ('1080p','AVI')})
            report = NativeSmokeContractTests().valid_result(); report['outputs'] = result['outputs']
            smoke_windows.validate_result(report)
            for field, value in [('format', 'MP4'), ('container', 'matroska')]:
                bad = copy.deepcopy(report); bad['outputs'][2][field] = value
                with self.subTest(field=field), self.assertRaises(ValueError):
                    smoke_windows.validate_result(bad)
            bad = copy.deepcopy(report); bad['outputs'][2]['streams'][0]['codec_name'] = 'h264'
            with self.assertRaises(ValueError):
                smoke_windows.validate_result(bad)
            bad = copy.deepcopy(report); bad['outputs'].pop()
            with self.assertRaises(ValueError):
                smoke_windows.validate_result(bad)
        ps = (Path(__file__).resolve().parents[1] / 'windows_build/smoke_windows.ps1').read_text()
        self.assertIn("'AVI'", ps)
        self.assertIn("'mpeg4'", ps)
        self.assertIn('outputs.Count -ne 3', ps)

    def test_15_motor_recusa_alinhamento_arbitrario_antes_de_midia(self):
        settings = {'formato':'MP4', 'alinhamento_titulo':'direita'}
        with tempfile.TemporaryDirectory() as tmp, patch.object(motor, 'resolve_tool',
                side_effect=AssertionError('Não deve sondar opção inválida')):
            with self.assertRaisesRegex(motor.ErroProjeto, 'alinhamento_titulo'):
                motor.export_media('Título', [], Path(tmp)/'out.mp4', settings)

    def test_16_motor_confere_conteiner_antes_da_publicacao(self):
        with tempfile.TemporaryDirectory(prefix='etapa1-container-') as tmp:
            base = Path(tmp); source = base/'source.mp4'
            motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg')) + ['-f','lavfi','-i',
                'testsrc2=s=64x64:r=30:d=0.4','-c:v','libx264',str(source)])
            project = self.project(); project.scenes[0].source=source; project.export_format='AVI'
            probe = motor.probe_media
            def mismatched(ffprobe, path, **kwargs):
                info = probe(ffprobe,path,**kwargs)
                if Path(path).name == '00-APRESENTACAO.avi':
                    self.assertEqual(info['format']['format_name'], 'avi')
                    info['format']['format_name']='matroska'
                return info
            with patch.object(motor,'probe_media',side_effect=mismatched):
                with self.assertRaisesRegex(motor.ErroProjeto,'contêiner'):
                    services.render_project(project,None,base/'out.avi',profile='prova')
            self.assertFalse((base/'out.avi').exists())
            self.assertFalse(list(base.glob('.out.avi*')))

    def test_17_seletor_usa_mesma_cor_tk_do_swatch_ffmpeg(self):
        from test_gui_janela import WindowLogicTests
        case = WindowLogicTests(); case.setUp(); app=case.app
        app.color_vars['fundo'].set('0x234567')
        app.color_swatches={'fundo':Mock()}
        with patch.object(gui.colorchooser,'askcolor',return_value=((35,69,103),'#234567')) as picker:
            app.choose_color('fundo')
        self.assertEqual(picker.call_args.kwargs['color'],'#234567')
        self.assertEqual(app.color_vars['fundo'].get(),'#234567')
        app.color_swatches['fundo'].configure.assert_called_with(background='#234567')

    def test_18_fases_sem_medida_identificam_ajuste_de_texto(self):
        import media_runtime as runtime
        with tempfile.TemporaryDirectory(prefix='etapa1-stage-') as tmp:
            base=Path(tmp); source=base/'source.mp4'; music=base/'music.wav'
            ffmpeg=motor.resolve_tool('ffmpeg')
            motor.run_media(motor.ffmpeg_base(ffmpeg)+['-f','lavfi','-i',
                'testsrc2=s=64x64:r=30:d=0.4','-c:v','libx264',str(source)])
            motor.run_media(motor.ffmpeg_base(ffmpeg)+['-f','lavfi','-i',
                'sine=duration=0.4:sample_rate=48000',str(music)])
            project=self.project(); project.scenes[0].source=source
            project.music=services.inspect_media(music,audio=True); events=[]
            services.render_project(project,None,base/'out.mp4',profile='prova',progress=events.append)
            measured=[e for e in events if isinstance(e,runtime.MediaProgress)]
            self.assertTrue(any(e.stage=='Ajustando textos' and e.fraction is None for e in measured))
            self.assertTrue(any(e.stage=='Conferindo áudio: music.wav' and e.seconds is not None for e in measured))
