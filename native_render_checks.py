"""Evidência sintética de EOF e tipografia; gates por fontes e frozen.

Não altera política de render, licença ou resolução das ferramentas do motor.
"""
from fractions import Fraction
import array
import json
import math
from pathlib import Path
import re
import sys
import time
import copy

import client_download
import menu_anisio as motor


def render_font_evidence(operation, directory, *, profile, export_format):
    """Observe a composição executada de fato; retenha vídeo/frame sintéticos.

    Reusa os renders curtos do smoke. O observador encaminha todas as chamadas
    ao runner original, sem simular FFmpeg ou trocar seus resultados.
    """
    import shutil
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    evidence = {'profile': profile, 'format': export_format}
    original = motor.run_media
    original_measure = motor.measure_text_pixels
    evidence['measurements'] = []
    def measure(ffmpeg, work, font, value, size, spacing):
        measured = original_measure(ffmpeg, work, font, value, size, spacing)
        path = work / font.split('=', 1)[1] if font.startswith('fontfile=') else None
        evidence['measurements'].append({'file': path.name if path else None,
            'sha256': motor.sha256(path) if path else None, 'text': value, 'size': size,
            'width': measured[0], 'height': measured[1]})
        return measured
    def observe(command, **kwargs):
        result = original(command, **kwargs)
        if '-filter_complex' in command:
            graph = command[command.index('-filter_complex') + 1]
            evidence.update(graph=graph, composition_exit=result.returncode,
                            command=list(command))
            evidence['sizes'] = {}
            for name, stem in (('title', 'titre'), ('scene', 'label-1'), ('number', 'number-1')):
                found = re.findall(r'textfile=' + stem + r'(?:-line\d+)?\.txt:expansion=none:fontsize=(\d+):', graph)
                if not found or len(set(found)) != 1:
                    raise ValueError('Grafo real não comprovou tamanho de fonte uniforme por texto.')
                evidence['sizes'][name] = int(found[0])
            work = Path(kwargs['cwd'])
            roles_path = work / 'font-roles.json'
            if roles_path.is_file():
                evidence['font_roles'] = json.loads(roles_path.read_text(encoding='utf-8'))
                import font_families as fonts
                evidence['font_manifest_sha256'] = fonts.MANIFEST_SHA256
            evidence['texts'] = {name: (work / filename).read_text(encoding='utf-8')
                                 for name, filename in (('title', 'titre.txt'), ('scene', 'label-1.txt'))}
        return result
    try:
        motor.run_media = observe
        motor.measure_text_pixels = measure
        output = operation()
    finally:
        motor.run_media = original
        motor.measure_text_pixels = original_measure
        (directory / 'font-render-partial.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    if evidence.get('composition_exit') != 0:
        raise ValueError('Composição real não foi observada com sucesso.')
    retained = directory / ('menu.' + export_format.lower())
    shutil.copyfile(output, retained)
    frame = directory / 'frame.png'
    motor.run_media(motor.ffmpeg_base(motor.resolve_tool('ffmpeg')) + [
        '-i', str(retained), '-frames:v', '1', '-threads', '1', str(frame)])
    evidence.update(sample=str(retained), sample_sha256=motor.sha256(retained),
                    frame=str(frame), frame_sha256=motor.sha256(frame))
    (directory / 'font-render.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return output, evidence


def validate_font_render(data, *, profile, export_format):
    """Gate dos tamanhos manuais do smoke existente; não aceita só verified=True."""
    try:
        factor = 1 if profile == '1080p' else 1/3
        assert data['profile'] == profile and data['format'] == export_format
        assert data['composition_exit'] == 0
        expected = {key: round(size * factor) for key, size in (('title', 60), ('scene', 36), ('number', 32))}
        assert data['sizes'] == expected
        for key, stem in (('title', 'titre'), ('scene', 'label-1'), ('number', 'number-1')):
            values = re.findall(r'textfile=' + stem + r'(?:-line\d+)?\.txt:expansion=none:fontsize=(\d+):', data['graph'])
            assert values and all(int(value) == expected[key] for value in values)
        for kind in ('sample', 'frame'):
            assert data[kind] and re.fullmatch('[0-9a-f]{64}', data[kind + '_sha256'])
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise ValueError('Evidência de render real com fonte manual incompleta ou inválida.') from exc


def validate_family_render(data, expected=None):
    """Cada papel precisa ligar ID, TTF fixado, corpo medido e drawtext executado."""
    import font_families as fonts
    expected = expected or {'titre.txt': 'cinzel', 'label-1.txt': 'arimo', 'label-2.txt': 'lobster',
                            'number-1.txt': None, 'number-2.txt': None}
    pins = {row['id']: row['files'][0]['sha256'] for row in fonts.audit_assets()['families']}
    legacy = motor.find_font(None)
    def require(condition, key):
        if not condition:
            raise ValueError(f'Evidência de famílias no render inválida: {key}.')
    try:
        require(data['font_manifest_sha256'] == fonts.MANIFEST_SHA256, 'manifest.sha256')
        require(set(data['font_roles']) == set(expected), 'roles.ids')
        for filename, identity in expected.items():
            row = data['font_roles'][filename]
            require(row['id'] == identity, filename + '.id')
            file = f'family-{identity}.ttf' if identity else 'font.ttf'
            digest = pins[identity] if identity else motor.sha256(legacy)
            require(row['file'] == file and row['sha256'] == digest, filename + '.sha256')
            require(bool(row['text']) and row['measured_width'] > 0 and row['measured_height'] > 0, filename + '.body')
            stem = Path(filename).stem
            fragments = re.findall(r'drawtext=fontfile=' + re.escape(file) + r':textfile=' +
                re.escape(stem) + r'(?:-line\d+)?\.txt:expansion=none:fontsize=(\d+):', data['graph'])
            require(len(fragments) == len(row['text'].split('\n')) and all(int(v) == row['size'] for v in fragments), filename + '.graph')
            for line in row['text'].split('\n'):
                require(any(m['file'] == file and m['sha256'] == digest and m['text'] == line and m['size'] == row['size']
                            and m['width'] > 0 and m['height'] > 0 for m in data['measurements']), filename + '.measured_text')
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError(f'Evidência de famílias no render incompleta: {exc}.') from exc


def validate_input_duration_case(row, *, frozen=False):
    """Exige dados concretos da regressão; não é o review independente."""
    def require(condition):
        if not condition:
            raise ValueError()
    def digest(value):
        return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)
    def finite(value):
        return not isinstance(value, bool) and math.isfinite(float(value))
    try:
        require(row['success'] is True and row['errors'] == [] and row['save_reopen_equal'] is True)
        origin = row['origin']
        require(origin in ('SOURCE-HOST', 'EXE'))
        if frozen:
            require(origin == 'EXE' and row['frozen'] is True and row['platform'] == 'win32')
        require(digest(row['project_sha256_before']) and row['project_sha256_before'] == row['project_sha256_after'])
        require(finite(row['music_seconds']) and abs(row['music_seconds'] - 1.5) < .001)
        require(len(row['inputs']) == 2)
        for item, method in zip(row['inputs'], ('tags.DURATION', 'timestamps.frames')):
            require(item['origin'] == origin and item['method'] == method and item['stream_index'] == 1)
            require(finite(item['seconds']) and abs(item['seconds'] - 1) <= .001)
            require(digest(item['source_sha256_before']) and item['source_sha256_before'] == item['source_sha256_after'])
            require(type(item['full_decode_exit']) is int and item['full_decode_exit'] == 0)
            video = item['stream']
            require(video['codec_type'] == 'video' and not video.get('disposition', {}).get('attached_pic'))
            require('duration' not in video and 'duration_ts' not in video)
            if method == 'timestamps.frames':
                require('DURATION' not in video.get('tags', {}) and 'duration' not in item['format'])
                require(item['frames'] == 30 and finite(Fraction(item['first_timestamp']))
                        and finite(Fraction(item['end_timestamp'])))
            else:
                require(video['tags']['DURATION'] == '00:00:01.000000000')
        require(len(row['outputs']) == 2 and {item['format'] for item in row['outputs']} == {'MP4', 'AVI'})
        for item in row['outputs']:
            require(item['origin'] == origin and type(item['decode_exit']) is int and item['decode_exit'] == 0)
            require(digest(item['sample_sha256']) and digest(item['frame_sha256']))
            require(Path(item['sample']).name.startswith(origin + '-') and Path(item['frame']).name.startswith(origin + '-'))
            video, audio = item['streams']
            avi = item['format'] == 'AVI'
            require(item['container'] == 'avi' if avi else 'mp4' in item['container'].split(','))
            require(video['codec_name'] == ('mpeg4' if avi else 'h264') and audio['codec_name'] == ('mp3' if avi else 'aac'))
            require((video['width'], video['height'], video['pix_fmt'], video['r_frame_rate']) == (1920, 1080, 'yuv420p', '30/1'))
            require(audio['channels'] == 2 and int(video['nb_read_frames']) == 12)
            require(abs(motor.media_duration(video, 'smoke duração') - .4) <= (1/30 + .001 if avi else 1e-6))
            timestamps = item['timestamps']
            require(timestamps['method'] == 'timestamps.frames' and timestamps['frames'] == 12)
            require(finite(timestamps['seconds']) and abs(timestamps['seconds'] - .4) <= (1/30 + .001 if avi else .001))
            require(finite(Fraction(timestamps['first_timestamp'])) and finite(Fraction(timestamps['end_timestamp'])))
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError, motor.ErroProjeto) as exc:
        raise ValueError('Evidência de duração MKV incompleta ou inválida.') from exc


def run_input_duration_case(directory, progress=lambda message: None):
    """Mesma regressão no host e no EXE; artefatos sobrevivem a falha posterior."""
    import gui_servicos as services
    from gui_autoria import Project
    directory = Path(directory).resolve()
    directory.mkdir(parents=True)
    origin = 'EXE' if getattr(sys, 'frozen', False) else 'SOURCE-HOST'
    report_path = directory / f'{origin}-duration-report.json'
    row = {'origin': origin, 'platform': sys.platform, 'frozen': bool(getattr(sys, 'frozen', False)),
           'inputs': [], 'outputs': [], 'errors': [], 'report': str(report_path)}
    def save():
        report_path.write_text(json.dumps(row, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    save()
    try:
        ffmpeg, ffprobe = motor.resolve_tool('ffmpeg'), motor.resolve_tool('ffprobe')
        project = Project(title='Regressão duração MKV', duration=.4)
        sources = []
        for live in (False, True):
            source = directory / ('sem-metadata.mkv' if live else 'tag-normal.mkv')
            progress('Conferindo entrada MKV sem duração de stream…')
            motor.run_media(motor.ffmpeg_base(ffmpeg) + [
                '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=30:duration=1',
                '-f', 'lavfi', '-i', 'sine=sample_rate=48000:duration=1.5',
                '-map', '1:a', '-map', '0:v', '-c:v', 'libx264', '-bf', '2',
                '-threads', '1', '-c:a', 'pcm_s16le', *(['-live', '1'] if live else []), str(source)])
            details = motor.probe_media(ffprobe, source)
            video = next(s for s in details['streams'] if s['codec_type'] == 'video')
            if 'duration' in video or 'duration_ts' in video:
                raise ValueError('Fixture MKV precisa reproduzir ausência de duration do stream.')
            if live and ('DURATION' in video.get('tags', {}) or 'duration' in details['format']):
                raise ValueError('Fixture live precisa obrigar medição real.')
            resolution = motor.input_duration_info(ffprobe, source, video, details)
            expected = 'timestamps.frames' if live else 'tags.DURATION'
            if resolution['method'] != expected or abs(resolution['seconds'] - 1) > .001:
                raise ValueError('Duração/método da faixa MKV incorreto.')
            imported = services.inspect_media(source)
            if imported.duration != resolution['seconds']:
                raise ValueError('Inspeção diverge do resolvedor de entrada.')
            project.add_video(imported)
            project.edit_scene(len(project.scenes) - 1, 'Cena medida' if live else 'Cena com tag', .1, .2)
            motor.check_decode(ffmpeg, source)
            sources.append(source)
            row['inputs'].append({**resolution, 'source': str(source), 'origin': origin,
                'source_sha256_before': motor.sha256(source), 'full_decode_exit': 0,
                'stream': video, 'format': details['format']})
            save()
        project.music = services.inspect_media(sources[-1], audio=True)
        project_path = services.save_project(project, directory / 'projeto.menu')
        project_before = motor.sha256(project_path)
        reopened = services.open_project(project_path)
        row.update(save_reopen_equal=reopened == project, project=str(project_path),
                   project_sha256_before=project_before, music_seconds=project.music.duration)
        if reopened != project:
            raise ValueError('Salvar/reabrir alterou projeto MKV.')
        save()
        for export_format in ('MP4', 'AVI'):
            candidate = copy.deepcopy(reopened)
            candidate.export_format = export_format
            output = services.render_project(candidate, directory,
                directory / f'{origin}-menu.{export_format.lower()}', profile='1080p', progress=progress)
            details = motor.probe_media(ffprobe, output, count_frames=True)
            video, audio = details['streams']
            avi = export_format == 'AVI'
            container = details['format']['format_name']
            if not ((container == 'avi' if avi else 'mp4' in container.split(','))
                    and video['codec_name'] == ('mpeg4' if avi else 'h264')
                    and audio['codec_name'] == ('mp3' if avi else 'aac') and audio['channels'] == 2
                    and (video['width'], video['height'], video['pix_fmt'], video['r_frame_rate']) == (1920, 1080, 'yuv420p', '30/1')
                    and int(video['nb_read_frames']) == 12
                    and abs(motor.media_duration(video, 'MKV smoke') - .4) <= (1/30 + .001 if avi else 1e-6)):
                raise ValueError('Saída MKV smoke fora do contrato MP4/AVI1080p.')
            motor.check_decode(ffmpeg, output, duration=.4)
            measured = motor.measure_input_duration(ffprobe, output, video)
            if abs(measured['seconds'] - .4) > (1/30 + .001 if avi else .001):
                raise ValueError('Timestamps da saída inválidos.')
            frame = directory / f'{origin}-frame-{export_format}.png'
            motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-protocol_whitelist', 'file,pipe',
                '-i', str(output), '-frames:v', '1', '-threads', '1', str(frame)])
            row['outputs'].append({'origin': origin, 'format': export_format, 'container': container,
                'sample': str(output), 'sample_sha256': motor.sha256(output),
                'frame': str(frame), 'frame_sha256': motor.sha256(frame), 'decode_exit': 0,
                'streams': details['streams'], 'timestamps': measured})
            for source, entry in zip(sources, row['inputs']):
                entry['source_sha256_after'] = motor.sha256(source)
                if entry['source_sha256_after'] != entry['source_sha256_before']:
                    raise ValueError('Fonte MKV foi modificada.')
            row['project_sha256_after'] = motor.sha256(project_path)
            if row['project_sha256_after'] != project_before:
                raise ValueError('Projeto MKV foi modificado pela exportação.')
            # Grava cada prova concluída antes da próxima exportação/etapa.
            save()
        row['success'] = True
        save()
        validate_input_duration_case(row, frozen=bool(getattr(sys, 'frozen', False)))
        return row
    except BaseException as exc:
        row['success'] = False
        row['errors'].append(f'{type(exc).__name__}: {exc}')
        save()
        raise


def tool_identity(portable_root=None):
    manifest = client_download.load_manifest()
    if portable_root is None and getattr(sys, 'frozen', False):
        from bootstrap_gui import app_root
        portable_root = app_root()
    cache = client_download.verified_cache(Path(portable_root).resolve(), manifest) if portable_root else None
    tools = {}
    for name in ('ffmpeg', 'ffprobe'):
        path = Path(motor.resolve_tool(name)).resolve()
        if portable_root and (not cache or not path.samefile(Path(cache['directory']) / (name + '.exe'))):
            raise ValueError('Ferramenta efetiva diverge do cache portátil verificado.')
        version = motor.run_media([str(path), '-version']).stdout.decode('utf-8', errors='replace').splitlines()[0]
        tools[name] = {'path': str(path), 'sha256': motor.sha256(path), 'version': version}
    manifest_path = Path(client_download.__file__).parent / 'windows_build/client-download-manifest.json'
    return {'platform': sys.platform, 'frozen': bool(getattr(sys, 'frozen', False)),
            'portable_verified': cache is not None, 'manifest_sha256': client_download.fingerprint(manifest),
            'manifest_file_sha256': motor.sha256(manifest_path),
            'archive_sha256': manifest['binary']['sha256'], 'tools': tools}


def create_fixture(directory, music_seconds):
    directory = Path(directory).resolve()
    directory.mkdir()
    ffmpeg = motor.resolve_tool('ffmpeg')
    sources = []
    for index, size in enumerate(('640x360', '360x640')):
        source = directory / f'fonte-{index}.mp4'
        motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
            f'testsrc2=size={size}:rate=30:duration=3', '-an', '-c:v', 'libx264',
            '-threads', '2', '-pix_fmt', 'yuv420p', str(source)])
        sources.append(source)
    music = directory / 'trilha.mp3'
    # MP3 inclui um frame de padding no duration do demuxer. Ajuste a
    # fixture, nunca o validador, para menor/igual/maior significarem duração real.
    nominal = music_seconds
    for attempt in range(3):
        candidate = directory / f'trilha-{attempt}.mp3'
        motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-f', 'lavfi', '-i',
            f'sine=frequency=440:sample_rate=48000:duration={nominal}',
            '-c:a', 'libmp3lame', '-b:a', '192k', str(candidate)])
        duration = motor.media_duration(motor.probe_media(motor.resolve_tool('ffprobe'), candidate)['streams'][0], 'MP3')
        if abs(duration - music_seconds) < 1e-6:
            candidate.rename(music)
            break
        nominal -= duration - music_seconds
    else:
        raise ValueError(f'Fixture MP3 não atingiu duração exata {music_seconds}: {duration}')
    return sources, music


def validate_case(row, *, seconds, scenes, preview):
    """Campos concretos obrigatórios; flags isoladas nunca bastam."""
    def require(condition):
        if not condition:
            raise ValueError('Campo obrigatório inválido.')
    try:
        video, audio = row['streams']
        composition = row['composition']
        expected_frames = round(seconds * 30)
        require(row['seconds'] == seconds and row['scene_count'] == scenes and row['preview_seconds'] == preview)
        require(row['profile'] == '1080p' and row['full_decode'] is True)
        require(row['frames'] == expected_frames == int(video['nb_read_frames']))
        require(video['codec_name'] == 'h264' and video['pix_fmt'] == 'yuv420p')
        require((video['width'], video['height'], video['r_frame_rate']) == (1920, 1080, '30/1'))
        require(abs(Fraction(video['duration_ts']) * Fraction(video['time_base']) - Fraction(str(seconds))) < Fraction(1, 1000))
        require(audio['codec_name'] == 'aac' and audio['channels'] == 2)
        require(abs(Fraction(audio['duration_ts']) * Fraction(audio['time_base']) - Fraction(str(seconds))) <= Fraction(1, 48000))
        require(type(composition['exit_code']) is int and composition['exit_code'] == 0 and composition['timed_out'] is False)
        require(composition['spontaneous_exit'] is True and composition['progress_end'] is True)
        require(composition['progress_frames'] == expected_frames and 0 < composition['elapsed_seconds'] < 90)
        require(composition['background_seconds'] == seconds and composition['output_limit_seconds'] == seconds)
        require(composition['preview_loop_count'] == scenes and composition['music_looped'] is False)
        require(row['music_seconds'] > 0 and row['audio_rms_start'] > 100)
        require(row['non_repeating'] is True)
        if row['music_seconds'] < seconds:
            require(row['post_track_samples'] >= 800 and row['post_track_rms'] < 2)
        require(re.fullmatch('[0-9a-f]{64}', row['output_sha256']))
        require(re.fullmatch('[0-9a-f]{64}', row['decode_sha256']))
        require(len(row['sources']) == 3)
        for source in row['sources']:
            require(source['full_decode'] is True)
            require(re.fullmatch('[0-9a-f]{64}', source['before']) and source['before'] == source['after'])
    except (AssertionError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError('Evidência de composição EOF incompleta ou inválida.') from exc


def run_case(directory, sources, music, *, seconds, scenes, preview, progress=lambda message: None):
    directory = Path(directory).resolve()
    sources = [Path(path).resolve() for path in sources]
    music = Path(music).resolve()
    directory.mkdir()
    ffmpeg, ffprobe = motor.resolve_tool('ffmpeg'), motor.resolve_tool('ffprobe')
    originals = [*sources, music]
    before = {path: motor.sha256(path) for path in originals}
    music_seconds = motor.media_duration(motor.probe_media(ffprobe, music)['streams'][0], 'MP3')
    project = directory / 'projeto.json'
    project.write_text(json.dumps({'modo': 'animado', 'titulo': 'Regressão EOF',
        'duracao': {'modo': 'manual', 'segundos': seconds}, 'musica': {'arquivo': str(music)},
        'cenas': [{'arquivo': str(sources[i % 2]), 'nome': f'Cena {i+1}',
                   'previa': {'inicio': 0, 'duracao': preview}} for i in range(scenes)]}, ensure_ascii=False), encoding='utf-8')
    # API de fontes aceita caminhos absolutos; o projeto persistido é apenas evidência.
    rows = [{'source': sources[i % 2], 'nome': f'Cena {i+1}',
             'previa': {'inicio': 0, 'duracao': preview}} for i in range(scenes)]
    settings = {'segundos': seconds, 'modo_duracao': 'manual', 'exibir_rodape': True,
                'musica': {'source': music},
                'visual': {'fundo': '#101f33', 'texto': '#f3ead6', 'destaque': '#c7a362'}}
    commands = []
    original_run = motor.run_media
    composition = None

    def observe(command, **kwargs):
        nonlocal composition
        command = list(command)
        is_composition = '-filter_complex' in command
        entry = {'command': command, 'exit_code': None, 'timed_out': False}
        commands.append(entry)
        index = len(commands)
        if is_composition:
            command = command[:-1] + ['-progress', 'pipe:1', '-nostats', *command[-1:]]
            entry['command'] = command
            kwargs['timeout'] = min(kwargs.get('timeout', 90), 90)
            composition = entry
        started = time.monotonic()
        try:
            result = original_run(command, **kwargs)
            entry['exit_code'] = result.returncode
            (directory / f'{index:03d}-stdout.log').write_bytes(result.stdout)
            (directory / f'{index:03d}-stderr.log').write_bytes(result.stderr)
            if is_composition:
                stdout = result.stdout.decode('utf-8', errors='replace')
                color = command[command.index('-i') + 1]
                input_positions = [i for i, token in enumerate(command) if token == '-i']
                music_looped = '-stream_loop' in command[input_positions[-2]+2:input_positions[-1]]
                entry.update(spontaneous_exit=result.returncode == 0,
                    progress_end='progress=end' in stdout,
                    progress_frames=int(re.findall(r'^frame=(\d+)', stdout, re.M)[-1]),
                    background_seconds=float(color.rsplit(':d=', 1)[1]),
                    output_limit_seconds=float(command[command.index('-t') + 1]),
                    preview_loop_count=command.count('-stream_loop'),
                    music_looped=music_looped)
            return result
        except Exception as exc:
            import subprocess
            entry['timed_out'] = isinstance(exc.__cause__, subprocess.TimeoutExpired)
            entry['error'] = str(exc)
            cause = exc.__cause__
            entry['exit_code'] = getattr(cause, 'returncode', None)
            for name in ('stdout', 'stderr'):
                output = getattr(cause, name, None)
                if output:
                    (directory / f'{index:03d}-{name}.log').write_bytes(output if isinstance(output, bytes) else output.encode('utf-8'))
            raise
        finally:
            entry['elapsed_seconds'] = time.monotonic() - started
            (directory / 'commands.json').write_text(json.dumps(commands, indent=2), encoding='utf-8')

    try:
        progress(f'Compondo {scenes} cenas / {seconds}s…')
        motor.run_media = observe
        output = motor.export_media('Regressão EOF', rows, directory / 'menu.mp4', settings, '1080p')
    finally:
        motor.run_media = original_run
    streams = motor.probe_media(ffprobe, output, count_frames=True)['streams']

    def decode(path):
        result = motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-xerror', '-err_detect', 'explode',
            '-i', str(path), '-map', '0:v?', '-map', '0:a?', '-threads', '1', '-f', 'hash', '-hash', 'sha256', '-'])
        return result.stdout.decode('utf-8').strip().split('=')[-1].lower()

    decoded = decode(output)
    source_rows = [{'name': path.name, 'before': before[path], 'after': motor.sha256(path),
                    'full_decode': bool(decode(path))} for path in originals]
    raw = motor.run_media(motor.ffmpeg_base(ffmpeg) + ['-i', str(output), '-map', '0:a:0',
        '-ac', '1', '-ar', '8000', '-f', 's16le', '-']).stdout
    samples = array.array('h', raw)
    if sys.byteorder != 'little':
        samples.byteswap()
    def rms(part):
        return math.sqrt(sum(x*x for x in part) / len(part)) if part else None
    tail = samples[math.ceil((music_seconds + .25) * 8000):int((seconds - .1) * 8000)] if music_seconds < seconds else []
    row = {'seconds': seconds, 'scene_count': scenes, 'preview_seconds': preview, 'profile': '1080p',
           'streams': streams, 'frames': int(streams[0]['nb_read_frames']), 'full_decode': True,
           'output_sha256': motor.sha256(output), 'decode_sha256': decoded, 'sources': source_rows,
           'music_seconds': music_seconds, 'audio_rms_start': rms(samples[800:2400]),
           'post_track_samples': len(tail), 'post_track_rms': rms(tail),
           'non_repeating': composition['music_looped'] is False and (not tail or rms(tail) < 2),
           'composition': composition}
    (directory / 'result.json').write_text(json.dumps(row, indent=2), encoding='utf-8')
    validate_case(row, seconds=seconds, scenes=scenes, preview=preview)
    return row


def render_family_samples(directory):
    """Uma imagem por opção, FreeType real; nenhuma matriz adicional de vídeos."""
    import shutil
    import tempfile
    import font_families as fonts
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    report = {'manifest_sha256': fonts.MANIFEST_SHA256, 'samples': []}
    fonts.audit_assets()
    try:
        with tempfile.TemporaryDirectory(prefix='family-samples-') as tmp:
            work = Path(tmp)
            text = 'Uma noite para recordar'
            (work/'sample.txt').write_text(text, encoding='utf-8')
            ffmpeg = motor.resolve_tool('ffmpeg')
            for identity in (None, *fonts.FAMILIES):
                source = fonts.font_path(identity) if identity else motor.find_font(None)
                shutil.copyfile(source, work/'approved.ttf')
                width, height = motor.measure_text_pixels(ffmpeg, work, 'fontfile=approved.ttf', text, 44, 0)
                frame = directory / ((identity or 'current')+'.png')
                result = motor.run_media(motor.ffmpeg_base(ffmpeg)+[
                    '-f','lavfi','-i','color=c=0x101f33:s=800x140:r=1',
                    '-vf','drawtext=fontfile=approved.ttf:textfile=sample.txt:expansion=none:fontsize=44:fontcolor=white:x=20:y=40',
                    '-frames:v','1','-threads','1',str(frame)],cwd=work)
                report['samples'].append({'id':identity,'file':str(source),'sha256':motor.sha256(source),
                    'text':text,'size':44,'measured_width':width,'measured_height':height,
                    'frame':str(frame),'frame_sha256':motor.sha256(frame),'exit_code':result.returncode})
        validate_family_samples(report)
        return report
    finally:
        (directory/'family-samples.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


def validate_family_samples(data):
    import font_families as fonts
    def require(condition,key):
        if not condition:
            raise ValueError(f'Evidência FreeType das famílias inválida: {key}.')
    try:
        require(data['manifest_sha256']==fonts.MANIFEST_SHA256,'manifest.sha256')
        rows=data['samples']
        require([r['id'] for r in rows]==[None,*fonts.FAMILIES],'samples.ids')
        pins={r['id']:r['files'][0]['sha256'] for r in fonts.audit_assets()['families']}
        for row in rows:
            identity=row['id']; expected=pins[identity] if identity else motor.sha256(motor.find_font(None))
            require(row['sha256']==expected and bool(row['file']),f'samples.{identity}.sha256')
            require(row['text']=='Uma noite para recordar' and row['size']==44
                    and row['measured_width']>0 and row['measured_height']>0,f'samples.{identity}.measured_text')
            require(type(row['exit_code']) is int and row['exit_code']==0 and bool(row['frame'])
                    and re.fullmatch('[0-9a-f]{64}',row['frame_sha256']),f'samples.{identity}.frame')
        require(len({r['frame_sha256'] for r in rows[1:]})==8,'samples.distinct_frames')
    except (KeyError,TypeError,IndexError) as exc:
        raise ValueError(f'Evidência FreeType das famílias incompleta: {exc}.') from exc
