"""Auditoria independente da demo: ffprobe, decode, SHA256 e quadros."""

import argparse
import hashlib
import json
import struct
import subprocess
from pathlib import Path
from menu_video import media_duration, input_media_duration, media_timeout, load_project
from media_runtime import resolve_tool, media_arguments, no_console_options


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args, *, timeout=120):
    return subprocess.run(media_arguments(args), check=True, capture_output=True,
                          timeout=timeout, stdin=subprocess.DEVNULL, **no_console_options())


def inspect(path):
    info = json.loads(run([resolve_tool("ffprobe"), "-v", "error", "-threads", "1",
                          "-protocol_whitelist", "file,pipe", "-show_streams", "-show_format", "-of", "json", str(path)]).stdout)
    duration = max((input_media_duration(resolve_tool("ffprobe"), path, stream, info) for stream in info["streams"]
                    if stream.get("codec_type") in ("video", "audio")
                    and not stream.get("disposition", {}).get("attached_pic")), default=0)
    run([resolve_tool("ffmpeg"), "-v", "error", "-xerror", "-nostdin", "-threads", "1",
         "-filter_threads", "1", "-protocol_whitelist", "file,pipe", "-i", str(path), "-map", "0:V:0?", "-map", "0:a?",
         "-threads", "1", "-f", "null", "-"], timeout=media_timeout(duration))
    info["format"]["filename"] = path.name
    return info


def mp4_boxes(path):
    boxes = []
    with path.open("rb") as stream:
        while header := stream.read(8):
            size, kind = struct.unpack(">I4s", header)
            header_size = 8
            if size == 1:
                size = struct.unpack(">Q", stream.read(8))[0]
                header_size = 16
            boxes.append(kind.decode("ascii"))
            if size == 0:
                break
            if size < header_size:
                raise ValueError("box MP4 inválido")
            stream.seek(size - header_size, 1)
    return boxes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projeto", type=Path)
    parser.add_argument("saida", type=Path)
    parser.add_argument("--evidencias", type=Path, default=Path("evidencias"))
    parser.add_argument("--registrar-antes", action="store_true")
    args = parser.parse_args()
    args.evidencias.mkdir(exist_ok=True)
    title, scenes, _, settings = load_project(args.projeto, args.evidencias / 'nao-publicar.mp4', with_settings=True)
    sources = list(dict.fromkeys(scene['source'] for scene in scenes))
    if settings and settings['musica']:
        sources.append(settings['musica']['source'])
    snapshot_path = args.evidencias / "demo-fontes-antes.json"
    if args.registrar_antes:
        snapshot_path.write_text(json.dumps([
            {"arquivo": p.name, "sha256": digest(p)} for p in sources
        ], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("SHA256 das fontes registrado antes da exportação.")
        return
    before = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert len(before) == len(sources), "snapshot não corresponde às fontes"
    source_infos = {}
    report = {"fontes": [], "decodificacao_completa": "sem erros"}
    for source, expected in zip(sources, before):
        current = digest(source)
        assert current == expected['sha256'], "fonte alterada desde o registro anterior"
        source_infos[source] = inspect(source)
        report['fontes'].append({'arquivo': source.name, 'sha256': current,
                                 'bytes': source.stat().st_size, 'ffprobe': source_infos[source]})
    catalog = args.saida
    assert catalog.is_file() and catalog.suffix.lower() == '.mp4'
    info = inspect(catalog)
    stream = info['streams'][0]
    assert len(info['streams']) == (2 if settings and settings['musica'] else 1)
    assert (stream['codec_name'], stream['pix_fmt'], stream['r_frame_rate']) == ('h264', 'yuv420p', '30/1')
    assert (stream['width'], stream['height']) == (1920, 1080)
    duration = (input_media_duration(resolve_tool('ffprobe'), settings['musica']['source'],
                   next(stream for stream in source_infos[settings['musica']['source']]['streams']
                        if stream.get('codec_type') == 'audio'), source_infos[settings['musica']['source']])
                if settings and settings['modo_duracao'] == 'musica'
                else settings['segundos'] if settings else 2 + 3 * len(scenes))
    assert abs(media_duration(stream, 'menu') - duration) <= 1/30 + .001
    if settings and settings['musica']:
        audio = info['streams'][1]
        assert (audio['codec_name'], audio['channels']) == ('aac', 2)
    boxes = mp4_boxes(catalog)
    assert boxes.index("moov") < boxes.index("mdat"), "faststart ausente"
    report["catalogo"] = {"sha256": digest(catalog), "boxes_mp4": boxes, "ffprobe": info}
    for label, time in [("menu", min(duration / 2, .5))]:
        frame = (args.evidencias / f"demo-{label}.png").resolve()
        run([resolve_tool("ffmpeg"), "-v", "error", "-nostdin", "-n", "-threads", "1",
             "-filter_threads", "1", "-ss", str(time), "-protocol_whitelist", "file,pipe", "-i", str(catalog.resolve()),
             "-frames:v", "1", "-threads", "1", str(frame)])
    (args.evidencias / "demo-verificacao.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Menu MP4 1080p verificado: H.264/yuv420p/30fps, duração, áudio, faststart; "
          "fontes com SHA256 antes/depois idêntico; decodificação sem erros.")


if __name__ == "__main__":
    main()
