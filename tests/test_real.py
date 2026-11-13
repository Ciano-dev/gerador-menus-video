import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from gerar_fixtures import generate


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def probe(path):
    result = subprocess.run([
        shutil.which("ffprobe"), "-v", "error", "-threads", "1", "-show_streams",
        "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)


def decode(path):
    return subprocess.run([
        shutil.which("ffmpeg"), "-v", "error", "-xerror", "-nostdin",
        "-threads", "1", "-filter_threads", "1", "-i", str(path),
        "-map", "0:V:0", "-map", "0:a?", "-threads", "1", "-f", "null", "-"],
        capture_output=True, text=True, timeout=45)


class RealExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="menu-video-real-", dir=None)
        cls.base = Path(cls.temp.name)
        cls.fixtures = generate(cls.base / "fontes")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_exportacao_real_preserva_ordem_audio_bytes_e_texto_literal(self):
        title = 'João & Lívia: "Sim" 100% %{eif:1:d} [x];'
        name = 'Cerimônia "Sim": 100% [a];\\ Festa/CON?*'
        project = self.fixtures / "especial.json"
        sources = [self.fixtures / "retrato-mudo.mov", self.fixtures / "cores-com-audio.mp4"]
        project.write_text(json.dumps({"titulo": title, "cenas": [
            {"arquivo": p.name, "nome": name} for p in sources
        ]}, ensure_ascii=False), encoding="utf-8")
        import menu_video as motor
        loaded_title, rows, _ = motor.load_project(project, self.base / "nao-exportar.mp4")
        self.assertEqual(loaded_title, title)
        self.assertEqual([row["source"].resolve() for row in rows],
                         [source.resolve() for source in sources])
        self.assertEqual([row["nome"] for row in rows], [name, name])
        original_hashes = [sha(p) for p in sources]
        output = self.base / "entrega.mp4"
        result = subprocess.run([
            sys.executable, str(ROOT / "menu_video.py"), str(project),
            "--saida", str(output), "--perfil", "prova"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.is_file())
        self.assertEqual([sha(source) for source in sources], original_hashes)
        for source in sources:
            decoded = decode(source)
            self.assertEqual(decoded.returncode, 0, decoded.stderr)
        catalog = output
        self.assertNotIn(str(self.base).encode('utf-8'), catalog.read_bytes())
        streams = probe(catalog)["streams"]
        self.assertEqual(len(streams), 1)
        stream = streams[0]
        self.assertEqual(stream["codec_name"], "h264")
        self.assertEqual(stream["pix_fmt"], "yuv420p")
        self.assertEqual(stream["r_frame_rate"], "30/1")
        self.assertEqual((stream["width"], stream["height"]), (640, 360))
        self.assertAlmostEqual(float(stream["duration"]), 8.0, delta=0.1)
        content = catalog.read_bytes()
        self.assertLess(content.index(b"moov"), content.index(b"mdat"))
        decoded = decode(catalog)
        self.assertEqual(decoded.returncode, 0, decoded.stderr)
        self.assertEqual(set(p.name for p in self.base.iterdir()), {'fontes', 'entrega.mp4'})
        self.assertFalse(any(self.base.glob(".entrega-*")))


if __name__ == "__main__":
    unittest.main()
