"""Resolução única: PATH nas fontes; somente cache portátil verificado no executável congelado."""

from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import math
import shutil
import subprocess
import sys
from threading import Lock, Thread, Event
from queue import Queue, Empty, Full
import time


class Cancelled(Exception):
    """Pedido atendido somente depois de encerrar filhos e limpar o job."""


class CancellationToken:
    def __init__(self):
        self._lock = Lock()
        self._cancelled = False
        self._committed = False

    def cancel(self):
        with self._lock:
            if self._cancelled or self._committed:
                return False
            self._cancelled = True
            return True

    def check(self):
        with self._lock:
            if self._cancelled:
                raise Cancelled('Geração cancelada.')

    def commit(self, publish):
        # Mesmo lock do clique: publicação ou cancelamento vence, nunca ambos.
        with self._lock:
            if self._cancelled:
                raise Cancelled('Geração cancelada.')
            publish()
            self._committed = True


_current_cancel = ContextVar('media_cancel', default=None)
_current_progress = ContextVar('media_progress', default=None)


@dataclass(frozen=True)
class MediaProgress:
    stage: str
    seconds: float | None = None
    duration: float | None = None

    @property
    def fraction(self):
        if self.seconds is None or self.duration is None or self.duration <= 0:
            return None
        return max(0., min(1., self.seconds / self.duration))

    def __str__(self):
        fraction = self.fraction
        return self.stage + ('…' if fraction is None else f': {fraction:.0%} da etapa')


def current_progress():
    return _current_progress.get()


def report_progress(stage):
    callback = current_progress()
    if callback is not None:
        callback(MediaProgress(stage))


@contextmanager
def progress_scope(callback):
    marker = _current_progress.set(callback)
    try:
        yield
    finally:
        _current_progress.reset(marker)


def current_cancel():
    return _current_cancel.get()


@contextmanager
def cancellation_scope(token):
    marker = _current_cancel.set(token)
    try:
        if token is not None:
            token.check()
        yield
    finally:
        _current_cancel.reset(marker)


def resolve_tool(name):
    if name not in ('ffmpeg', 'ffprobe'):
        raise RuntimeError('Ferramenta não permitida no pacote.')
    if getattr(sys, 'frozen', False):
        import client_download
        from bootstrap_gui import app_root
        try:
            cache = client_download.verified_cache(app_root(), client_download.load_manifest())
            if cache is None:
                raise ValueError('Cache do pacote ausente ou corrompido; reabra para autorizar o download.')
            return str(Path(cache['directory']) / (name + '.exe'))
        except (ValueError, OSError) as error:
            raise RuntimeError(f'Ferramentas do pacote indisponíveis: {error}') from error
    tool = shutil.which(name)
    if not tool:
        raise RuntimeError(f'{name} deve estar no PATH para execução por fontes.')
    return tool


def no_console_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}


def media_arguments(arguments):
    arguments = list(arguments)
    if getattr(sys, 'frozen', False):
        name = Path(arguments[0]).name.lower().removesuffix('.exe')
        arguments[0] = resolve_tool(name)
    return arguments


def stream_lines(arguments, consume, *, timeout=120, cancel=None):
    """Pipe incremental limitado; drena stderr e reapenas o próprio filho.

    Threads de leitura funcionam também nos pipes Windows. A fila contém no
    máximo oito blocos de 8 KiB; uma linha excessiva é recusada.
    """
    cancel = cancel if cancel is not None else current_cancel()
    if cancel is not None:
        cancel.check()
    chunks, stop, errors = Queue(maxsize=8), Event(), []
    diagnostic = bytearray()
    def read_stdout():
        try:
            while not stop.is_set():
                block = process.stdout.read1(8192)
                while not stop.is_set():
                    try:
                        chunks.put(block, timeout=.05)
                        break
                    except Full:
                        continue
                if not block:
                    return
        except Exception as exc:
            errors.append(exc)
    def read_stderr():
        try:
            while block := process.stderr.read1(8192):
                diagnostic.extend(block)
                del diagnostic[:-4096]
        except Exception as exc:
            errors.append(exc)
    readers = [Thread(target=read_stdout, name='menu-video-probe-stdout'),
               Thread(target=read_stderr, name='menu-video-probe-stderr')]
    process = subprocess.Popen(media_arguments(arguments), shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_console_options())
    try:
        deadline, pending = time.monotonic() + timeout, b''
        for reader in readers:
            reader.start()
        while True:
            if cancel is not None:
                cancel.check()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(arguments, timeout)
            if errors:
                raise errors[0]
            try:
                block = chunks.get(timeout=.05)
            except Empty:
                continue
            if not block:
                if pending:
                    consume(pending.decode('utf-8', errors='strict'))
                break
            pending += block
            while b'\n' in pending:
                line, pending = pending.split(b'\n', 1)
                if len(line) > 65536:
                    raise ValueError('linha de timestamps excessiva')
                consume(line.decode('utf-8', errors='strict'))
            if len(pending) > 65536:
                raise ValueError('linha de timestamps excessiva')
        while process.poll() is None:
            if cancel is not None:
                cancel.check()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(arguments, timeout)
            try:
                process.wait(timeout=.05)
            except subprocess.TimeoutExpired:
                pass
        if cancel is not None:
            cancel.check()
        for reader in readers:
            reader.join(timeout=.5)
        if errors:
            raise errors[0]
        if any(reader.is_alive() for reader in readers):
            raise RuntimeError('leitura dos pipes de mídia não terminou')
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, arguments, stderr=bytes(diagnostic))
        return bytes(diagnostic)
    finally:
        stop.set()
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        for reader in readers:
            if reader.ident is not None:
                reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()


def run_cancellable(arguments, *, cwd, timeout, cancel, on_progress=None, progress_path=None):
    """Drena os dois pipes e encerra somente o filho criado por este job."""
    cancel.check()
    process = subprocess.Popen(arguments, cwd=cwd, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                               **no_console_options())
    deadline = None if timeout is None else time.monotonic() + timeout
    consumed = 0
    pending = b''

    def consume(data):
        nonlocal consumed, pending
        if on_progress is None:
            return
        if progress_path is not None:
            try:
                with Path(progress_path).open('rb') as stream:
                    stream.seek(consumed)
                    chunk = stream.read()
            except FileNotFoundError:
                return
            consumed += len(chunk)
        elif data:
            chunk = data[consumed:]
            consumed = len(data)
        else:
            return
        pending += chunk
        lines = pending.split(b'\n')
        pending = lines.pop()
        for line in lines:
            if line.startswith(b'out_time_us='):
                try:
                    seconds = int(line.split(b'=', 1)[1]) / 1_000_000
                except ValueError:
                    continue
                if math.isfinite(seconds) and seconds >= 0:
                    on_progress(seconds)
    try:
        while True:
            cancel.check()
            remaining = .05 if deadline is None else deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(arguments, timeout)
            try:
                stdout, stderr = process.communicate(timeout=min(.05, remaining))
                consume(stdout)
                break
            except subprocess.TimeoutExpired as exc:
                consume(exc.output)
                continue
        cancel.check()
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, arguments, stdout, stderr)
        return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
    except BaseException:
        # O evento terminal só sai após reap e drain. Falha de cleanup não vira Cancelled.
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            process.communicate(timeout=.5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=2)
        raise
    finally:
        process.stdout.close()
        process.stderr.close()
