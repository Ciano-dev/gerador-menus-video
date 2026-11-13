"""Gera somente mídias sintéticas locais, sem rede ou assets externos."""

import argparse
import json
import subprocess
from pathlib import Path
from media_runtime import resolve_tool, no_console_options


def generate(destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("a pasta de fixtures deve ser nova")
    executable = resolve_tool("ffmpeg")
    destination.mkdir()
    common = [executable, "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
              "-filter_threads", "1", "-filter_complex_threads", "1", "-threads", "1"]
    commands = [
        ["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
         "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(destination / "cores-com-audio.mp4")],
        ["-f", "lavfi", "-i", "color=c=0x993344:size=240x320:rate=30:duration=1",
         "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-an",
         str(destination / "retrato-mudo.mov")],
        ["-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=0.3",
         "-c:a", "aac", str(destination / "som-sem-video.m4a")],
    ]
    for command in commands:
        subprocess.run(common + command, check=True, timeout=40, capture_output=True,
                       stdin=subprocess.DEVNULL, **no_console_options())
    (destination / "projeto.json").write_text(json.dumps({
        "titulo": "Encontro de João e Lívia — demonstração sintética",
        "cenas": [
            {"arquivo": "cores-com-audio.mp4", "nome": "Cerimônia: o sim de João e Lívia"},
            {"arquivo": "retrato-mudo.mov", "nome": "Celebração — família e amigos"},
        ],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destino", type=Path)
    args = parser.parse_args()
    generate(args.destino)
    print("Fixtures sintéticas geradas.")


if __name__ == "__main__":
    main()
