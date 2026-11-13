"""I/O de autoria. Chamado pelo worker, nunca pelos callbacks Tk diretamente."""

import copy
import json
import ntpath
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

from gui_autoria import MediaInfo, Project, Scene
import menu_video as motor
from font_families import family_id


def inspect_media(source, *, audio=False):
    source = Path(source).resolve()
    if not source.is_file():
        raise motor.ErroProjeto(f'Arquivo inexistente: {source}')
    ffprobe = motor.resolve_tool('ffprobe')
    details = motor.probe_media(ffprobe, source)
    kind = 'audio' if audio else 'video'
    streams = [s for s in details.get('streams', []) if s.get('codec_type') == kind
               and (audio or not s.get('disposition', {}).get('attached_pic'))]
    if not streams:
        raise motor.ErroProjeto(f'{source.name}: sem {"áudio" if audio else "vídeo"}.')
    duration = motor.input_media_duration(ffprobe, source, streams[0], details)
    motor.numero(duration, 'Música' if audio else 'Vídeo', minimo=1/30,
                 maximo=float('inf'))
    return MediaInfo(source, duration)


def relative_media_path(source, directory, *, windows=None):
    windows = os.name == 'nt' if windows is None else windows
    try:
        path = (ntpath if windows else os.path).relpath(str(source), str(directory))
    except ValueError:
        # Unidades Windows distintas: referência absoluta local, sem copiar mídia.
        path = ntpath.normpath(str(source)) if windows else str(Path(source).resolve())
    return path.replace('\\', '/') if windows else path


def project_data(project, directory):
    project.validate()
    duration = {'modo': project.duration_mode}
    if project.duration_mode == 'manual':
        duration['segundos'] = project.duration
    else:
        duration['segundos_manuais'] = project.duration
    data = {'versao': 2, 'modo': 'animado', 'titulo': project.title, 'duracao': duration,
            'formato': project.export_format,
            'alinhamento_titulo': project.title_alignment,
            'visual': dict(project.colors), 'exibir_rodape': project.show_footer,
            'cenas': [{'arquivo': relative_media_path(s.source, directory), 'nome': s.name,
                       'previa': {'inicio': s.start, 'duracao': s.duration}}
                      for s in project.scenes]}
    if project.music:
        data['musica'] = {'arquivo': relative_media_path(project.music.source, directory)}
    if project.title_font_size is not None:
        data['tamanho_titulo'] = motor.font_size(project.title_font_size, 'Título')
    if project.title_font_family is not None:
        data['familia_titulo'] = family_id(project.title_font_family, 'Título')
    for scene, row in zip(project.scenes, data['cenas']):
        if scene.font_family is not None:
            row['familia_fonte'] = family_id(scene.font_family, 'Nome da cena')
        if scene.font_size is not None:
            row['tamanho_fonte'] = motor.font_size(scene.font_size, 'Nome da cena')
    return data


def save_project(project, path):
    path = Path(path).absolute()
    if os.path.lexists(path):
        raise motor.ErroProjeto('O arquivo já existe; escolha um projeto novo para preservar o anterior.')
    path = path.parent.resolve() / path.name
    content = json.dumps(project_data(project, path.parent), ensure_ascii=False,
                         indent=2, allow_nan=False) + '\n'
    # Criação exclusiva: nem um arquivo surgido entre validação e escrita é substituído.
    try:
        stream = path.open('x', encoding='utf-8')
    except FileExistsError as exc:
        raise motor.ErroProjeto('O arquivo já existe; escolha um projeto novo.') from exc
    try:
        with stream:
            stream.write(content)
    except BaseException:
        path.unlink()
        raise
    return path


def open_project(path):
    path = Path(path).resolve()
    try:
        title, rows, _, settings = motor.load_project(
            path, path.parent / f'.gui-validacao-{uuid.uuid4().hex}', with_settings=True)
    except motor.ErroProjeto as exc:
        message = str(exc).replace('JSON inválido; use UTF-8', 'Arquivo de projeto inválido')
        message = message.replace('objeto JSON', 'arquivo de projeto válido')
        raise motor.ErroProjeto(message) from exc
    if settings is None:
        raise motor.ErroProjeto('Esta interface abre projetos de modo animado (1 a 6 cenas).')
    project = Project(title=title, colors=settings['visual'],
                      duration_mode=settings['modo_duracao'],
                      duration=(settings['segundos'] if settings['modo_duracao'] == 'manual'
                                else settings.get('segundos_manuais', 15)),
                      show_footer=settings['exibir_rodape'], export_format=settings['formato'],
                      title_alignment=settings['alinhamento_titulo'],
                      title_font_size=settings.get('tamanho_titulo'),
                      title_font_family=settings.get('familia_titulo'))
    for row in rows:
        info = inspect_media(row['source'])
        project.scenes.append(Scene(info.source, info.duration, row['nome'],
                                    row['previa']['inicio'], row['previa']['duracao'],
                                    row.get('tamanho_fonte'), row.get('familia_fonte')))
    if settings['musica']:
        project.music = inspect_media(settings['musica']['source'], audio=True)
    project.validate()
    return project


def available_export_path(output):
    """Política da GUI: nomes locais exclusivos, inclusive com case Windows."""
    output = Path(output).absolute()
    if output.suffix.lower() not in ('.mp4', '.avi'):
        raise motor.ErroProjeto('Informe um nome de arquivo .mp4 ou .avi para exportar.')
    occupied = {path.name.casefold() for path in output.parent.iterdir()}
    candidate = output
    match = re.fullmatch(r'(.*) \(([0-9]+)\)', output.stem)
    stem, number = (match[1], int(match[2])) if match else (output.stem, 0)
    while os.path.lexists(candidate) or candidate.name.casefold() in occupied:
        number += 1
        candidate = output.with_name(f'{stem} ({number}){output.suffix}')
    return candidate


def render_project(project, directory, output, *, profile='1080p', progress=lambda message: None, cancel=None):
    """Autoria em memória: nenhum projeto salvo ou volume comum é necessário."""
    project.validate()
    requested = Path(output).absolute()
    if requested.suffix.lower() != '.' + project.export_format.lower():
        raise motor.ErroProjeto('A extensão deve corresponder ao formato escolhido.')
    output = available_export_path(requested)
    notice = 'Já existe um arquivo com esse nome. ' if output != requested else ''
    progress(f'{notice}O vídeo será salvo como “{output.name}”: {output}. Validando mídias e gerando menu…')
    conflict = (f'Já existe outro arquivo ou uma geração usando “{output.name}”. '
                'Tente exportar novamente para escolher outro nome. Nenhum arquivo foi substituído.')
    if os.path.lexists(output) or os.path.lexists(output.parent / f'.{output.name}.menu-video.lock'):
        raise motor.ErroProjeto(conflict)
    scenes = [{'source': scene.source.resolve(), 'nome': scene.name,
               'tamanho_fonte': scene.font_size, 'familia_fonte': scene.font_family,
               'previa': {'inicio': scene.start, 'duracao': scene.duration}}
              for scene in project.scenes]
    settings = {'modo_duracao': project.duration_mode, 'segundos': project.effective_duration(),
                'visual': dict(project.colors), 'exibir_rodape': project.show_footer,
                'formato': project.export_format,
                'alinhamento_titulo': project.title_alignment,
                'tamanho_titulo': project.title_font_size, 'familia_titulo': project.title_font_family,
                'musica': {'source': project.music.source.resolve()} if project.music else None}
    try:
        result = motor.export_media(project.title, scenes, output, settings, profile, cancel=cancel, progress=progress)
    except motor.ErroProjeto as exc:
        if str(exc) in ('a saída já existe; escolha um arquivo novo',
                        'a saída já existe; exportação cancelada',
                        'outra exportação reservou esta saída'):
            raise motor.ErroProjeto(conflict) from exc
        raise
    progress(f'Menu {project.export_format} gerado e conferido: {result}')
    return result


def preview_project(project, directory=None, progress=lambda message: None, *, cancel=None):
    # Só o MP4 é retido, no temporário do sistema, para o player externo.
    directory = Path(tempfile.mkdtemp(prefix='menu-previa-'))
    try:
        preview = copy.deepcopy(project)
        preview.export_format = 'MP4'
        return render_project(preview, directory, directory / 'previa.mp4',
                              profile='prova', progress=progress, cancel=cancel)
    except BaseException:
        shutil.rmtree(directory)
        raise


def open_player(path):
    path = Path(path).absolute()
    if not path.is_file():
        raise motor.ErroProjeto('O vídeo não está mais disponível; exporte novamente.')
    try:
        if sys.platform == 'win32':
            os.startfile(str(path))
        else:
            executable = shutil.which('open' if sys.platform == 'darwin' else 'xdg-open')
            if not executable:
                raise motor.ErroProjeto(f'Player padrão indisponível. Abra o arquivo: {path}')
            subprocess.run([executable, str(path)], check=True, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise motor.ErroProjeto(f'Não foi possível abrir o player padrão. Arquivo preservado: {path}') from exc


def output_available(path):
    try:
        return (path is not None and Path(path).suffix.lower() in ('.mp4', '.avi')
                and Path(path).is_file() and Path(path).stat().st_size > 0)
    except OSError:
        return False


def open_output_folder(path):
    """Recebe somente a saída conferida do job; nunca uma fonte do projeto."""
    if not output_available(path):
        raise motor.ErroProjeto('O vídeo não está disponível. Exporte um vídeo antes de abrir a pasta.')
    directory = Path(path).resolve().parent
    try:
        if sys.platform == 'win32':
            os.startfile(str(directory))
            return
        executable = shutil.which('open' if sys.platform == 'darwin' else 'xdg-open')
        if not executable:
            raise OSError('Gerenciador de arquivos indisponível')
        subprocess.run([executable, str(directory)], check=True, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise motor.ErroProjeto(f'Não foi possível abrir a pasta. O vídeo está preservado em: {directory}') from exc
