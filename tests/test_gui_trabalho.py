import importlib
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = importlib.import_module('gui_trabalho').Worker()

    def collect(self):
        events = []
        deadline = time.monotonic() + 3
        while self.worker.busy and time.monotonic() < deadline:
            events.extend(self.worker.poll())
            time.sleep(.005)
        self.assertFalse(self.worker.busy)
        return events

    def test_trabalho_fora_da_thread_chamadora_com_eventos_ordenados(self):
        caller = threading.get_ident()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def operation(progress):
            self.assertNotEqual(threading.get_ident(), caller)
            started.set()
            release.wait(2)
            progress('Renderizando pelo motor')
            return 'arquivo.mp4'
        self.worker.submit(operation)
        self.assertTrue(started.wait(1))
        self.assertTrue(self.worker.busy)
        self.assertEqual(self.worker.poll(), [])
        release.set()
        events = self.collect()
        self.assertEqual([(e.kind, e.value) for e in events],
                         [('progress', 'Renderizando pelo motor'), ('done', 'arquivo.mp4')])

    def test_erro_visivel_e_worker_reutilizavel(self):
        def fail(progress):
            raise ValueError('mídia inválida')
        self.worker.submit(fail)
        events = self.collect()
        self.assertEqual(events[-1].kind, 'error')
        self.assertIn('mídia inválida', str(events[-1].value))
        self.worker.submit(lambda progress: 42)
        self.assertEqual(self.collect()[-1].value, 42)

    def test_nao_aceita_operacoes_simultaneas_nem_perde_resultado_pendente(self):
        release = threading.Event()
        self.addCleanup(release.set)
        self.worker.submit(lambda progress: release.wait(2))
        with self.assertRaises(RuntimeError):
            self.worker.submit(lambda progress: None)
        release.set()
        self.collect()


if __name__ == '__main__':
    unittest.main()
