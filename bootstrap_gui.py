"""Small first-run Tk consent dialog; network and ZIP work stay off the Tk thread."""
from pathlib import Path
import queue
import sys
import threading
import webbrowser

import client_download


def app_root():
    path = Path(sys.executable)
    if not path.is_absolute():
        raise ValueError('Executável portátil precisa de caminho absoluto.')
    return path.parent


def consent_text(root, data=None):
    data = data or client_download.load_manifest()
    links = data['links']
    return (f'Para criar vídeos, autorize baixar aproximadamente 195 MB pela internet.\n'
            f'Destino portátil exato: {root / "tools"}\n'
            'Sem instalação, administrador ou alteração do PATH. O ZIP do aplicativo não inclui '
            'FFmpeg/ffprobe. Os binários serão baixados diretamente do fornecedor após seu consentimento. '
            'Depois da verificação, o aplicativo pode ser reaberto offline.\n'
            'Recusar fecha o aplicativo sem baixar nem renderizar.\n\n'
            + '\n'.join(f'{label}: {links[key]}' for label, key in (
                ('Fornecedor', 'supplier'), ('Release fixada', 'release'),
                ('Fontes e scripts do fornecedor', 'build_scripts'),
                ('Fonte FFmpeg', 'upstream_source'), ('Licença GPL', 'license'))))


class DownloadSession:
    def __init__(self, root, data):
        self.root, self.data = root, data
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.thread = None
        self.closed = False

    @property
    def busy(self):
        return self.thread is not None and self.thread.is_alive()

    def accept(self):
        if self.thread is not None or self.closed:
            return
        def work():
            try:
                result = client_download.ensure_tools(
                    self.root, self.data, consent=True, cancel=self.cancel,
                    progress=lambda done, total: self.events.put(('progress', (done, total))))
                self.events.put(('done', result))
            except Exception as error:
                self.events.put(('error', error))
        self.thread = threading.Thread(target=work, name='portable-tools-download', daemon=False)
        self.thread.start()

    def decline(self):
        self.close()

    def close(self):
        self.closed = True
        self.cancel.set()

    def drain(self):
        result = []
        while True:
            try:
                result.append(self.events.get_nowait())
            except queue.Empty:
                return result


def prepare_tools(diagnostics, authorize_download=False, smoke=False):
    data = client_download.load_manifest()
    root_path = app_root()
    cached = client_download.verified_cache(root_path, data)
    if cached:
        return cached
    if smoke and not authorize_download:
        raise ValueError('Cache ausente no smoke: exige --smoke-download explícito.')
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    diagnostics.attach_tk(root)
    root.title('Gerador de menus em vídeo')
    root.geometry('760x650')
    session = DownloadSession(root_path, data)
    result = None
    finished = False
    heartbeats = 0
    viewable = False
    ttk.Label(root, text='Autorizar download das ferramentas de vídeo?', font=('', 14)).pack(padx=20, pady=15)
    text = tk.Text(root, wrap='word', height=21, padx=12, pady=8)
    text.insert('1.0', consent_text(root_path, data))
    text.configure(state='disabled')
    text.pack(fill='both', expand=True, padx=16)
    links = ttk.Frame(root)
    links.pack(fill='x', padx=16, pady=6)
    for label, key in (('Fornecedor', 'supplier'), ('Fontes/scripts', 'build_scripts'), ('Licença GPL', 'license')):
        ttk.Button(links, text=label, command=lambda url=data['links'][key]: webbrowser.open(url)).pack(side='left', padx=5)
    status = ttk.Label(root, text='Nenhum download iniciado.')
    status.pack(pady=4)
    bar = ttk.Progressbar(root, maximum=data['binary']['size_bytes'])
    bar.pack(fill='x', padx=20, pady=6)
    buttons = ttk.Frame(root)
    buttons.pack(pady=12)

    def accept():
        yes.configure(state='disabled')
        no.configure(state='disabled')
        status.configure(text='Baixando e verificando… aguarde. Fechar cancela com segurança.')
        session.accept()

    def close():
        session.close()
        status.configure(text='Encerrando com segurança; aguardando a operação de rede terminar…')
        yes.configure(state='disabled')
        no.configure(state='disabled')

    yes = ttk.Button(buttons, text='Autorizar e baixar 195 MB', command=accept)
    yes.pack(side='left', padx=8)
    no = ttk.Button(buttons, text='Recusar e sair', command=close)
    no.pack(side='left', padx=8)
    root.protocol('WM_DELETE_WINDOW', close)

    def poll():
        nonlocal result, finished, heartbeats, viewable
        viewable = viewable or bool(root.winfo_viewable())
        if session.busy:
            heartbeats += 1
        for kind, value in session.drain():
            if kind == 'progress':
                done, total = value
                bar['value'] = done
                status.configure(text=f'Baixado {done / 1e6:.1f} / {total / 1e6:.1f} MB; verificando SHA256 ao concluir.')
            elif kind == 'done':
                result = value
                finished = True
            elif kind == 'error':
                finished = True
                if not session.closed:
                    diagnostics.report('download inicial; tente novamente na próxima abertura',
                                       type(value), value, value.__traceback__)
        if (finished or session.closed) and not session.busy:
            root.destroy()
            return
        root.after(50, poll)

    root.after(50, poll)
    if authorize_download:
        root.after(150, accept)  # Only the explicit automated smoke authorization uses this.
    root.mainloop()
    if result is not None:
        result.update(ui_heartbeats=heartbeats, window_viewable=viewable)
    return None if session.closed else result
