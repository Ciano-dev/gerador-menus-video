"""Regressões com FFmpeg real: capa anterior ao primeiro vídeo da filmagem."""

import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import menu_anisio as motor
import verificar_demo
from test_real import decode


def create_media(base):
    """MP4 válido com udta/covr antes de trak, sem mudar offsets ou payloads."""
    base = Path(base)
    base.mkdir()
    common = motor.ffmpeg_base(shutil.which("ffmpeg"))
    commands = [
        common + ["-f", "lavfi", "-i", "color=red:size=80x60",
                  "-frames:v", "1", "-threads", "1", str(base / "capa.png")],
        common + ["-f", "lavfi", "-i", "color=blue:size=160x90:rate=5:duration=1",
                  "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p",
                  str(base / "filme.mp4")],
        common + ["-i", str(base / "capa.png"), "-i", str(base / "filme.mp4"),
                  "-map", "0:v:0", "-map", "1:v:0", "-c", "copy",
                  "-disposition:v:0", "attached_pic", str(base / "muxado.mp4")],
    ]
    for command in commands:
        subprocess.run(command, check=True, capture_output=True, timeout=30)
    (base / "comandos.json").write_text(json.dumps(commands, indent=2) + "\n")

    def boxes(content):
        offset = 0
        while offset < len(content):
            size, kind = struct.unpack_from(">I4s", content, offset)
            if size < 8 or offset + size > len(content):
                raise ValueError("box inesperado na fixture sintética")
            yield kind, content[offset:offset + size]
            offset += size

    content = b""
    for kind, box in boxes((base / "muxado.mp4").read_bytes()):
        if kind == b"moov":
            children = sorted(boxes(box[8:]), key=lambda item: item[0] != b"udta")
            box = box[:8] + b"".join(child for _, child in children)
        content += box
    source = base / "capa-antes-filme.mp4"
    source.write_bytes(content)
    # Somente o payload mdat da filmagem é corrompido; capa/índice ficam íntegros.
    broken = base / "filme-corrompido-capa-integra.mp4"
    broken.write_bytes(b"".join(box[:8] + bytes(len(box) - 8) if kind == b"mdat"
                                else box for kind, box in boxes(content)))
    return source, broken


class StreamSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="anisio-streams-", dir=None)
        cls.addClassCleanup(cls.temp.cleanup)
        cls.base = Path(cls.temp.name)
        cls.source, cls.broken = create_media(cls.base / "fontes")
        streams = motor.probe_media(shutil.which("ffprobe"), cls.source)["streams"]
        assert [(s["codec_type"], s["disposition"]["attached_pic"]) for s in streams] == [
            ("video", 1), ("video", 0)], streams

    def test_miniatura_real_usa_filmagem_em_vez_de_capa(self):
        work = self.base / "trabalho"
        delivery = self.base / "entrega-miniatura"
        work.mkdir()
        delivery.mkdir()
        motor.render_catalog(shutil.which("ffmpeg"), work, delivery, "Streams",
                             [{"source": self.source, "nome": "Filmagem azul"}], "prova", False)
        thumbnail = work / "miniatura-001.png"
        stream = motor.probe_media(shutil.which("ffprobe"), thumbnail)["streams"][0]
        self.assertEqual((stream["width"], stream["height"]), (160, 90))
        rgb = subprocess.run(motor.ffmpeg_base(shutil.which("ffmpeg")) + [
            "-i", str(thumbnail), "-frames:v", "1", "-pix_fmt", "rgb24",
            "-threads", "1", "-f", "rawvideo", "-"],
            check=True, capture_output=True, timeout=30).stdout
        self.assertGreater(rgb[2], 240, "a miniatura deve ser azul como a filmagem")
        self.assertLess(rgb[0], 10)

    def test_prevalidacao_recusa_filmagem_corrompida_com_capa_integra(self):
        project = self.broken.parent / "projeto.json"
        project.write_text(json.dumps({"titulo": "Streams", "cenas": [
            {"arquivo": self.broken.name, "nome": "Filmagem corrompida"}]}))
        output = self.base / "entrega-corrompida.mp4"
        with self.assertRaisesRegex(motor.ErroProjeto, "mídia inválida"):
            motor.export_project(project, output, "prova")
        self.assertFalse(output.exists())
        self.assertFalse(any(self.base.glob(".entrega-corrompida*")))

    def test_helper_dos_testes_recusa_filmagem_corrompida(self):
        result = decode(self.broken)
        self.assertNotEqual(result.returncode, 0, "o helper validou somente a capa íntegra")

    def test_verificador_demo_recusa_filmagem_corrompida(self):
        with self.assertRaises(subprocess.CalledProcessError):
            verificar_demo.inspect(self.broken)


if __name__ == "__main__":
    unittest.main()
