"""Smoke integrado ao exe: Tk real, worker real e mídias exclusivamente sintéticas."""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid


def validate_result(data):
    from native_render_checks import validate_input_duration_case
    validate_input_duration_case(data.get('input_duration', {}), frozen=bool(data.get('frozen')))
    if data.get('frozen') or data.get('platform') == 'win32' or 'font_sizes' in data:
        from native_color_checks import validate_font_report
        validate_font_report(data.get('font_sizes', {}),
                             require_captures=bool(data.get('frozen') or data.get('platform') == 'win32'))
    if data.get('frozen') or data.get('platform') == 'win32' or 'font_families' in data:
        from native_color_checks import validate_family_report
        validate_family_report(data.get('font_families', {}),
                               require_captures=bool(data.get('frozen') or data.get('platform') == 'win32'))
    if data.get('frozen') or data.get('platform') == 'win32' or 'interface' in data:
        from native_ui_checks import validate_interface_report
        validate_interface_report(data.get('interface', {}),
                                  require_captures=bool(data.get('frozen') or data.get('platform') == 'win32'))
    if data.get('frozen') or data.get('platform') == 'win32' or 'color_preview' in data:
        from native_color_checks import validate_color_report
        validate_color_report(data.get('color_preview', {}),
                              require_captures=bool(data.get('frozen') or data.get('platform') == 'win32'))
    if data.get('frozen'):
        import client_download
        manifest = client_download.load_manifest()
        bootstrap = data.get('bootstrap', {})
        if not (bootstrap.get('cache_verified') is True
                and bootstrap.get('manifest_sha256') == client_download.fingerprint(manifest)
                and bootstrap.get('archive_sha256') == manifest['binary']['sha256']):
            raise ValueError('Bootstrap não comprovou cache íntegro do manifesto fixado.')
        if data.get('download_authorized'):
            if not (bootstrap.get('downloaded') is True and bootstrap.get('download_requests') == 1
                    and bootstrap.get('download_bytes') == manifest['binary']['size_bytes']
                    and bootstrap.get('window_viewable') and bootstrap.get('ui_heartbeats', 0) > 0
                    and data.get('network_events', 0) > 0):
                raise ValueError('Primeiro smoke precisa baixar realmente uma vez com UI responsiva.')
        elif not (bootstrap.get('downloaded') is False and bootstrap.get('download_requests') == 0
                  and bootstrap.get('download_bytes') == 0 and data.get('network_events') == 0):
            raise ValueError('Reabertura precisa reutilizar cache verificado sem rede.')
    if data.get('frozen') or data.get('smoke_scale_seconds') is not None:
        from native_render_checks import validate_case
        seconds = data.get('smoke_scale_seconds')
        if seconds not in (15, 30) or isinstance(seconds, bool):
            raise ValueError('Smoke frozen exige escala explícita 15/30s.')
        identity = data.get('scale_identity', {})
        if data.get('frozen'):
            if not (identity.get('frozen') is True and identity.get('platform') == 'win32'
                    and identity.get('portable_verified') is True
                    and identity.get('manifest_sha256') == data['bootstrap']['manifest_sha256']
                    and identity.get('archive_sha256') == data['bootstrap']['archive_sha256']
                    and re.fullmatch(r'[0-9a-f]{64}', identity.get('manifest_file_sha256', ''))):
                raise ValueError('Escala frozen exige ferramentas efetivas do pacote verificado.')
            for name in ('ffmpeg', 'ffprobe'):
                tool = identity.get('tools', {}).get(name, {})
                if not (tool.get('path') and tool.get('path') == data.get('tools', {}).get(name)
                        and re.fullmatch(r'[0-9a-f]{64}', tool.get('sha256', ''))
                        and tool.get('version', '').startswith(name + ' version ')):
                    raise ValueError('Escala frozen exige hash/versão de cada ferramenta efetiva.')
        scaled = data.get('scaled_render', {})
        validate_case(scaled, seconds=seconds, scenes=2, preview=3)
        if scaled['music_seconds'] != 6:
            raise ValueError('Escala frozen exige MP3 de 6s e silêncio após a faixa.')
    from native_render_checks import validate_family_render, validate_family_samples
    validate_family_samples(data.get('family_samples', {}))
    from native_render_checks import validate_font_render
    validate_font_render(data.get('current_font_render', {}), profile='prova', export_format='MP4')
    validate_family_render(data.get('current_font_render', {}), {
        'titre.txt': None, 'label-1.txt': None, 'label-2.txt': None,
        'number-1.txt': None, 'number-2.txt': None})
    window = data['window']
    if not (window['viewable'] and window['width'] >= 820 and window['height'] >= 600
            and window['map_events'] > 0 and window['destroy_event']):
        raise ValueError('Janela Tk real não foi exibida/medida/fechada pelo fluxo da aplicação.')
    if not data['worker_done'] or data['worker_heartbeats'] < 1:
        raise ValueError('Worker não concluiu ou a UI não respondeu durante o trabalho.')
    if data['errors'] or data['diagnostic_failures'] or not data['close_requested']:
        raise ValueError('Smoke detectou exceção, falha de diagnóstico ou fechamento incompleto.')
    if (len(data['outputs']) != 3 or {(row['profile'], row['format']) for row in data['outputs']} != {('prova', 'MP4'), ('1080p', 'MP4'), ('1080p', 'AVI')}
            or not all(row['verified'] for row in data['outputs'])):
        raise ValueError('Prévia técnica MP4 e exportações MP4/AVI reais não foram verificadas.')
    cancellation = data.get('cancellation', {})
    if not (cancellation.get('button_main_thread') is True
            and cancellation.get('composition_started') is True
            and cancellation.get('children_reaped') is True
            and cancellation.get('no_partials') is True
            and cancellation.get('sources_intact') is True
            and cancellation.get('retry_verified') is True
            and cancellation.get('heartbeats', 0) > 0
            and 0 < cancellation.get('seconds', 0) < 12):
        raise ValueError('Smoke exige cancelamento real pelo botão Tk e nova geração verificada.')
    for row in data['outputs']:
        from native_render_checks import validate_font_render
        validate_font_render(row.get('font_render', {}), profile=row['profile'], export_format=row['format'])
        from native_render_checks import validate_family_render
        validate_family_render(row.get('font_render', {}))
        streams = row['streams']
        width, height = (640, 360) if row['profile'] == 'prova' else (1920, 1080)
        video = streams[0]
        avi = row['format'] == 'AVI'
        container_ok = row['container'] == 'avi' if avi else 'mp4' in row['container'].split(',')
        if not (container_ok and row['full_decode'] and re.fullmatch(r'[0-9a-f]{64}', row['catalog_sha256'])
                and len(streams) == 2 and video['codec_name'] == ('mpeg4' if avi else 'h264')
                and (video['width'], video['height']) == (width, height)
                and video['pix_fmt'] == 'yuv420p' and video['r_frame_rate'] == '30/1'
                and int(video['nb_read_frames']) == 12 and abs(float(video['duration']) - 0.4) < (1/30 + .001 if avi else 1e-6)
                and streams[1]['codec_name'] == ('mp3' if avi else 'aac') and streams[1]['channels'] == 2):
            raise ValueError('Relatório contém streams/decode fora do perfil do smoke.')
        if len(row['sources']) != 2 or row['output_files'] != (['previa.mp4'] if row['profile'] == 'prova' else ['menu.avi' if avi else 'menu.mp4']):
            raise ValueError('Relatório precisa comprovar fontes intactas e vídeo único.')
        for source in row['sources']:
            if not (source['full_decode'] and re.fullmatch(r'[0-9a-f]{64}', source['source_sha256_before'])
                    and source['source_sha256_before'] == source['source_sha256_after']):
                raise ValueError('Relatório contém hash da fonte antes/depois divergente.')


def synthetic_services(directory, progress, scale_seconds=None, *, evidence_directory=None):
    """Executada pelo Worker da própria App; nenhuma chamada de mídia na thread Tk."""
    import gui_servicos as services
    import menu_anisio as motor
    from gui_autoria import Project

    ffmpeg, ffprobe = motor.resolve_tool('ffmpeg'), motor.resolve_tool('ffprobe')
    source, music = directory / 'video-sintetico.mp4', directory / 'musica-sintetica.wav'
    progress('Gerando vídeo e música sintéticos com FFmpeg do pacote…')
    motor.run_media(motor.ffmpeg_base(ffmpeg) + [
        '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30',
        '-f', 'lavfi', '-i', 'sine=frequency=660:sample_rate=48000',
        '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
        '-threads', '2', '-shortest', str(source)], timeout=40)
    motor.run_media(motor.ffmpeg_base(ffmpeg) + [
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
        '-t', '0.8', '-c:a', 'pcm_s16le', str(music)], timeout=40)
    before = {p.name: motor.sha256(p) for p in (source, music)}
    project = Project(title='Smoke sintético', duration=0.4, title_font_size=60)
    project.add_video(services.inspect_media(source))
    project.edit_scene(0, 'Cena sintética', 0, 0.2)
    project.scenes[0].font_size = 36
    project.title_font_family = 'cinzel'
    project.scenes[0].font_family = 'arimo'
    project.add_video(services.inspect_media(source))
    project.edit_scene(1, 'Outra cena', 0, .2)
    project.scenes[1].font_size, project.scenes[1].font_family = 36, 'lobster'
    project.music = services.inspect_media(music, audio=True)
    path = services.save_project(project, directory / 'projeto.menu')
    reopened = services.open_project(path)
    if reopened != project:
        raise ValueError('Salvar/reabrir alterou o projeto sintético.')

    def decode(path):
        motor.run_media(motor.ffmpeg_base(ffmpeg) + [
            '-xerror', '-err_detect', 'explode', '-protocol_whitelist', 'file,pipe',
            '-i', str(path), '-map', '0:v?', '-map', '0:a?', '-threads', '1',
            '-f', 'null', '-'], timeout=60)

    for file in (source, music):
        decode(file)
    source_streams = motor.probe_media(ffprobe, source)['streams']
    if [s['codec_type'] for s in source_streams] != ['video', 'audio']:
        raise ValueError('Fixture sintética sem streams vídeo/áudio esperados.')
    outputs = []
    for profile, export_format in (('prova', 'MP4'), ('1080p', 'MP4'), ('1080p', 'AVI')):
        avi = export_format == 'AVI'
        candidate = copy.deepcopy(reopened)
        candidate.export_format = export_format
        export_directory = directory / ('exportacao-avi' if avi else 'exportacao')
        if profile == '1080p':
            export_directory.mkdir()
        progress(f'Exercitando serviço GUI: {profile}…')
        from native_render_checks import render_font_evidence
        font_directory = (Path(evidence_directory).with_name(Path(evidence_directory).name + '-fonts')
                          if evidence_directory else directory / 'font-evidence') / f'{profile}-{export_format}'
        catalog, font_evidence = render_font_evidence(
            lambda: (services.preview_project(candidate, directory, progress) if profile == 'prova'
                     else services.render_project(candidate, directory, export_directory / ('menu.avi' if avi else 'menu.mp4'),
                                                  profile='1080p', progress=progress)),
            font_directory, profile=profile, export_format=export_format)
        info = motor.probe_media(ffprobe, catalog, count_frames=True)
        streams = info['streams']
        width, height = (640, 360) if profile == 'prova' else (1920, 1080)
        video = streams[0]
        if not (len(streams) == 2 and video['codec_name'] == ('mpeg4' if avi else 'h264')
                and video['width'] == width and video['height'] == height
                and video['pix_fmt'] == 'yuv420p' and video['r_frame_rate'] == '30/1'
                and int(video['nb_read_frames']) == 12
                and abs(motor.media_duration(video, 'smoke') - 0.4) < (1/30 + .001 if avi else 1e-6)
                and streams[1]['codec_name'] == ('mp3' if avi else 'aac') and streams[1]['channels'] == 2):
            raise ValueError(f'Streams fora do contrato: {profile}')
        decode(catalog)
        sources = []
        for original in (source, music):
            after = motor.sha256(original)
            if before[original.name] != after:
                raise ValueError('Hash da fonte antes/depois divergiu.')
            sources.append({'source': original.name, 'source_sha256_before': before[original.name],
                            'source_sha256_after': after, 'full_decode': True})
        files = sorted(p.name for p in catalog.parent.iterdir())
        if files != [catalog.name]:
            raise ValueError('Exportação/prévia contém arquivos além do vídeo.')
        outputs.append({'profile': profile, 'format': export_format, 'container': info['format']['format_name'], 'verified': True, 'full_decode': True,
                        'font_render': font_evidence,
                        'catalog_sha256': motor.sha256(catalog), 'streams': streams,
                        'sources': sources, 'output_files': files})
        if profile == 'prova':
            shutil.rmtree(catalog.parent)
    from native_render_checks import render_family_samples, validate_family_render
    family_directory = (Path(evidence_directory).with_name(Path(evidence_directory).name + '-families')
                        if evidence_directory else directory / 'family-evidence')
    family_samples = render_family_samples(family_directory / 'samples')
    current = copy.deepcopy(reopened)
    current.title_font_family = None
    for scene in current.scenes:
        scene.font_family = None
    current_output = directory / 'current'
    current_output.mkdir()
    _, current_evidence = render_font_evidence(
        lambda: services.render_project(current, None, current_output / 'current.mp4', profile='prova'),
        family_directory / 'current', profile='prova', export_format='MP4')
    validate_family_render(current_evidence, {key: None for key in current_evidence['font_roles']})
    if any(motor.sha256(p) != before[p.name] for p in (source, music)):
        raise ValueError('Fonte sintética foi modificada.')
    from native_render_checks import run_input_duration_case
    duration_case = run_input_duration_case(evidence_directory or directory / 'duration-regression', progress)
    scale = {'input_duration': duration_case}
    if scale_seconds is not None:
        from native_render_checks import create_fixture, run_case, tool_identity
        if scale_seconds not in (15, 30) or isinstance(scale_seconds, bool):
            raise ValueError('Escala explícita deve ser 15 ou 30 segundos.')
        scale['scale_identity'] = tool_identity()
        sources, track = create_fixture(directory / 'scale-fixture', 6)
        scale['scaled_render'] = run_case(directory / 'scale-render', sources, track,
            seconds=scale_seconds, scenes=2, preview=3, progress=progress)
    return {**scale, 'family_samples': family_samples, 'current_font_render': current_evidence, 'project': reopened, 'project_path': path, 'outputs': outputs,
            'tools': {'ffmpeg': ffmpeg, 'ffprobe': ffprobe},
            'services': ['inspect_media', 'save_project', 'open_project', 'preview_project', 'render_project']}


def cancel_then_render(app, directory, project, on_done, on_error):
    """Harness compartilhado por fontes/frozen: filhos reais e botão na thread Tk."""
    import gui_servicos as services
    import menu_anisio as motor
    directory = Path(directory)
    directory.mkdir()
    main_thread = threading.get_ident()
    original_popen = subprocess.Popen
    composition = threading.Event()
    children = []
    sources = [scene.source for scene in project.scenes]
    if project.music:
        sources.append(project.music.source)
    sources += list(directory.parent.glob('*.menu'))
    before = {path: motor.sha256(path) for path in sources}
    long_project = copy.deepcopy(project)
    long_project.duration_mode, long_project.duration = 'manual', 3600
    output = directory / 'cancelado.mp4'
    state = {'heartbeats': 0}
    started = time.monotonic()
    requested = None
    restored = False
    pending_error = None

    def restore():
        nonlocal restored
        if not restored:
            subprocess.Popen = original_popen
            restored = True

    def observe(arguments, *args, **kwargs):
        child = original_popen(arguments, *args, **kwargs)
        children.append(child)
        if '-filter_complex' in arguments:
            composition.set()
        return child

    def retry_done(catalog):
        try:
            state['retry_verified'] = catalog.is_file()
            state['sources_intact'] = all(motor.sha256(path) == digest for path, digest in before.items())
            if not state['sources_intact'] or not state['retry_verified']:
                raise ValueError('Cancelamento/repetição alterou fontes ou não gerou MP4.')
            on_done(state)
        except Exception as exc:
            on_error(exc)

    def monitor():
        nonlocal requested, pending_error
        try:
            if pending_error is not None:
                if app.worker.busy:
                    app.root.after(25, monitor)
                else:
                    restore()
                    on_error(pending_error)
                return
            if app.worker.busy:
                state['heartbeats'] += 1
                if composition.is_set() and requested is None:
                    # O filho já existe. O callback do botão roda no próprio loop Tk.
                    requested = time.monotonic()
                    state['button_main_thread'] = threading.get_ident() == main_thread
                    app.cancel_button.invoke()
                    if app.status_var.get() != 'Cancelando…':
                        raise ValueError('Botão Tk não aceitou cancelamento durante composição.')
                if time.monotonic() - (requested or started) > (12 if requested else 30):
                    app.cancel_render()
                    raise ValueError('Cancelamento real excedeu o orçamento do smoke.')
                app.root.after(25, monitor)
                return
            restore()
            state.update(seconds=time.monotonic() - (requested or started),
                         composition_started=composition.is_set(),
                         children_reaped=bool(children) and all(child.poll() is not None for child in children),
                         no_partials=not list(directory.iterdir()))
            if (requested is None or app.status_var.get() != 'Geração cancelada.'
                    or not state['children_reaped'] or not state['no_partials']):
                raise ValueError('Cancelamento não confirmou reap/cleanup/status.')
            retry = copy.deepcopy(project)
            app.run_render(lambda progress: services.render_project(
                retry, None, directory / 'repetido.mp4', profile='prova', progress=progress), retry_done)
        except Exception as exc:
            if app.worker.busy:
                pending_error = exc
                app.cancel_render()
                app.root.after(25, monitor)
            else:
                restore()
                on_error(exc)

    subprocess.Popen = observe
    try:
        app.run_render(lambda progress: services.render_project(
            long_project, None, output, profile='1080p', progress=progress),
            lambda result: on_error(ValueError('Render prolongado concluiu antes de cancelar.')))
        app.root.after(25, monitor)
    except BaseException:
        restore()
        raise


def run_smoke(report_path, diagnostics, smoke_download=False, scale_seconds=None):
    report_path = Path(report_path).absolute()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    data = {'schema_version': 1, 'launch_id': uuid.uuid4().hex, 'success': False,
            'platform': sys.platform, 'frozen': bool(getattr(sys, 'frozen', False)),
            'executable': sys.executable, 'cwd': str(Path.cwd()), 'path': os.environ.get('PATH', ''),
            'window': {'viewable': False, 'width': 0, 'height': 0, 'map_events': 0, 'destroy_event': False},
            'worker_heartbeats': 0, 'worker_done': False, 'close_requested': False,
            'outputs': [], 'errors': [], 'events': [], 'diagnostic_failures': 0, 'notifications': [],
            'smoke_scale_seconds': scale_seconds,
            'download_authorized': bool(smoke_download), 'network_events': 0, 'bootstrap': None}
    root = app = None
    previous_notify = diagnostics.notify
    # Sem caixas modais no smoke automatizado; os hooks reais continuam gravando e falhando.
    diagnostics.notify = data['notifications'].append

    def write_report():
        data['diagnostic_failures'] = diagnostics.failures
        data['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    try:
        if data['frozen']:
            # Reopen smoke fails on any network attempt, even outside our downloader.
            def network_audit(event, args):
                if event in ('urllib.Request', 'socket.connect', 'socket.getaddrinfo'):
                    data['network_events'] += 1
                    if not smoke_download:
                        raise RuntimeError('Rede proibida no smoke de reabertura offline.')
            sys.addaudithook(network_audit)
            from bootstrap_gui import prepare_tools
            data['bootstrap'] = prepare_tools(diagnostics, authorize_download=smoke_download, smoke=True)
            if data['bootstrap'] is None:
                raise ValueError('Bootstrap do smoke não foi concluído.')
        import tkinter as tk
        from menu_gui import App
        with tempfile.TemporaryDirectory(prefix='anisio-smoke-') as temporary:
            root = tk.Tk()
            diagnostics.attach_tk(root)
            root.bind('<Map>', lambda event: data['window'].__setitem__(
                'map_events', data['window']['map_events'] + (event.widget == root)), add='+')
            root.bind('<Destroy>', lambda event: data['window'].__setitem__(
                'destroy_event', data['window']['destroy_event'] or event.widget == root), add='+')
            app = App(root)
            root.deiconify()
            root.update_idletasks()
            root.update()
            data['window'].update(width=root.winfo_width(), height=root.winfo_height(),
                                  requested_width=root.winfo_reqwidth(), requested_height=root.winfo_reqheight(),
                                  viewable=bool(root.winfo_viewable()), screen_width=root.winfo_screenwidth(),
                                  screen_height=root.winfo_screenheight())
            if not data['window']['viewable']:
                raise RuntimeError('Tk não tem janela real visível; não há fallback de sucesso.')
            data['events'].append('window-visible')
            finished = False

            def close_normally():
                data['close_requested'] = True
                data['events'].append('app-close')
                app.action(app.close)()

            def done(result):
                app.project_path = result.pop('project_path')
                project = result.pop('project')
                app.saved_state = copy.deepcopy(project)
                app.adopt(project)
                data.update(result)
                from native_color_checks import probe_color_preview
                prefix = report_path.with_name(report_path.stem + '-colors') if sys.platform == 'win32' else None
                data['color_preview'] = probe_color_preview(app, prefix)
                from native_color_checks import probe_font_sizes
                font_prefix = report_path.with_name(report_path.stem + '-fonts') if sys.platform == 'win32' else None
                data['font_sizes'] = probe_font_sizes(app, font_prefix)
                from native_color_checks import probe_font_families
                try:
                    data['font_families'] = probe_font_families(app, report_path.with_name(report_path.stem + '-families'),
                                                               capture=sys.platform == 'win32')
                finally:
                    if hasattr(app, 'last_family_report'):
                        data['font_families'] = app.last_family_report
                from native_ui_checks import probe_interface
                data['interface'] = probe_interface(app, report_path.with_name(report_path.stem + '-interface'),
                                                     capture=sys.platform == 'win32')
                data['events'].append('cancel-start')
                cancel_then_render(app, Path(temporary) / 'cancelamento', project, cancel_done, failed)

            def cancel_done(result):
                nonlocal finished
                data['cancellation'] = result
                data['worker_done'] = True
                data['events'].append('worker-done')
                finished = True
                root.after(100, close_normally)

            def failed(error):
                nonlocal finished
                data['errors'].append(''.join(traceback.format_exception(error)))
                diagnostics.report('smoke worker/callback', type(error), error, error.__traceback__)
                finished = True
                root.after(100, close_normally)

            app.show_error = failed
            app.run_job(lambda progress: synthetic_services(Path(temporary), progress, scale_seconds,
                evidence_directory=report_path.with_name(report_path.stem + '-duration-' +
                    ('EXE' if data['frozen'] else 'SOURCE-HOST'))), done)
            data['events'].append('worker-start')

            def heartbeat():
                if app.worker.busy:
                    data['worker_heartbeats'] += 1
                if diagnostics.failures and not app.worker.busy and not finished:
                    close_normally()
                    return
                if time.monotonic() - started > 180 and 'internal-timeout' not in data['events']:
                    data['errors'].append('Smoke excedeu 180s; orquestrador encerrará processo se necessário.')
                    data['events'].append('internal-timeout')
                    write_report()
                root.after(25, heartbeat)

            root.after(25, heartbeat)
            root.mainloop()
            data['events'].append('mainloop-exited')
            data['diagnostic_failures'] = diagnostics.failures
            validate_result(data)
            data['success'] = True
    except Exception:
        info = sys.exc_info()
        data['errors'].append(''.join(traceback.format_exception(*info, chain=True)))
        diagnostics.report('smoke nativo', *info)
    finally:
        if app is not None and not data['window']['destroy_event'] and not app.worker.busy:
            app.shutdown()
        diagnostics.notify = previous_notify
        write_report()
    return 0 if data['success'] else 1
