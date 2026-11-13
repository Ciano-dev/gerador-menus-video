"""Uma operação por vez. Worker só publica dados; Tk consome pela thread principal."""

from dataclasses import dataclass
from queue import Empty, Queue
from threading import Thread
from media_runtime import Cancelled


@dataclass(frozen=True)
class Event:
    kind: str
    value: object


class Worker:
    def __init__(self):
        self._events = Queue()
        self.busy = False

    def submit(self, operation):
        if self.busy:
            raise RuntimeError('Aguarde a operação atual terminar.')
        self.busy = True

        def run():
            try:
                result = operation(lambda message: self._events.put(Event('progress', message)))
            except Cancelled as exc:
                self._events.put(Event('cancelled', exc))
            except Exception as exc:
                self._events.put(Event('error', exc))
            else:
                self._events.put(Event('done', result))

        # Sem abandono de cópia/render ao fechar a janela; a UI aguarda sem join bloqueante.
        try:
            Thread(target=run, name='menu-video-autoria', daemon=False).start()
        except Exception:
            self.busy = False
            raise

    def poll(self):
        events = []
        # Um lote finito devolve o controle ao Tk mesmo com produtor ativo.
        for _ in range(100):
            try:
                event = self._events.get_nowait()
            except Empty:
                break
            events.append(event)
            if event.kind in ('done', 'error', 'cancelled'):
                self.busy = False
        return events
