import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import menu_anisio as motor
from gerar_fixtures import generate
from test_real import sha


class SafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="anisio-safety-", dir=None)
        cls.base = Path(cls.temp.name)
        cls.fixtures = generate(cls.base / "fontes")
        cls.sources = [cls.fixtures / "cores-com-audio.mp4", cls.fixtures / "retrato-mudo.mov"]
        cls.before = [sha(p) for p in cls.sources]

    @classmethod
    def tearDownClass(cls):
        try:
            if [sha(p) for p in cls.sources] != cls.before:
                raise AssertionError("os originais foram modificados")
        finally:
            cls.temp.cleanup()

    def setUp(self):
        self.project = self.fixtures / "caso.json"
        self.output = self.base / f'{self._testMethodName}.mp4'

    def project_with(self, filename, name="Cerimônia"):
        self.project.write_text(json.dumps({"titulo": "Encontro", "cenas": [
            {"arquivo": filename, "nome": name}]}, ensure_ascii=False), encoding="utf-8")

    def reject(self, fragment=None):
        result = subprocess.run([
            sys.executable, str(ROOT / "menu_anisio.py"), str(self.project),
            "--saida", str(self.output), "--perfil", "prova"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        if fragment:
            self.assertIn(fragment, result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(any(self.base.glob(f".{self.output.name}-*")))
        self.assertFalse((self.base / f".{self.output.name}.anisio.lock").exists())

    def test_arquivo_faltante(self):
        self.project_with("nao-existe.mp4")
        self.reject("inexistente")

    def test_arquivo_corrompido(self):
        (self.fixtures / "corrompido.mp4").write_bytes(b"not a movie")
        self.project_with("corrompido.mp4")
        self.reject("mídia inválida")

    def test_arquivo_so_audio(self):
        self.project_with("som-sem-video.m4a")
        self.reject("sem vídeo")

    def test_stream_corrompido_com_container_legivel(self):
        content = bytearray(self.sources[0].read_bytes())
        position = content.index(b"mdat") + 4
        content[position:position + 600] = bytes(600)
        broken = self.fixtures / "stream-corrompido.mp4"
        broken.write_bytes(content)
        # O container ainda tem stream de vídeo: a decodificação precisa rejeitá-lo.
        import shutil
        details = motor.probe_media(shutil.which("ffprobe"), broken)
        self.assertTrue(any(s.get("codec_type") == "video" for s in details["streams"]))
        self.project_with(broken.name)
        self.reject("mídia inválida")

    def test_json_chaves_duplicadas(self):
        self.project.write_text('{"titulo":"A","titulo":"B","cenas":'
                                '[{"arquivo":"cores-com-audio.mp4","nome":"A"}]}', encoding="utf-8")
        self.reject("JSON inválido")

    def test_nome_unicode_cabe_em_componente_portavel(self):
        self.project_with("cores-com-audio.mp4", "😀" * 80)
        _, scenes, _ = motor.load_project(self.project, self.output)
        name = Path(scenes[0]["arquivo"]).name
        self.assertLessEqual(len(name.encode("utf-8")), 240)
        self.assertEqual(scenes[0]["nome"], "😀" * 80)

    def test_json_tipos_invalidos_e_limites(self):
        for data in [None, [], {}, {"titulo": "Evento", "cenas": "errado"},
                     {"titulo": "x" * 101, "cenas": []},
                     {"titulo": "Evento", "cenas": [{"arquivo": "cores-com-audio.mp4", "nome": "x\ny"}]},
                     {"titulo": "Evento", "cenas": [{"arquivo": "cores-com-audio.mp4", "nome": "x" * 81}]}]:
            with self.subTest(data=data):
                self.project.write_text(json.dumps(data), encoding="utf-8")
                self.reject()

    def test_caminho_absoluto_rejeitado(self):
        for name in [str(self.sources[0]), "C:\\privado\\filme.mp4"]:
            with self.subTest(name=name):
                self.project_with(name)
                self.reject("relativo")

    def test_saida_igual_entrada_e_pai_das_entradas(self):
        self.project_with("cores-com-audio.mp4")
        for output in [self.sources[0], self.fixtures, self.project]:
            with self.subTest(output=output):
                with self.assertRaises(motor.ErroProjeto):
                    motor.export_project(self.project, output, "prova")
        self.assertEqual([sha(p) for p in self.sources], self.before)

    def test_link_quebrado_na_saida_rejeitado(self):
        self.project_with("cores-com-audio.mp4")
        try:
            self.output.symlink_to(self.base / "alvo-inexistente", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("criação de symlink indisponível neste ambiente")
        with self.assertRaisesRegex(motor.ErroProjeto, "saída já existe"):
            motor.export_project(self.project, self.output, "prova")
        self.assertTrue(self.output.is_symlink())

    def test_falha_de_render_limpa_so_staging_proprio(self):
        self.project_with("cores-com-audio.mp4")
        sentinel = self.base / ".outra-exportacao-nao-tocar"
        sentinel.mkdir()
        (sentinel / "intacto").write_text("intacto", encoding="utf-8")
        with patch.object(motor, "render_catalog", side_effect=motor.ErroProjeto("falha induzida")):
            with self.assertRaisesRegex(motor.ErroProjeto, "falha induzida"):
                motor.export_project(self.project, self.output, "prova")
        self.assertFalse(self.output.exists())
        self.assertFalse(any(self.base.glob(f".{self.output.name}-*")))
        self.assertFalse((self.base / f".{self.output.name}.anisio.lock").exists())
        self.assertEqual((sentinel / "intacto").read_text(encoding="utf-8"), "intacto")

    def test_timeout_e_tratado(self):
        with patch.object(motor.subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 1)):
            with self.assertRaisesRegex(motor.ErroProjeto, "tempo limite"):
                motor.run_media(["ffmpeg"], timeout=1)


if __name__ == "__main__":
    unittest.main()
