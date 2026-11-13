"""Diagnósticos sem importar Tk ou módulos da aplicação."""

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import tempfile
import threading
import traceback

active = None


def notify(message):
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'Gerador de menus em vídeo', 0x10)
    elif sys.stderr is not None:
        print(message, file=sys.stderr)


class Diagnostics:
    def __init__(self, directory=None, *, max_bytes=1024 * 1024, backups=3):
        local = os.environ.get('LOCALAPPDATA')
        self.location_note = ''
        if directory is None:
            if local and Path(local).is_absolute():
                directory = Path(local) / 'GeradorMenusVideo' / 'logs'
            else:
                directory = Path(tempfile.gettempdir()) / 'GeradorMenusVideo' / 'logs'
                self.location_note = 'Fallback explícito: LOCALAPPDATA indisponível; logs no temporário.'
        self.path = Path(directory).absolute() / 'diagnostic.log'
        self.max_bytes, self.backups = max_bytes, backups
        self.lock = threading.RLock()
        self.failures = 0
        self.notify = notify

    def write(self, context, exc_type, exc, tb):
        trace = ''.join(traceback.format_exception(exc_type, exc, tb, chain=True))
        record = f'\n{datetime.now(timezone.utc).isoformat()} [{context}]\n{trace}'
        data = record.encode('utf-8')
        if len(data) > self.max_bytes:
            raise OSError('Traceback excede o limite do log; diagnóstico completo exibido na mensagem.')
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size + len(data) > self.max_bytes:
                for index in range(self.backups, 0, -1):
                    source = self.path if index == 1 else self.path.with_name(f'{self.path.name}.{index-1}')
                    target = self.path.with_name(f'{self.path.name}.{index}')
                    if source.exists():
                        source.replace(target)
            with self.path.open('ab') as stream:
                stream.write(data)
        return self.path

    def report(self, context, exc_type, exc, tb, *, notify_user=True):
        self.failures += 1
        try:
            self.write(context, exc_type, exc, tb)
        except Exception as log_error:
            original = ''.join(traceback.format_exception(exc_type, exc, tb, chain=True))
            log_trace = ''.join(traceback.format_exception(log_error))
            message = (f'Falha ao gravar diagnóstico em {self.path}: {log_error}\n'
                       f'{self.location_note}\nErro original completo:\n{original}\n'
                       f'Falha do log:\n{log_trace}')
            if sys.stderr is not None:
                print(message, file=sys.stderr)
        else:
            message = (f'Não foi possível concluir a operação ({context}).\n'
                       f'{exc}\nDiagnóstico completo: {self.path}\n{self.location_note}')
        if notify_user:
            self.notify(message)
        return message

    def install(self):
        global active
        active = self
        sys.excepthook = lambda *info: self.report('exceção não tratada', *info)
        threading.excepthook = lambda args: self.report(
            f'thread {args.thread.name}', args.exc_type, args.exc_value, args.exc_traceback)

    def attach_tk(self, root):
        root.report_callback_exception = lambda *info: self.report('callback Tk', *info)


def report_handled(error, context):
    if active is not None:
        return active.report(context, type(error), error, error.__traceback__, notify_user=False)
    return str(error)
