import json
import os
from unittest.mock import patch
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "menu_anisio.py"


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="anisio-test-", dir=None)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / "projeto.json"
        self.output = self.base / "entrega.mp4"

    def run_cli(self):
        return subprocess.run(
            [sys.executable, str(CLI), str(self.project), "--saida", str(self.output)],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )

    def test_json_invalido_nao_cria_saida(self):
        self.project.write_text("{ inválido", encoding="utf-8")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("JSON inválido", result.stderr)
        self.assertFalse(self.output.exists())

    def test_cli_utf8_independente_da_codificacao_do_runner(self):
        with patch.dict(os.environ, {'PYTHONIOENCODING': 'cp1252'}):
            self.test_json_invalido_nao_cria_saida()

    def test_projeto_vazio_nao_cria_saida(self):
        self.project.write_text(json.dumps({"titulo": "Festa", "cenas": []}), encoding="utf-8")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("cenas", result.stderr)
        self.assertFalse(self.output.exists())

    def test_saida_existente_e_preservada(self):
        self.project.write_text(json.dumps({"titulo": "Festa", "cenas": [
            {"arquivo": "ausente.mp4", "nome": "Entrada"}]}), encoding="utf-8")
        self.output.mkdir()
        marker = self.output / "sentinela.txt"
        marker.write_text("intacto", encoding="utf-8")
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("saída já existe", result.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "intacto")


if __name__ == "__main__":
    unittest.main()
