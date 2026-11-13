"""Motor local do Gerador de menus em vídeo; somente biblioteca padrão."""

import argparse
from contextlib import nullcontext
import errno
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unicodedata
from fractions import Fraction
from pathlib import Path, PureWindowsPath
import media_runtime
from font_families import family_id, font_path


class ErroProjeto(Exception):
    pass


def resolve_tool(name):
    try:
        return media_runtime.resolve_tool(name)
    except RuntimeError as exc:
        raise ErroProjeto(str(exc)) from exc


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("chave JSON duplicada")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError("constante não JSON")


def texto(value, campo, limite):
    if (not isinstance(value, str) or not value.strip() or len(value) > limite
            or any(unicodedata.category(c).startswith("C") for c in value)):
        raise ErroProjeto(f"{campo}: texto obrigatório, sem controles, até {limite} caracteres")
    return unicodedata.normalize("NFC", value.strip())


def numero(value, campo, *, minimo=0, maximo=math.inf, aberto=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or value > maximo or (value <= minimo if aberto else value < minimo)
            or not math.isfinite(value)):
        raise ErroProjeto(f"{campo}: número finito entre {minimo} e {maximo}" +
                          (", maior que o mínimo" if aberto else ""))
    return float(value)


def font_size(value, campo='Tamanho da fonte'):
    """None conserva autofit legado; tamanho explícito é px na referência 1080p."""
    if value is None:
        return None
    if (type(value) not in (int, float) or not 1 <= value <= 200
            or not math.isfinite(value) or int(value) != value):
        raise ErroProjeto(f'{campo}: informe um inteiro de 1 a 200 px (referência 1080p).')
    return int(value)


def local_source(filename, project, output, campo, *, allow_absolute=False):
    if not isinstance(filename, str) or not filename or "\x00" in filename:
        raise ErroProjeto(f"{campo}: arquivo deve ser um caminho relativo local ao projeto")
    windows = PureWindowsPath(filename)
    absolute = Path(filename).is_absolute() or windows.is_absolute()
    scheme = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", filename)
    if ((scheme and not (windows.drive and len(windows.drive) == 2))
            or (windows.drive and not windows.is_absolute())
            or (windows.root and not windows.drive and not Path(filename).is_absolute())
            or (absolute and not allow_absolute)):
        raise ErroProjeto(f"{campo}: arquivo deve ser um caminho relativo local ao projeto")
    source = (project.parent / filename).resolve()
    if not source.is_file():
        raise ErroProjeto(f"{campo}: arquivo inexistente ou não regular")
    if source == output or output in source.parents:
        raise ErroProjeto("saída sobre/contendo uma entrada")
    return source


def animated_settings(data, project, output):
    export_format = data.get('formato', 'MP4')
    if export_format not in ('MP4', 'AVI'):
        raise ErroProjeto('formato: use MP4 ou AVI')
    alignment = data.get('alinhamento_titulo', 'esquerda')
    if alignment not in ('esquerda', 'centralizado'):
        raise ErroProjeto('alinhamento_titulo: use esquerda ou centralizado')
    family_id(data.get("familia_titulo"), "Título")
    mode = data.get("modo", "legado")
    if mode not in ("legado", "animado"):
        raise ErroProjeto("modo: use legado ou animado")
    if mode == "legado":
        if export_format != 'MP4':
            raise ErroProjeto('formato: AVI exige modo animado')
        return None
    show_footer = data.get("exibir_rodape", True)
    if type(show_footer) is not bool:
        raise ErroProjeto("exibir_rodape: booleano obrigatório (true ou false)")
    duration = data.get("duracao")
    if not isinstance(duration, dict) or duration.get("modo") not in ("manual", "musica"):
        raise ErroProjeto("duracao: informe modo manual ou musica")
    settings = {"modo_duracao": duration["modo"], "musica": None,
                "tamanho_titulo": font_size(data.get('tamanho_titulo'), 'Título'),
                "familia_titulo": family_id(data.get("familia_titulo"), "Título"),
                "formato": export_format,
                "alinhamento_titulo": alignment,
                "exibir_rodape": show_footer}
    if duration["modo"] == "manual":
        settings["segundos"] = numero(duration.get("segundos"), "duracao/segundos", minimo=1/30)
    elif 'segundos_manuais' in duration:
        settings['segundos_manuais'] = numero(duration['segundos_manuais'],
                                             'duracao/segundos_manuais', minimo=1/30)
    music = data.get("musica")
    if music is not None:
        if not isinstance(music, dict):
            raise ErroProjeto("musica: objeto com arquivo local obrigatório")
        source = local_source(music.get("arquivo"), project, output, "musica",
                              allow_absolute=data.get("versao") == 2)
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", source.suffix):
            raise ErroProjeto("musica: extensão não segura")
        settings["musica"] = {"source": source, "arquivo": "musica/trilha" + source.suffix}
    if duration["modo"] == "musica" and not music:
        raise ErroProjeto("duracao: modo musica exige uma trilha")
    visual = data.get("visual", {})
    if not isinstance(visual, dict) or set(visual) - {"fundo", "texto", "destaque"}:
        raise ErroProjeto("visual: use fundo, texto e destaque em #RRGGBB")
    settings["visual"] = {}
    for key, default in (("fundo", "#101f33"), ("texto", "#f3ead6"), ("destaque", "#c7a362")):
        color = visual.get(key, default)
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ErroProjeto("visual: cores devem usar #RRGGBB")
        settings["visual"][key] = color
    return settings


def load_project(project, output, *, with_settings=False):
    project = Path(project).resolve()
    raw_output = Path(output).absolute()
    if os.path.lexists(raw_output):
        raise ErroProjeto("a saída já existe; escolha um arquivo novo")
    output = raw_output.resolve()
    if not output.parent.is_dir():
        raise ErroProjeto("a pasta pai da saída deve existir")
    try:
        data = json.loads(project.read_text(encoding="utf-8"),
                          object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ErroProjeto("JSON inválido; use UTF-8") from exc
    except OSError as exc:
        raise ErroProjeto("não foi possível ler o projeto") from exc
    if not isinstance(data, dict):
        raise ErroProjeto("projeto deve ser um objeto JSON")
    settings = animated_settings(data, project, output)
    title = texto(data.get("titulo"), "titulo", 100)
    scenes = data.get("cenas")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 200:
        raise ErroProjeto("cenas: informe uma lista com 1 a 200 cenas")
    if settings and len(scenes) > 6:
        raise ErroProjeto("modo animado: informe 1 a 6 cenas")
    result = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            raise ErroProjeto(f"cena {index}: objeto obrigatório")
        family_id(scene.get("familia_fonte"), f"Cena {index}")
        name = texto(scene.get("nome"), f"cena {index}/nome", 80)
        filename = scene.get("arquivo")
        source = local_source(filename, project, output, f"cena {index}",
                              allow_absolute=data.get("versao") == 2)
        extension = source.suffix
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", extension):
            raise ErroProjeto(f"cena {index}: extensão não segura para a entrega")
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", name).rstrip(" .")[:64] or "Cena"
        while len(safe_name.encode("utf-8")) > 200:
            safe_name = safe_name[:-1]
        safe_name = safe_name.rstrip(" .") or "Cena"
        width = max(2, len(str(len(scenes))))
        destination = f"{index:0{width}d}-{safe_name}{extension}"
        row = {"source": source, "nome": name, "arquivo": f"cenas/{destination}"}
        if settings:
            row['tamanho_fonte'] = font_size(scene.get('tamanho_fonte'), f'Cena {index}')
            row['familia_fonte'] = family_id(scene.get('familia_fonte'), f'Cena {index}')
            preview = scene.get("previa")
            if not isinstance(preview, dict):
                raise ErroProjeto(f"cena {index}/previa: informe inicio e duracao")
            row["previa"] = {
                "inicio": numero(preview.get("inicio"), "previa/inicio", maximo=1e9),
                "duracao": numero(preview.get("duracao"), "previa/duracao", minimo=1/30),
            }
        result.append(row)
    if project == output or output in project.parents:
        raise ErroProjeto("saída sobre/contendo o projeto")
    if with_settings:
        return title, result, output, settings
    return title, result, output


def export_project(project, output, profile="1080p", font=None, *, cancel=None):
    if profile not in ("1080p", "prova"):
        raise ErroProjeto("perfil inválido; use 1080p ou prova")
    title, scenes, output, settings = load_project(project, output, with_settings=True)
    return export_media(title, scenes, output, settings, profile, font, cancel=cancel)


def export_media(title, scenes, output, settings, profile="1080p", font=None, *, cancel=None, progress=None):
    # Contexto local à thread, restaurado em sucesso/erro/cancelamento; sem globals por job.
    token = cancel if cancel is not None else media_runtime.current_cancel()
    with media_runtime.cancellation_scope(token), media_runtime.progress_scope(progress):
        return _export_media(title, scenes, output, settings, profile, font)


def _export_media(title, scenes, output, settings, profile="1080p", font=None):
    """Lê fontes locais diretamente e publica um único vídeo sem sobrescrever."""
    output = Path(output).absolute()
    if os.path.lexists(output):
        raise ErroProjeto("a saída já existe; escolha um arquivo novo")
    export_format = settings.get('formato', 'MP4') if settings else 'MP4'
    if export_format not in ('MP4', 'AVI'):
        raise ErroProjeto('formato: use MP4 ou AVI')
    extension = '.' + export_format.lower()
    if settings and settings.get('alinhamento_titulo', 'esquerda') not in ('esquerda', 'centralizado'):
        raise ErroProjeto('alinhamento_titulo: use esquerda ou centralizado')
    if settings:
        family_id(settings.get('familia_titulo'), 'Título')
        font_size(settings.get('tamanho_titulo'), 'Título')
        for index, scene in enumerate(scenes, 1):
            family_id(scene.get('familia_fonte'), f'Cena {index}')
            font_size(scene.get('tamanho_fonte'), f'Cena {index}')
    if output.suffix.lower() != extension:
        raise ErroProjeto(f'a saída deve ser um arquivo {extension}')
    if not output.parent.is_dir():
        raise ErroProjeto("a pasta pai da saída deve existir")
    if re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', output.name):
        raise ErroProjeto("nome de arquivo reservado no Windows")
    if profile not in ('1080p', 'prova'):
        raise ErroProjeto("perfil inválido")
    ffmpeg, ffprobe = resolve_tool("ffmpeg"), resolve_tool("ffprobe")
    media_runtime.report_progress('Validando fontes')
    if font is not None:
        font = Path(font).resolve()
        if not font.is_file():
            raise ErroProjeto("fonte inexistente; informe um arquivo TTF/OTF local")
    # Validação por fonte, mesmo quando vários trechos usam o mesmo vídeo.
    inspected, decoded = {}, set()
    for index, scene in enumerate(scenes, 1):
        if scene["source"] not in inspected:
            inspected[scene["source"]] = probe_media(ffprobe, scene["source"])
        details = inspected[scene["source"]]
        videos = [s for s in details.get("streams", []) if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")]
        if not videos:
            raise ErroProjeto(f"cena {index}: entrada sem vídeo")
        video_duration = input_media_duration(ffprobe, scene["source"], videos[0], details)
        if settings:
            preview = scene["previa"]
            if (preview["inicio"] >= video_duration
                    or preview["inicio"] + preview["duracao"] > video_duration + 1e-6):
                raise ErroProjeto(f"cena {index}: trecho de prévia ultrapassa o vídeo; reduza início/duração")
            preview["duracao_fonte"] = video_duration
            preview["duracao_efetiva"] = math.floor(preview["duracao"] * 30 + 1e-7) / 30
        if scene["source"] not in decoded:
            check_decode(ffmpeg, scene["source"], duration=video_duration)
            decoded.add(scene["source"])

    assets = list(scenes)
    if settings and settings["musica"]:
        music = settings["musica"]
        details = probe_media(ffprobe, music["source"])
        audios = [s for s in details.get("streams", []) if s.get("codec_type") == "audio"]
        if not audios:
            raise ErroProjeto("musica: entrada sem áudio")
        duration = input_media_duration(ffprobe, music["source"], audios[0], details)
        if duration < 1/30:
            raise ErroProjeto("musica: duração menor que um frame (1/30s)")
        music["duracao"] = duration
        if settings["modo_duracao"] == "musica":
            settings["segundos"] = duration
        run_media(ffmpeg_base(ffmpeg) + ["-xerror", "-err_detect", "explode",
            "-protocol_whitelist", "file,pipe", "-i", str(music["source"]),
            "-map", "0:a:0", "-vn", "-threads", "1", "-f", "null", "-"], timeout=media_timeout(duration),
            stage=f'Conferindo áudio: {music["source"].name}', progress_duration=duration)

        assets.append(music)
    if settings:
        settings["fade_out_segundos"] = min(.6, settings["segundos"] / 4) if settings["musica"] else 0
    def fingerprint(path):
        info = Path(path).stat()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    before = {asset['source']: fingerprint(asset['source']) for asset in assets}
    if output.resolve() in before:
        raise ErroProjeto("saída sobre uma fonte")
    lock_path = output.parent / f".{output.name}.menu-video.lock"
    try:
        lock = lock_path.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise ErroProjeto("outra exportação reservou esta saída") from exc
    lock_info = os.fstat(lock.fileno())
    lock_identity = lock_info.st_dev, lock_info.st_ino
    try:
        lock.write("Reserva temporária do gerador de menu\n")
        lock.flush()
        with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temp, \
                tempfile.TemporaryDirectory(prefix="menu-video-work-") as scratch:
            delivery, work = Path(temp), Path(scratch)
            selected_font = find_font(font)
            if selected_font:
                shutil.copyfile(selected_font, work / "font.ttf")
            catalog = delivery / ("00-APRESENTACAO" + extension)
            media_runtime.report_progress('Ajustando textos e preparando cenas')
            if settings:
                render_animated(ffmpeg, work, delivery, title, scenes, profile,
                                bool(selected_font), settings, **({'font_override': True} if font is not None else {}))
            else:
                render_catalog(ffmpeg, work, delivery, title, scenes, profile, bool(selected_font))
            if any(fingerprint(path) != stamp for path, stamp in before.items()):
                raise ErroProjeto("entrada mudou durante o render; exportação cancelada")
            check_decode(ffmpeg, catalog, duration=settings["segundos"] if settings else 2 + 3 * len(scenes))
            media = probe_media(ffprobe, catalog)
            container = media.get('format', {}).get('format_name', '')
            if not (container == 'avi' if export_format == 'AVI' else 'mp4' in container.split(',')):
                raise ErroProjeto('contêiner do vídeo fora do formato escolhido')
            video = media["streams"][0]
            width, height = (1920, 1080) if profile == "1080p" else (640, 360)
            has_music = bool(settings and settings["musica"])
            if (len(media["streams"]) != (2 if has_music else 1) or video.get("codec_name") != ("h264" if export_format == "MP4" else "mpeg4")
                    or video.get("pix_fmt") != "yuv420p"
                    or (video.get("width"), video.get("height")) != (width, height)
                    or video.get("r_frame_rate") != "30/1"):
                raise ErroProjeto("menu fora do perfil esperado")
            if has_music:
                audio = media["streams"][1]
                if audio.get("codec_name") != ("aac" if export_format == "MP4" else "mp3") or audio.get("channels") != 2:
                    raise ErroProjeto("áudio fora do perfil estéreo esperado")
            if settings and abs(media_duration(video, 'menu') - settings["segundos"]) > 1/30 + .001:
                raise ErroProjeto("duração do menu fora do perfil esperado")
            media_runtime.report_progress('Publicando vídeo conferido')
            publish_mp4(catalog, output)
    finally:
        try:
            # POSIX: mantém o inode vivo até o unlink. Windows: fecha antes,
            # pois a própria abertura impede substituição enquanto o job roda.
            if os.name == 'nt':
                lock.close()
            try:
                current = lock_path.lstat()
                if (current.st_dev, current.st_ino) == lock_identity:
                    lock_path.unlink()
            except FileNotFoundError:
                pass
        finally:
            lock.close()
    return output


def publish_mp4(catalog, output, *, cancel=None):
    """Commit linearizado com o pedido; rollback verifica a identidade exclusiva."""
    cancel = cancel if cancel is not None else media_runtime.current_cancel()
    if cancel is not None:
        cancel.check()
    def commit(action):
        if cancel is None:
            action()
        else:
            cancel.commit(action)
    try:
        try:
            commit(lambda: os.link(catalog, output))
            return
        except OSError as exc:
            if (exc.errno not in (errno.EOPNOTSUPP, errno.ENOTSUP, errno.ENOSYS, errno.EXDEV)
                    and getattr(exc, 'winerror', None) not in (1, 50)):
                raise
        if cancel is not None:
            cancel.check()
        # xb também deve recusar symlink quebrado no Windows.
        if os.path.lexists(output):
            raise FileExistsError(errno.EEXIST, "a saída já existe", output)
        target = output.open('xb')
        identity, completed, guard = None, False, None
        try:
            with target:
                info = os.fstat(target.fileno())
                identity = info.st_dev, info.st_ino
                # POSIX permite substituir um arquivo aberto. Retém o inode até
                # o rollback para impedir que o substituto reutilize seu número.
                # Windows impede remoção enquanto o handle está aberto.
                if os.name != 'nt':
                    guard = os.dup(target.fileno())
                with catalog.open('rb') as source:
                    if cancel is None:
                        shutil.copyfileobj(source, target)
                    else:
                        while True:
                            cancel.check()
                            chunk = source.read(1024 * 1024)
                            if not chunk:
                                break
                            target.write(chunk)
            def verify_identity():
                current = output.lstat()
                if (current.st_dev, current.st_ino) != identity:
                    raise ErroProjeto("a reserva da saída foi substituída durante a publicação")
            commit(verify_identity)
            completed = True
        finally:
            try:
                if not completed and identity is not None:
                    try:
                        current = output.lstat()
                        if (current.st_dev, current.st_ino) == identity:
                            output.unlink()
                    except FileNotFoundError:
                        pass
            finally:
                if guard is not None:
                    os.close(guard)
    except FileExistsError as exc:
        raise ErroProjeto("a saída já existe; exportação cancelada") from exc


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def media_duration(stream, campo):
    # O decimal do ffprobe pode truncar um frame exato (1600/48000s).
    # Use os ticks racionais quando válidos; mantenha float na API/manifesto.
    try:
        ticks = Fraction(str(stream["duration_ts"]))
        time_base = Fraction(str(stream["time_base"]))
        if ticks.denominator != 1 or ticks <= 0 or time_base <= 0:
            raise ValueError()
        value = float(ticks * time_base)
        if not math.isfinite(value) or value <= 0:
            raise ValueError()
        return value
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        pass
    try:
        if isinstance(stream["duration"], bool):
            raise ValueError()
        value = float(stream["duration"])
        if not math.isfinite(value) or value <= 0:
            raise ValueError()
        return value
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ErroProjeto(f"{campo}: duração do stream indisponível ou inválida") from exc


def input_duration_info(ffprobe, source, stream, details):
    """Duração da faixa de entrada; verificadores de saída usam media_duration."""
    candidates, invalid = [], False
    def positive(value):
        if isinstance(value, bool):
            raise ValueError()
        result = Fraction(str(value))
        if result <= 0 or not math.isfinite(float(result)) or float(result) <= 0:
            raise ValueError()
        return result
    if 'duration_ts' in stream:
        try:
            ticks = positive(stream['duration_ts'])
            actual_base = positive(stream['time_base'])
            if ticks.denominator != 1:
                raise ValueError()
            candidates.append(('duration_ts*time_base', positive(ticks * actual_base)))
        except (KeyError, ValueError, ZeroDivisionError, OverflowError):
            invalid = True
    if 'duration' in stream:
        try:
            candidates.append(('stream.duration', positive(stream['duration'])))
        except (ValueError, ZeroDivisionError, OverflowError):
            invalid = True
    tags = stream.get('tags', {})
    if not isinstance(tags, dict):
        tags, invalid = {}, True
    has_tag = False
    for key, value in tags.items():
        if key.casefold() != 'duration':
            continue
        has_tag = True
        match = re.fullmatch(r'([0-9]{2,}):([0-5][0-9]):([0-5][0-9])\.([0-9]+)', value) if isinstance(value, str) else None
        if match is None:
            invalid = True
            continue
        hours, minutes, seconds, fraction = match.groups()
        try:
            duration = positive(int(hours) * 3600 + int(minutes) * 60 + int(seconds)
                                + Fraction(int(fraction), 10 ** len(fraction)))
            candidates.append(('tags.' + key, duration))
        except (ValueError, ZeroDivisionError, OverflowError):
            invalid = True
    # Só acomoda a impressão decimal (6 casas) do ffprobe. Um tick grande
    # não torna duas durações contraditórias compatíveis.
    tolerance = Fraction(1, 1_000_000)
    if candidates and any(abs(value - candidates[0][1]) > tolerance for _, value in candidates):
        invalid = True
    try:
        start = stream.get('start_time', '0')
        if (isinstance(start, bool) or (has_tag and 'start_time' not in stream)
                or not math.isfinite(float(start))
                or (has_tag and Fraction(str(start)) != 0)):
            invalid = True
    except (TypeError, ValueError, OverflowError, ZeroDivisionError):
        invalid = True
    if candidates and not invalid:
        method, seconds = candidates[0]
        try:
            total = positive(details.get('format', {}).get('duration'))
            if seconds > total + tolerance:
                invalid = True
        except (ValueError, ZeroDivisionError, OverflowError):
            pass
        if not invalid:
            return {'seconds': float(seconds), 'method': method, 'stream_index': stream.get('index')}
    return measure_input_duration(ffprobe, source, stream)


def measure_input_duration(ffprobe, source, stream, *, timeout=120):
    """Intervalo de apresentação dos frames decodificados, incluindo a cauda.

    B-frames usam PTS de apresentação; VFR usa duração de cada frame, nunca
    fps nominal. Normaliza o primeiro PTS (inclusive offsets negativos).
    Áudio sem duração de frame usa nb_samples/sample_rate da própria faixa.
    Sem PTS ou duração explícita da cauda, falha em vez de extrapolar.
    """
    index = stream.get('index')
    if (type(index) is not int or index < 0 or stream.get('codec_type') not in ('video', 'audio')
            or stream.get('disposition', {}).get('attached_pic')):
        raise ErroProjeto('índice/tipo da faixa inválido para medir duração')
    first, last, count = None, None, 0
    def rational(value):
        if isinstance(value, bool):
            raise ValueError()
        result = Fraction(str(value))
        if not math.isfinite(float(result)):
            raise ValueError()
        return result
    try:
        time_base = rational(stream.get('time_base'))
        if time_base <= 0:
            time_base = None
    except (ValueError, ZeroDivisionError, OverflowError):
        time_base = None
    def timestamp(fields, ticks_key, decimal_key):
        if time_base is not None and fields.get(ticks_key) not in (None, 'N/A'):
            ticks = rational(fields[ticks_key])
            if ticks.denominator != 1:
                raise ValueError('timestamp sem ticks inteiros')
            return ticks * time_base
        return rational(fields.get(decimal_key))
    def consume(line):
        nonlocal first, last, count
        fields = dict(part.split('=', 1) for part in line.split('|') if '=' in part)
        if not fields or not any(k in fields for k in (
                'pts', 'pts_time', 'best_effort_timestamp', 'best_effort_timestamp_time',
                'duration', 'duration_time', 'pkt_duration', 'pkt_duration_time', 'nb_samples')):
            return  # side data não é outro frame
        pts = timestamp(fields, 'best_effort_timestamp', 'best_effort_timestamp_time') if fields.get('best_effort_timestamp_time') not in (None, 'N/A') else timestamp(fields, 'pts', 'pts_time')
        if stream['codec_type'] == 'audio' and fields.get('nb_samples') not in (None, 'N/A'):
            samples, rate = rational(fields.get('nb_samples')), rational(stream.get('sample_rate'))
            if samples.denominator != 1 or rate.denominator != 1 or samples <= 0 or rate <= 0:
                raise ValueError()
            duration = samples / rate
        else:
            duration = timestamp(fields, 'duration' if 'duration' in fields else 'pkt_duration',
                                 'duration_time' if 'duration_time' in fields else 'pkt_duration_time')
        if duration <= 0:
            raise ValueError('cauda sem duração')
        first = pts if first is None else min(first, pts)
        last = pts + duration if last is None else max(last, pts + duration)
        count += 1
    media_runtime.report_progress('Analisando duração da faixa')
    try:
        diagnostic = media_runtime.stream_lines([ffprobe, '-v', 'error', '-threads', '1',
            '-protocol_whitelist', 'file,pipe', '-select_streams', str(index),
            '-show_frames', '-show_entries',
            'frame=best_effort_timestamp,pts,best_effort_timestamp_time,pts_time,duration,pkt_duration,duration_time,pkt_duration_time,nb_samples',
            '-of', 'compact=p=0:nk=0', str(source)], consume, timeout=timeout)
        # ffprobe pode sair 0 após erros de decode e fornecer só parte dos
        # frames. Com loglevel error, stderr impede aceitar essa duração parcial.
        if diagnostic or not count or last <= first:
            raise ValueError()
        seconds = float(last - first)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError()
        return {'seconds': seconds, 'method': 'timestamps.frames', 'stream_index': index,
                'first_timestamp': str(first), 'end_timestamp': str(last), 'frames': count,
                'tail_rule': 'rational frame ticks/time_base; audio decoded nb_samples/sample_rate'}
    except subprocess.TimeoutExpired as exc:
        raise ErroProjeto('Tempo limite ao analisar duração da faixa; tente novamente.') from exc
    except (ValueError, UnicodeError, OverflowError, ZeroDivisionError, subprocess.CalledProcessError) as exc:
        raise ErroProjeto(f'{Path(source).name}: duração da faixa indeterminada; verifique a mídia.') from exc


def input_media_duration(ffprobe, source, stream, details):
    return input_duration_info(ffprobe, source, stream, details)['seconds']


def run_media(arguments, *, cwd=None, timeout=120, cancel=None, stage=None, progress_duration=None):
    cancel = cancel if cancel is not None else media_runtime.current_cancel()
    if cancel is not None:
        cancel.check()
    callback = media_runtime.current_progress()
    measured = callback is not None and stage is not None and progress_duration is not None
    try:
        # Arquivo privado permite ler progresso durante communicate também no Windows,
        # cujas threads de pipe só entregam stdout completo no fim do processo.
        with (tempfile.TemporaryDirectory(prefix='menu-video-progress-') if measured else nullcontext()) as temp:
            on_progress, progress_path = None, None
            if stage is not None:
                media_runtime.report_progress(stage)
            if measured:
                progress_path = Path(temp) / 'progress.txt'
                arguments = list(arguments)
                arguments[1:1] = ['-progress', str(progress_path), '-nostats']
                on_progress = lambda seconds: callback(media_runtime.MediaProgress(stage, seconds, progress_duration))
                if cancel is None:
                    cancel = media_runtime.CancellationToken()
            if cancel is not None:
                return media_runtime.run_cancellable(media_runtime.media_arguments(arguments),
                    cwd=cwd, timeout=timeout, cancel=cancel, on_progress=on_progress, progress_path=progress_path)
            return subprocess.run(media_runtime.media_arguments(arguments), cwd=cwd,
                                  capture_output=True, check=True, timeout=timeout,
                                  stdin=subprocess.DEVNULL, **media_runtime.no_console_options())
    except subprocess.TimeoutExpired as exc:
        raise ErroProjeto(f"tempo limite de {timeout}s excedido no processamento de mídia") from exc
    except subprocess.CalledProcessError as exc:
        # Diagnóstico limitado; não é gravado no manifesto/entrega.
        diagnostic = exc.stderr.decode("utf-8", errors="replace")[-1800:]
        raise ErroProjeto(f"mídia inválida ou falha no FFmpeg/ffprobe: {diagnostic}") from exc


def probe_media(ffprobe, source, *, count_frames=False):
    media_runtime.report_progress(f'Conferindo mídia: {Path(source).name}')
    timeout = 30
    if count_frames:
        metadata = probe_media(ffprobe, source)
        durations = [input_media_duration(ffprobe, source, stream, metadata) for stream in metadata.get('streams', [])
                     if stream.get('codec_type') in ('video', 'audio')
                     and not stream.get('disposition', {}).get('attached_pic')]
        timeout = media_timeout(max(durations, default=0))
    result = run_media([ffprobe, "-v", "error", "-threads", "1", "-protocol_whitelist", "file,pipe",
                        *(["-count_frames"] if count_frames else []),
                        "-show_streams", "-show_format", "-of", "json", str(source)], timeout=timeout)
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError) as exc:
        raise ErroProjeto("resposta inválida do ffprobe") from exc


def ffmpeg_base(ffmpeg):
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
            "-threads", "1", "-filter_threads", "1", "-filter_complex_threads", "1"]


def media_timeout(duration):
    """Orçamento proporcional, sem limite superior oculto para mídia longa."""
    return max(120, duration * 8 + 60)


def check_decode(ffmpeg, source, *, duration=None):
    if duration is None:
        details = probe_media(resolve_tool("ffprobe"), source)
        durations = [input_media_duration(resolve_tool("ffprobe"), source, s, details) for s in details.get("streams", [])
                     if s.get("codec_type") in ("video", "audio")
                     and not s.get("disposition", {}).get("attached_pic")]
        duration = max(durations, default=0)
    # V exclui attached_pic, como a pré-validação feita pelo ffprobe.
    run_media(ffmpeg_base(ffmpeg) + ["-xerror", "-err_detect", "explode",
              "-protocol_whitelist", "file,pipe", "-i", str(source),
              "-map", "0:V:0", "-map", "0:a?", "-threads", "1", "-f", "null", "-"], timeout=media_timeout(duration),
              stage=f'Conferindo decodificação: {Path(source).name}', progress_duration=duration)


def find_font(explicit):
    if explicit:
        return explicit
    candidates = []
    if os.environ.get("WINDIR"):
        candidates.append(Path(os.environ["WINDIR"]) / "Fonts" / "arial.ttf")
    candidates.extend([
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ])
    return next((p for p in candidates if p.is_file()), None)


def animated_layout(profile):
    """Coordenadas 1080p escaladas; seis posições fixas, em ordem por linha."""
    factor = 1 if profile == "1080p" else 1/3
    def even(value):
        return round(value * factor / 2) * 2
    return [{"x": even(88 + (i % 2) * 936), "y": even(230 + (i // 2) * 266),
             "w": even(808), "h": even(248), "vw": even(400), "vh": even(224),
             "pad": even(12), "label": even(436)} for i in range(6)]


def measure_text_pixels(ffmpeg, work, font, value, size, spacing):
    """Mede com o próprio drawtext/FreeType usado na saída, inclusive Unicode."""
    (work / 'measure.txt').write_text(value, encoding='utf-8')
    result = run_media(ffmpeg_base(ffmpeg) + [
        '-loglevel', 'info', '-f', 'lavfi', '-i', 'color=s=16x16:r=1',
        '-vf', f'drawtext={font}:textfile=measure.txt:expansion=none:fontsize={size}:'
               f'line_spacing={spacing}:x=print(text_w):y=print(text_h)',
        '-frames:v', '1', '-f', 'null', '-'], cwd=work)
    dimensions = re.findall(rb'\[Eval @ [^\]]+\]\s+([0-9]+(?:\.[0-9]+)?)', result.stderr)
    if len(dimensions) < 2:
        raise ErroProjeto('FFmpeg não informou as medidas do texto; entrega cancelada.')
    return float(dimensions[0]), float(dimensions[1])


def fit_text_pixels(value, width, height, size, measure, *, autofit=True):
    """Quebra somente em espaços; reduz explicitamente a fonte se não couber.

    Uma palavra excepcional permanece inteira. A altura medida limita também
    frases multilinha, sem truncar texto nem invadir número/cena vizinha.
    """
    words = value.split()
    for candidate_size in (range(size, 0, -1) if autofit else (size,)):
        lines, line = [], ''
        for word in words:
            candidate = f'{line} {word}' if line else word
            if line and measure(candidate, candidate_size)[0] > width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
        wrapped = '\n'.join(lines)
        measured_width, measured_height = measure(wrapped, candidate_size)
        if measured_width <= width and measured_height <= height:
            return wrapped, candidate_size
    if not autofit:
        raise ErroProjeto(f'O texto não cabe na área disponível com {size} px; '
                          'reduza o tamanho escolhido ou edite o texto. Nenhuma fonte foi reduzida.')
    raise ErroProjeto('O texto não cabe na área disponível mesmo com ajuste de fonte.')


def render_animated(ffmpeg, work, delivery, title, scenes, profile, has_font, settings, *, font_override=False):
    title_size = font_size(settings.get('tamanho_titulo'), 'Título')
    scene_sizes = [font_size(scene.get('tamanho_fonte'), f'Cena {i}')
                   for i, scene in enumerate(scenes, 1)]
    width, height = (1920, 1080) if profile == "1080p" else (640, 360)
    factor = height / 1080
    font = "fontfile=font.ttf" if has_font else "font=sans-serif"
    colors = {k: '0x' + v[1:] for k, v in settings["visual"].items()}
    layout = animated_layout(profile)
    inputs = ["-f", "lavfi", "-i", f"color=c={colors['fundo']}:s={width}x{height}:r=30:d={settings['segundos']}"]
    filters = []

    # Draw each line separately: FreeType's multiline text_h can add a large
    # implicit baseline gap. Measure the exact glyphs and bound the whole block.
    spacing = max(1, round(2*factor))
    measurements = {}

    roles = {}

    def single_measure(value, size, selected_font):
        key = selected_font, value, size
        if key not in measurements:
            measurements[key] = measure_text_pixels(ffmpeg, work, selected_font, value, size, 0)
        return measurements[key]

    def block(value, size, selected_font):
        lines = value.split('\n')
        dimensions = [single_measure(line, size, selected_font) for line in lines]
        advance = max([size] + [h for _, h in dimensions]) + spacing
        bounds = (max(w for w, _ in dimensions),
                  max(index * advance + h for index, (_, h) in enumerate(dimensions)))
        return lines, advance, bounds

    def text(filename, value, available_width, available_height, x, y, size, color, *, centered=False, vertical_center=None, manual=False, context='Texto', identity=None):
        identity = family_id(identity, context)
        selected_font = font
        path = work / 'font.ttf' if has_font else None
        if identity is not None and not font_override:
            path = work / f'family-{identity}.ttf'
            if not path.exists():
                shutil.copyfile(font_path(identity), path)
            selected_font = f'fontfile={path.name}'
        def measure(value, pixels):
            return block(value, pixels, selected_font)[2]
        media_runtime.report_progress('Ajustando textos')
        try:
            value, actual_size = fit_text_pixels(value, available_width, available_height,
                                                max(1, round(size*factor)), measure, autofit=not manual)
        except ErroProjeto as exc:
            raise ErroProjeto(f'{context}: {exc} Tamanho de referência 1080p: {size} px.') from exc
        (work / filename).write_text(value, encoding="utf-8")
        lines, advance, (block_width, block_height) = block(value, actual_size, selected_font)
        roles[filename] = {"id": "explicit" if font_override else identity, "file": path.name if path else None,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None, "text": value,
                           "size": actual_size, "measured_width": block_width, "measured_height": block_height}
        if vertical_center is not None:
            y = vertical_center - block_height / 2
        filters = []
        for index, line in enumerate(lines):
            linefile = filename if len(lines) == 1 else f"{Path(filename).stem}-line{index+1}.txt"
            if linefile != filename:
                (work / linefile).write_text(line, encoding='utf-8')
            filters.append(f"drawtext={selected_font}:textfile={linefile}:expansion=none:"
                           f"fontsize={actual_size}:fontcolor={color}:"
                           f"line_spacing=0:x={'(w-text_w)/2' if centered else x}:y={round(y + index*advance)}")
        return ','.join(filters)

    base = text("titre.txt", title, round(1744*factor), round(122*factor),
                round(88*factor), round(52*factor), title_size if title_size is not None else 44, colors['texto'],
                manual=title_size is not None, context='Título', identity=settings.get('familia_titulo'),
                centered=settings.get('alinhamento_titulo', 'esquerda') == 'centralizado',
                vertical_center=round(198*factor)/2)
    base += f",drawbox=x={round(88*factor)}:y={round(198*factor)}:w={round(1744*factor)}:h={max(1,round(2*factor))}:color={colors['destaque']}:t=fill"
    filters.append(f"[0:v]{base}[base0]")
    # Check every text before preparing any video segment. Manual sizes never autofit.
    labels = []
    for i, (scene, cell, selected_size) in enumerate(zip(scenes, layout, scene_sizes), 1):
        x, y, w, h, pad = [cell[k] for k in ("x", "y", "w", "h", "pad")]
        gap = cell['label'] - (pad + cell['vw'])
        label_width = w - cell['label'] - gap
        name = text(f"label-{i}.txt", scene['nome'], label_width, h - round(66*factor) - pad,
                    x + cell['label'], y + round(66*factor),
                    selected_size if selected_size is not None else 24, colors['texto'],
                    manual=selected_size is not None, context=f'Cena {i} ({scene["nome"]})', identity=scene.get('familia_fonte'))
        number = text(f"number-{i}.txt", f"{i:02d}", label_width, round(38*factor),
                      x + cell['label'], y + round(18*factor), 32, colors['destaque'])
        labels.append((name, number))
    for i, (scene, cell) in enumerate(zip(scenes, layout), 1):
        preview = scene["previa"]
        segment = f"trecho-{i:02d}.mp4"
        frames = round(preview["duracao_efetiva"] * 30)
        start, end = preview["inicio"], preview["inicio"] + preview["duracao_efetiva"]
        # Corte feito uma única vez; stream_loop repete este arquivo mudo, nunca o original.
        # Normaliza a origem antes do trim; mantém os timestamps relativos ao início pedido.
        # eof_action=pass conserva a exibição final fracionária, sem buscar fora do trecho.
        run_media(ffmpeg_base(ffmpeg) + [
            "-protocol_whitelist", "file,pipe", "-i", str(scene["source"]),
            "-map", "0:V:0", "-an", "-map_metadata", "-1",
            "-vf", f"setpts=PTS-STARTPTS,trim=start={start}:end={end},setpts=PTS-{start}/TB,fps=30:start_time=0:eof_action=pass,trim=end_frame={frames},scale={cell['vw']}:{cell['vh']}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1,setsar=1,pad={cell['vw']}:{cell['vh']}:(ow-iw)/2:(oh-ih)/2:color={colors['fundo']}",
            "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-threads", "2",
            "-pix_fmt", "yuv420p", segment], cwd=work, timeout=media_timeout(end),
            stage=f'Preparando cena {i}/{len(scenes)}', progress_duration=frames/30)
        details = probe_media(resolve_tool("ffprobe"), work / segment, count_frames=True)
        try:
            video = details["streams"][0]
            valid = (int(video["nb_read_frames"]) == frames
                     and abs(media_duration(video, "prévia") - frames / 30) < 1e-6)
        except (KeyError, IndexError, TypeError, ValueError, ErroProjeto):
            valid = False
        if not valid:
            raise ErroProjeto(f"cena {i}: segmento de prévia não cumpre frames/duração; entrega cancelada")
        inputs += ["-stream_loop", "-1", "-threads", "1", "-protocol_whitelist", "file,pipe", "-i", segment]
        x, y, w, h, pad = [cell[k] for k in ("x", "y", "w", "h", "pad")]
        name, number = labels[i-1]
        filters.append(f"[base{i-1}]drawbox=x={x}:y={y}:w={w}:h={h}:color=0x192c42:t=fill,{name},{number}[card{i}]")
        filters.append(f"[card{i}][{i}:v]overlay=x={x+pad}:y={y+pad}:shortest=1[base{i}]")
    (work / "font-roles.json").write_text(json.dumps(roles, ensure_ascii=False, indent=2), encoding="utf-8")
    audio_options = ["-an"]
    if settings["musica"]:
        inputs += ["-threads", "1", "-protocol_whitelist", "file,pipe", "-i",
                   str(settings["musica"]["source"])]
        duration, fade = settings["segundos"], settings["fade_out_segundos"]
        audio_end = min(duration, settings["musica"]["duracao"])
        fade = min(fade, audio_end / 4)
        filters.append(f"[{len(scenes)+1}:a:0]asetpts=PTS-STARTPTS,"
                       f"aformat=sample_rates=48000:channel_layouts=stereo,"
                       f"afade=t=out:st={audio_end-fade}:d={fade},"
                       f"apad,atrim=duration={duration}[music]")
        audio_options = ["-map", "[music]", "-c:a", ("aac" if settings.get("formato", "MP4") == "MP4" else "libmp3lame"), "-b:a", "192k", "-ac", "2", "-ar", "48000"]
    run_media(ffmpeg_base(ffmpeg) + inputs + ["-filter_complex", ";".join(filters),
        "-map", f"[base{len(scenes)}]"] + audio_options + ["-t", str(settings["segundos"]),
        "-r", "30", "-map_metadata", "-1"] + (
        ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart"]
        if settings.get("formato", "MP4") == "MP4" else
        ["-c:v", "mpeg4", "-q:v", "3", "-vtag", "XVID"]) + [
        "-threads", "2", "-pix_fmt", "yuv420p",
        str(delivery / ("00-APRESENTACAO." + settings.get("formato", "MP4").lower()))],
        cwd=work, timeout=media_timeout(settings["segundos"]),
        stage='Compondo vídeo', progress_duration=settings['segundos'])


def render_catalog(ffmpeg, work, delivery, title, scenes, profile, has_font):
    width, height = (1920, 1080) if profile == "1080p" else (640, 360)
    size = round(height / 22)
    font = "fontfile=font.ttf" if has_font else "font=sans-serif"

    def draw(filename, y, fontsize=size):
        return (f"drawtext={font}:textfile={filename}:expansion=none:fontsize={fontsize}:"
                f"fontcolor=white:line_spacing={max(4, size // 4)}:x=(w-tw)/2:y={y}")

    def write_text(filename, value, columns=32):
        (work / filename).write_text(textwrap.fill(value, columns), encoding="utf-8")

    def encode(inputs, filters, duration, filename):
        run_media(ffmpeg_base(ffmpeg) + inputs + [
            "-vf", filters, "-t", str(duration), "-r", "30", "-an",
            "-map_metadata", "-1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-threads", "2",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", filename], cwd=work)

    write_text("titulo.txt", title)
    write_text("instrucao.txt", "Menu não interativo", 48)
    encode(["-f", "lavfi", "-i", f"color=c=0x152539:s={width}x{height}:r=30"],
           draw("titulo.txt", "(h-th)/2", round(size * 1.3)) + "," +
           draw("instrucao.txt", round(height * .86), round(size * .64)), 2, "parte-000.mp4")
    parts = ["parte-000.mp4"]
    for index, scene in enumerate(scenes, 1):
        # A miniatura vem diretamente do moving-video da fonte local.
        source = scene["source"]
        thumbnail = f"miniatura-{index:03d}.png"
        run_media(ffmpeg_base(ffmpeg) + ["-protocol_whitelist", "file,pipe", "-i", str(source),
                  "-map", "0:V:0", "-map_metadata", "-1", "-frames:v", "1",
                  "-an", "-threads", "1", thumbnail], cwd=work)
        namefile = f"nome-{index:03d}.txt"
        write_text(namefile, f"{index:02d}. {scene['nome']}", 38)
        thumb_w, thumb_h = round(width * .86) // 2 * 2, round(height * .66) // 2 * 2
        filters = (f"scale={thumb_w}:{thumb_h}:force_original_aspect_ratio=decrease,setsar=1,"
                   f"pad={width}:{height}:(ow-iw)/2:{round(height * .04)}:color=0x152539,"
                   + draw(namefile, round(height * .76)))
        part = f"parte-{index:03d}.mp4"
        encode(["-loop", "1", "-framerate", "30", "-i", thumbnail], filters, 3, part)
        parts.append(part)
    (work / "partes.txt").write_text(
        "".join(f"file '{part}'\n" for part in parts), encoding="utf-8")
    run_media(ffmpeg_base(ffmpeg) + ["-f", "concat", "-safe", "1", "-i", "partes.txt",
              "-map", "0:v:0", "-map_metadata", "-1", "-c", "copy", "-an", "-movflags", "+faststart",
              str(delivery / "00-APRESENTACAO.mp4")], cwd=work)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gerador de menus em vídeo.")
    parser.add_argument("projeto", type=Path)
    parser.add_argument("--saida", required=True, type=Path, help="arquivo .mp4 novo; pasta pai existente")
    parser.add_argument("--perfil", choices=("1080p", "prova"), default="1080p")
    parser.add_argument("--fonte", "--font", dest="fonte", type=Path, help="fonte TTF/OTF local; prevalece sobre famílias do projeto em todos os textos")
    args = parser.parse_args(argv)
    try:
        export_project(args.projeto, args.saida, args.perfil, args.fonte)
    except (ErroProjeto, OSError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    print("Menu MP4 concluído e verificado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
