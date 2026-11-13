"""Interface mínima de autoria. Execute: python menu_gui.py (sem dependências extras)."""

if __name__ == '__main__':
    from windows_entry import main
    raise SystemExit(main())

import copy
from fractions import Fraction
import math
from pathlib import Path
import re
import sys
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk, font as tkfont

from gui_autoria import Project
import font_families as families
from gui_cores import ColorPreview, safe_color
import gui_servicos as services
from gui_trabalho import Worker
from menu_video import ErroProjeto, font_size
from diagnosticos import report_handled
from media_runtime import CancellationToken, cancellation_scope, progress_scope, MediaProgress


# Cores da interface; a paleta do vídeo pertence exclusivamente a Project.
UI_COLORS = {'background': '#252525', 'surface': '#303030', 'field': '#1e1e1e',
             'text': '#f0f0f0', 'muted': '#bcbcbc', 'border': '#606060',
             'selection': '#305f89', 'focus': '#78b8ed', 'disabled': '#a6a6a6'}


class App:
    def __init__(self, root):
        self.root = root
        self.project = Project()
        self.saved_state = copy.deepcopy(self.project)
        self.project_path = None
        self.last_catalog = None
        self.editing_index = None
        self.worker = Worker()
        self.controls = []
        self._refreshing = False
        self._font_edited = set()
        self._font_errors = {}
        self._poll_token = None
        self._cancel_token = None
        self._cancel_requested = False
        self._close_after_job = False
        self._closed = False
        root.title('Gerador de menus em vídeo')
        root.configure(background=UI_COLORS['background'])
        root.geometry('1024x720')
        root.minsize(820, 600)
        root.protocol('WM_DELETE_WINDOW', self.action(self.close))
        self._build()
        self.refresh()
        self._poll_token = root.after(100, self.poll)

    def action(self, callback):
        def invoke(*args):
            if self.worker.busy and callback != self.close:
                return
            try:
                callback(*args)
            except (ErroProjeto, OSError, ValueError, IndexError) as exc:
                self.show_error(exc)
        return invoke

    def show_error(self, error):
        self.status_var.set(f'Erro: {error}')
        message = report_handled(error, 'operação da interface')
        messagebox.showerror('Gerador de menus em vídeo', message, parent=self.root)

    def _button(self, parent, label, command):
        widget = ttk.Button(parent, text=label, command=self.action(command))
        self.controls.append(widget)
        return widget

    def _entry(self, parent, variable, width=24):
        widget = ttk.Entry(parent, textvariable=variable, width=width)
        self.controls.append(widget)
        return widget

    def _font_spinbox(self, parent, scope, variable):
        widget = ttk.Spinbox(parent, textvariable=variable, from_=1, to=200,
                             increment=1, width=4,
                             command=lambda: self.finish_font_edit(scope, force=True))
        self.controls.append(widget)
        def enter(event):
            self.finish_font_edit(scope, force=True)
            return 'break'
        widget.bind('<Return>', enter)
        widget.bind('<KP_Enter>', enter)
        widget.bind('<FocusOut>', lambda event: self.finish_font_edit(scope))
        return widget

    def _family_combo(self, parent, scope):
        if scope not in self.family_vars:
            self.family_vars[scope] = tk.StringVar(master=self.root, value=families.CURRENT)
        widget = ttk.Combobox(parent, textvariable=self.family_vars[scope],
                              values=families.OPTIONS, state='readonly', width=23)
        self.controls.append(widget)
        self.family_combos[scope] = widget
        def choose(event):
            self.finish_family_edit(scope)
            return 'break'
        for event in ('<<ComboboxSelected>>', '<Return>', '<KP_Enter>'):
            widget.bind(event, choose)
        return widget

    def finish_family_edit(self, scope):
        """Muda somente a família; não captura nem recarrega campos pendentes."""
        if (getattr(self, '_refreshing', False) or getattr(self, '_loading_scene', False)
                or self.worker.busy or (scope == 'scene' and self.editing_index is None)):
            return False
        target = self.project if scope == 'title' else self.project.scenes[self.editing_index]
        field = 'title_font_family' if scope == 'title' else 'font_family'
        try:
            identity = families.from_label(self.family_vars[scope].get())
            if identity is not None:
                self.preview_fonts.font(identity, size=-12)
        except ErroProjeto as exc:
            self.family_vars[scope].set(families.label(getattr(target, field)))
            self.family_combos[scope].state(['invalid'])
            self.status_var.set(str(exc))
            report_handled(exc, 'família tipográfica')
            return False
        setattr(target, field, identity)
        self.family_combos[scope].state(['!invalid'])
        self.status_var.set(f'Família: {families.label(identity)} aplicada. Salvar o projeto é opcional.')
        self.color_preview.request()
        return True

    def mark_font_edited(self, scope):
        if (getattr(self, '_refreshing', False) or getattr(self, '_loading_scene', False)
                or getattr(self, '_loading_fonts', False)):
            return
        if not hasattr(self, '_font_edited'):
            self._font_edited = set()
        self._font_edited.add(scope)

    def finish_font_edit(self, scope, *, force=False):
        """Confirma só a fonte; nunca captura nome, trecho ou duração pendentes."""
        if (getattr(self, '_refreshing', False) or self.worker.busy
                or (scope == 'scene' and self.editing_index is None)):
            return False
        if not force and scope not in getattr(self, '_font_edited', set()):
            return True
        variable = getattr(self, f'{scope}_font_var')
        label = 'Título' if scope == 'title' else 'Nome da cena'
        try:
            raw = variable.get().strip()
            digits = raw.lstrip('0') or '0'
            if not re.fullmatch(r'[0-9]+', raw) or len(digits) > 3:
                raise ErroProjeto(f'{label}: informe um inteiro de 1 a 200 px (referência 1080p).')
            value = font_size(int(digits), label)
        except ErroProjeto as exc:
            self.font_entries[scope].state(['invalid'])
            if not hasattr(self, '_font_errors'):
                self._font_errors = {}
            self._font_errors[scope] = str(exc)
            self.font_notice_var.set(' '.join(self._font_errors.values()))
            self.status_var.set(f'Fonte não aplicada. {exc}')
            return False
        if scope == 'title':
            self.project.title_font_size = value
        else:
            self.project.scenes[self.editing_index].font_size = value
        self._loading_fonts = True
        try:
            variable.set(str(value))
        finally:
            self._loading_fonts = False
        self._font_edited.discard(scope)
        self.font_entries[scope].state(['!invalid'])
        if not hasattr(self, '_font_errors'):
            self._font_errors = {}
        self._font_errors.pop(scope, None)
        self.font_notice_var.set(' '.join(self._font_errors.values()))
        self.status_var.set(f'{label}: {value} px (referência 1080p) aplicado. Salvar o projeto é opcional.')
        self.color_preview.request()
        return True

    def _group(self, parent, title):
        group = ttk.LabelFrame(parent, text=title, padding=10)
        group.pack(fill='x', pady=(0, 12))
        return group

    def _configure_theme(self):
        colors = UI_COLORS
        native = tkfont.nametofont('TkDefaultFont', root=self.root)
        family = 'Segoe UI' if sys.platform == 'win32' and 'Segoe UI' in tkfont.families(self.root) else native.actual('family')
        self.ui_font = (family, 10)
        style = ttk.Style(self.root)
        style.theme_use('clam')
        self.style = style
        style.configure('.', font=self.ui_font, foreground=colors['text'],
                        background=colors['background'], bordercolor=colors['border'],
                        lightcolor=colors['border'], darkcolor=colors['border'],
                        troughcolor=colors['field'], focuscolor=colors['focus'])
        for name in ('TFrame', 'TLabel', 'TLabelframe', 'TLabelframe.Label',
                     'TCheckbutton', 'TRadiobutton'):
            style.configure(name, background=colors['background'])
            style.map(name, foreground=[('disabled', colors['disabled'])],
                      background=[('active', colors['surface'])])
        for name in ('TCheckbutton', 'TRadiobutton'):
            style.configure(name, indicatorbackground=colors['field'], indicatorforeground=colors['text'])
            style.map(name, indicatorbackground=[('disabled', colors['surface']), ('selected', colors['selection'])])
        style.configure('TLabelframe.Label', font=(family, 11, 'bold'))
        style.configure('Heading.TLabel', font=(family, 12, 'bold'))
        style.configure('TButton', padding=(8, 6), background=colors['surface'])
        style.map('TButton', background=[('disabled', colors['background']), ('pressed', colors['selection']), ('active', '#424242')],
                  foreground=[('disabled', colors['disabled'])],
                  bordercolor=[('focus', colors['focus'])])
        style.configure('Export.TButton', background=colors['selection'])
        style.map('Export.TButton', background=[('disabled', colors['surface']), ('active', '#3e76a6')])
        for name in ('TEntry', 'TCombobox', 'TSpinbox'):
            style.configure(name, fieldbackground=colors['field'], foreground=colors['text'],
                            selectbackground=colors['selection'], selectforeground=colors['text'],
                            insertcolor=colors['text'], padding=4, arrowsize=16)
            style.configure(name, arrowcolor=colors['text'])
            style.map(name, fieldbackground=[('disabled', colors['surface']), ('readonly', colors['field'])],
                      foreground=[('disabled', colors['disabled']), ('readonly', colors['text'])],
                      bordercolor=[('focus', colors['focus'])])
        style.configure('Treeview', background=colors['field'], fieldbackground=colors['field'],
                        foreground=colors['text'], rowheight=max(28, native.metrics('linespace') + 10))
        style.configure('Treeview.Heading', background=colors['surface'], foreground=colors['text'])
        style.map('Treeview', background=[('selected', colors['selection'])],
                  foreground=[('disabled', colors['disabled']), ('selected', colors['text'])],
                  bordercolor=[('focus', colors['focus'])])
        style.map('Treeview.Heading', background=[('active', '#424242')])
        style.configure('TProgressbar', background=colors['selection'])
        self.root.option_add('*TCombobox*Listbox.background', colors['field'])
        self.root.option_add('*TCombobox*Listbox.foreground', colors['text'])
        self.root.option_add('*TCombobox*Listbox.selectBackground', colors['selection'])
        self.root.option_add('*TCombobox*Listbox.selectForeground', colors['text'])
        self.root.option_add('*TCombobox*Listbox.font', self.ui_font)

    def _build(self):
        self.preview_fonts = families.PreviewFonts(self.root)
        self.family_vars = {}
        self.family_combos = {}
        self._configure_theme()
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        viewport = ttk.Frame(self.root)
        viewport.grid(row=0, column=0, sticky='nsew')
        viewport.columnconfigure(0, weight=1)
        viewport.rowconfigure(0, weight=1)
        canvas = tk.Canvas(viewport, background=UI_COLORS['background'], highlightthickness=0)
        self.viewport_canvas = canvas
        canvas.grid(row=0, column=0, sticky='nsew')
        scrollbar = ttk.Scrollbar(viewport, orient='vertical', command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        canvas.configure(yscrollcommand=scrollbar.set)
        body = self.body = ttk.Frame(canvas, padding=12)
        item = canvas.create_window(0, 0, window=body, anchor='nw')
        body.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(item, width=event.width))
        self.root.bind('<MouseWheel>', lambda event: canvas.yview_scroll(
            -1 if event.delta > 0 else 1, 'units'))
        self.root.bind('<Button-4>', lambda event: canvas.yview_scroll(-1, 'units'))
        self.root.bind('<Button-5>', lambda event: canvas.yview_scroll(1, 'units'))
        self.root.bind('<FocusIn>', self.ensure_focus_visible, add='+')
        ttk.Label(body, text='Gerador de menus em vídeo', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(body, text='Selecione até seis vídeos e gere um menu em vídeo.').pack(
            anchor='w', pady=(2, 12))
        files = ttk.Frame(body)
        files.pack(fill='x', pady=(0, 5))
        for label, callback in [('Novo projeto', self.new_project), ('Abrir projeto…', self.choose_open),
                                ('Salvar projeto como…', self.save_as)]:
            self._button(files, label, callback).pack(side='left', padx=(0, 8))
        self.path_var = tk.StringVar(master=self.root)
        ttk.Label(body, textvariable=self.path_var, wraplength=900).pack(anchor='w', pady=(0, 12))

        scenes = self.scenes_group = self._group(body, '1. Vídeos e trechos')
        toolbar = ttk.Frame(scenes)
        toolbar.pack(fill='x', pady=(0, 8))
        for label, callback in [('Adicionar vídeos…', self.choose_videos), ('Remover', self.remove_scene),
                                ('Subir', lambda: self.move_scene(-1)),
                                ('Descer', lambda: self.move_scene(1))]:
            self._button(toolbar, label, callback).pack(side='left', padx=(0, 8))
        columns = ('order', 'name', 'file', 'start', 'duration', 'source_duration')
        self.tree = ttk.Treeview(scenes, columns=columns, show='headings', height=6, selectmode='browse')
        for key, label, width in zip(columns,
                ('Ordem', 'Nome da cena', 'Arquivo de vídeo', 'Início', 'Prévia', 'Duração do vídeo'),
                (50, 190, 210, 80, 80, 80)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=45, stretch=key in ('name', 'file'))
        self.tree.pack(fill='x')
        horizontal = ttk.Scrollbar(scenes, orient='horizontal', command=self.tree.xview)
        horizontal.pack(fill='x')
        self.tree.configure(xscrollcommand=horizontal.set)
        self.tree.bind('<<TreeviewSelect>>', self.action(self.select_scene))
        self.controls.append(self.tree)
        self.scene_path_var = tk.StringVar(master=self.root)
        ttk.Label(scenes, textvariable=self.scene_path_var, wraplength=900).pack(anchor='w', pady=6)
        editor = self.scene_editor = ttk.Frame(scenes)
        editor.pack(fill='x')
        self.scene_name_var = tk.StringVar(master=self.root)
        self.font_entries = {}
        self.font_notice_var = tk.StringVar(master=self.root)
        self.scene_font_var = tk.StringVar(master=self.root, value='24')
        self.scene_font_var.trace_add('write', lambda *args: self.mark_font_edited('scene'))
        ttk.Label(editor, text='Nome da cena').grid(row=0, column=0, sticky='w')
        name_row = ttk.Frame(editor)
        name_row.grid(row=0, column=1, columnspan=3, sticky='ew', padx=(8, 0))
        self._entry(name_row, self.scene_name_var, 24).pack(side='left', fill='x', expand=True)
        ttk.Label(name_row, text='Fonte (px · 1080p)').pack(side='left', padx=(8, 4))
        self.font_entries['scene'] = self._font_spinbox(name_row, 'scene', self.scene_font_var)
        self.font_entries['scene'].pack(side='left')
        self._family_combo(name_row, 'scene').pack(side='left', padx=(8, 0))
        ttk.Label(editor, text='Nome exibido no menu; não altera o arquivo original.').grid(
            row=1, column=0, columnspan=4, sticky='w', pady=(4, 0))
        self.scene_time_groups, self.scene_time_entries = {}, {}
        for column, key, label in [(0, 'start', 'Início'), (1, 'end', 'Fim')]:
            group = ttk.LabelFrame(editor, text=label)
            self.scene_time_groups[key] = group
            group.grid(row=2, column=column, sticky='w', padx=(0, 12), pady=(8, 0))
            for position, (unit, caption) in enumerate(
                    [('hours', 'Hora'), ('minutes', 'Min'), ('seconds', 'Seg')]):
                variable = tk.StringVar(master=self.root)
                setattr(self, f'scene_{key}_{unit}_var', variable)
                variable.trace_add('write', lambda *args, k=key: self.mark_scene_time_edited(k))
                ttk.Label(group, text=caption).grid(row=0, column=position)
                entry = self._entry(group, variable, 4)
                entry.grid(row=1, column=position, padx=2, pady=(0, 4))
                self.scene_time_entries[key, unit] = entry
        editor.columnconfigure(3, weight=1)
        self.apply_scene_button = self._button(editor, 'Aplicar nome e trecho', self.apply_scene)
        self.apply_scene_button.grid(row=2, column=2, sticky='w', padx=(0, 8))
        self.scene_duration_var = tk.StringVar(master=self.root)
        ttk.Label(editor, textvariable=self.scene_duration_var).grid(row=3, column=0, columnspan=4, sticky='w', pady=(6, 0))
        ttk.Label(editor, textvariable=self.font_notice_var, wraplength=700).grid(row=4, column=0, columnspan=4, sticky='w')

        visual = self.visual_group = self._group(body, '2. Título e cores')
        self.title_var = tk.StringVar(master=self.root)
        self.title_font_var = tk.StringVar(master=self.root, value='44')
        self.title_font_var.trace_add('write', lambda *args: self.mark_font_edited('title'))
        ttk.Label(visual, text='Título').grid(row=0, column=0, sticky='w')
        title_row = ttk.Frame(visual)
        title_row.grid(row=0, column=1, columnspan=3, sticky='ew', padx=8)
        self._entry(title_row, self.title_var).pack(side='left', fill='x', expand=True)
        ttk.Label(title_row, text='Fonte (px · 1080p)').pack(side='left', padx=(8, 4))
        self.font_entries['title'] = self._font_spinbox(title_row, 'title', self.title_font_var)
        self.font_entries['title'].pack(side='left')
        self._family_combo(title_row, 'title').pack(side='left', padx=(8, 0))
        ttk.Label(visual, textvariable=self.font_notice_var, wraplength=700).grid(row=5, column=0, columnspan=4, sticky='w')
        self.font_preview_warning_var = tk.StringVar(master=self.root)
        ttk.Label(visual, textvariable=self.font_preview_warning_var, wraplength=700).grid(row=6, column=0, columnspan=4, sticky='w')
        self.alignment_var = tk.StringVar(master=self.root, value='esquerda')
        ttk.Label(visual, text='Posição do título').grid(row=4, column=0, sticky='w', pady=(8, 0))
        for column, (value, label) in enumerate([('esquerda', 'À esquerda'),
                                                ('centralizado', 'Centralizado')], 1):
            radio = ttk.Radiobutton(visual, text=label, variable=self.alignment_var, value=value)
            radio.grid(row=4, column=column, sticky='w', padx=8, pady=(8, 0))
            self.controls.append(radio)
        visual.columnconfigure(3, weight=1)
        self.color_vars = {}
        self.color_swatches = {}
        for row, (key, label) in enumerate([('fundo', 'Fundo'), ('texto', 'Texto'),
                                            ('destaque', 'Destaque')], 1):
            variable = self.color_vars[key] = tk.StringVar(master=self.root)
            ttk.Label(visual, text=label).grid(row=row, column=0, sticky='w', pady=(8, 0))
            swatch = self.color_swatches[key] = tk.Label(visual, width=3, height=1,
                background=safe_color(self.project.colors.get(key), key), relief='solid', borderwidth=1)
            swatch.grid(row=row, column=1, sticky='w', padx=8, pady=(8, 0))
            self._button(visual, 'Escolher cor…', lambda k=key: self.choose_color(k)).grid(
                row=row, column=2, sticky='w', pady=(8, 0))
        preview = ttk.Frame(visual)
        preview.grid(row=1, column=3, rowspan=3, sticky='nsew', padx=(16, 0), pady=(8, 0))
        ttk.Label(preview, text='Prévia aproximada (Tk; vídeo usa FreeType)', wraplength=320).pack(anchor='e')
        holder = ttk.Frame(preview, width=384, height=216)
        holder.pack(fill='x', pady=(4, 0))
        self.color_canvas = tk.Canvas(holder, width=384, height=216,
                                      highlightthickness=0, background='white')
        self.color_canvas.place(relx=1, anchor='ne', width=384, height=216)
        def resize_preview(event):
            width = max(1, min(384, event.width))
            height = max(1, round(width * 9 / 16))
            holder.configure(height=height)
            self.color_canvas.place_configure(width=width, height=height)
        holder.bind('<Configure>', resize_preview)
        self.color_preview = ColorPreview(self.color_canvas, self.title_var, self.color_vars,
                                          lambda: [scene.name for scene in self.project.scenes],
                                          sizes=lambda: (self.project.title_font_size,
                                                         [scene.font_size for scene in self.project.scenes]),
                                          families=lambda: (self.project.title_font_family,
                                                            [scene.font_family for scene in self.project.scenes]),
                                          preview_fonts=self.preview_fonts,
                                          warning=self.font_preview_warning_var.set,
                                          alignment=self.alignment_var)
        # Compatibility value for old projects; no footer control or rendered notice.
        self.footer_var = tk.BooleanVar(master=self.root, value=False)

        audio = self.audio_group = self._group(body, '3. Música e duração')
        audio_buttons = ttk.Frame(audio)
        audio_buttons.pack(fill='x')
        self._button(audio_buttons, 'Escolher música…', self.choose_music).pack(side='left', padx=(0, 8))
        self._button(audio_buttons, 'Retirar música', self.clear_music).pack(side='left')
        self.music_var = tk.StringVar(master=self.root)
        ttk.Label(audio, textvariable=self.music_var, wraplength=900).pack(anchor='w', pady=8)
        mode_row = ttk.Frame(audio)
        mode_row.pack(fill='x', pady=(0, 8))
        self.duration_mode_var = tk.StringVar(master=self.root, value='manual')
        self.duration_minutes_var = tk.StringVar(master=self.root, value='1')
        self.duration_seconds_var = tk.StringVar(master=self.root, value='0')
        for variable in (self.duration_minutes_var, self.duration_seconds_var):
            variable.trace_add('write', self.mark_duration_edited)
        self.duration_entries = []
        for value, label in [('manual', 'Duração manual'), ('musica', 'Usar duração da música')]:
            radio = ttk.Radiobutton(mode_row, text=label, variable=self.duration_mode_var,
                                    value=value, command=self.update_duration_mode)
            radio.pack(side='left', padx=(0, 8))
            self.controls.append(radio)
        duration = ttk.Frame(audio)
        duration.pack(fill='x')
        for label, variable in [('Minutos', self.duration_minutes_var), ('Segundos', self.duration_seconds_var)]:
            ttk.Label(duration, text=label).pack(side='left', padx=(8, 4))
            entry = self._entry(duration, variable, 5)
            entry.pack(side='left')
            self.duration_entries.append(entry)
            entry.bind('<Return>', self.action(lambda event: self.finish_duration_edit()))
        self._button(duration, 'Aplicar duração', self.finish_duration_edit).pack(side='left', padx=8)
        self.duration_hint_var = tk.StringVar(master=self.root)
        ttk.Label(audio, textvariable=self.duration_hint_var, wraplength=700).pack(anchor='w', pady=(8, 0))

        render = self.export_group = self._group(body, '4. Exportar vídeo')
        buttons = ttk.Frame(render)
        buttons.pack(fill='x')
        ttk.Label(buttons, text='Formato').pack(side='left', padx=(0, 6))
        self.format_var = tk.StringVar(master=self.root, value='MP4')
        self.format_combo = ttk.Combobox(buttons, textvariable=self.format_var,
                                        values=('MP4', 'AVI'), state='readonly', width=7)
        self.format_combo.pack(side='left', padx=(0, 12))
        self.controls.append(self.format_combo)
        self.export_button = self._button(buttons, 'Exportar vídeo (1080p)…', self.make_export)
        self.export_button.configure(style='Export.TButton')
        self.export_button.pack(side='left', padx=(0, 8))
        secondary = ttk.Frame(render)
        secondary.pack(fill='x', pady=(8, 0))
        self.open_button = self._button(secondary, 'Abrir último vídeo', self.open_last)
        self.open_button.pack(side='left', padx=(0, 8))
        self.open_button.state(['disabled'])
        self.folder_button = self._button(secondary, 'Abrir pasta do vídeo', self.open_folder)
        self.folder_button.pack(side='left')
        self.folder_button.state(['disabled'])
        ttk.Label(render, text='Vídeo sem botões. Os arquivos originais permanecem intactos.').pack(
            anchor='w', pady=(8, 0))

        bottom = self.bottom = ttk.Frame(self.root, padding=(12, 8))
        bottom.grid(row=1, column=0, sticky='ew')
        self.status_var = tk.StringVar(master=self.root, value='Pronto. Adicione vídeos para começar.')
        status = ttk.Label(bottom, textvariable=self.status_var, wraplength=950)
        status.pack(anchor='w')
        bottom.bind('<Configure>', lambda event: status.configure(wraplength=max(200, event.width - 24)))
        progress_row = ttk.Frame(bottom)
        progress_row.pack(fill='x', pady=6)
        # Cancelar precisa funcionar enquanto action() bloqueia as demais ações.
        self.cancel_button = ttk.Button(progress_row, text='Cancelar', command=self.cancel_render,
                                        state='disabled')
        self.cancel_button.pack(side='right', padx=(10, 0))
        self.progress = ttk.Progressbar(progress_row, mode='indeterminate')
        self.progress.pack(side='left', fill='x', expand=True)

    def ensure_focus_visible(self, event):
        widget = event.widget
        if not str(widget).startswith(str(self.body) + '.'):
            return
        self.root.update_idletasks()
        canvas = self.viewport_canvas
        top = widget.winfo_rooty() - canvas.winfo_rooty()
        bottom = top + widget.winfo_height()
        if top < 0 or bottom > canvas.winfo_height():
            box = canvas.bbox('all')
            target = canvas.canvasy(0) + (top - 8 if top < 0 else bottom - canvas.winfo_height() + 8)
            canvas.yview_moveto(max(0, target) / max(1, box[3]))

    @classmethod
    def read_time(cls, minutes, seconds, label, *, hours=None):
        values = []
        fields = [('minutos', minutes, 59 if hours is not None else None),
                  ('segundos', seconds, 59)]
        if hours is not None:
            fields.insert(0, ('Hora', hours, None))
        try:
            for unit, variable, limit in fields:
                text = variable.get().strip()
                if not re.fullmatch(r'[0-9]+', text):
                    raise ErroProjeto(f'{label}: {unit} devem ser um inteiro positivo ou zero.')
                value = int(text)
                if limit is not None and value > limit:
                    raise ErroProjeto(f'{label}: {unit} devem estar entre 0 e 59.')
                values.append(value)
            value = values[-2] * 60 + values[-1]
            if hours is not None:
                value += values[0] * 3600
            if not math.isfinite(value):
                raise ErroProjeto(f'{label}: duração deve ser finita.')
        except (ValueError, OverflowError) as exc:
            raise ErroProjeto(f'{label}: duração inválida.') from exc
        return value

    @staticmethod
    def visible_seconds(value):
        # Somar .5 em float pode promover 0.49999999999999994 para 1.
        return math.floor(Fraction(str(value)) + Fraction(1, 2))

    @classmethod
    def split_time(cls, value, *, hours=False):
        minutes, seconds = divmod(cls.visible_seconds(value), 60)
        if hours:
            hour, minutes = divmod(minutes, 60)
            return str(hour), str(minutes), str(seconds)
        return str(minutes), str(seconds)

    @classmethod
    def set_time(cls, minutes, seconds, value, *, hours=None):
        parts = cls.split_time(value, hours=hours is not None)
        if hours is not None:
            hours.set(parts[0])
        m, s = parts[-2:]
        minutes.set(m)
        seconds.set(s)

    @classmethod
    def format_time(cls, value, *, hours=None):
        if hours is None:
            hours = value >= 3600
        return ':'.join(part.zfill(2) for part in cls.split_time(value, hours=hours))

    def capture(self, *, validate=False, skip_scene=False):
        if getattr(self, '_font_edited', set()):
            raise ErroProjeto('Há um tamanho de fonte pendente. Pressione Enter ou saia do campo para confirmar (1 a 200 px).')
        if self.duration_pending():
            raise ErroProjeto('Há uma duração pendente. Clique em Aplicar duração antes de continuar.')
        project = copy.deepcopy(self.project)
        project.title = self.title_var.get()
        if hasattr(self, 'alignment_var'):
            project.title_alignment = self.alignment_var.get()
        if hasattr(self, 'format_var'):
            project.export_format = self.format_var.get()
        project.colors = {key: value.get() for key, value in self.color_vars.items()}
        project.show_footer = self.footer_var.get()
        if self.editing_index is not None and not skip_scene:
            original = project.scenes[self.editing_index]
            original_start = Fraction(str(original.start))
            original_end = original_start + Fraction(str(original.duration))
            times, changed = [], []
            for key, label, actual in [('start', 'Início', original_start), ('end', 'Fim', original_end)]:
                value = self.read_time(getattr(self, f'scene_{key}_minutes_var'),
                                       getattr(self, f'scene_{key}_seconds_var'), label,
                                       hours=getattr(self, f'scene_{key}_hours_var'))
                edited = (value != self.visible_seconds(actual)
                          or key in getattr(self, '_scene_edited', set()))
                times.append(Fraction(value) if edited else actual)
                changed.append(edited)
            start, end = times
            # O último segundo arredondado representa o fim real da fonte,
            # sem pedir frações na interface nem permitir extrapolação.
            if changed[1] and end > original.source_duration and end == self.visible_seconds(original.source_duration):
                end = Fraction(str(original.source_duration))
            duration = float(end - start) if any(changed) else original.duration
            if float(end) > original.source_duration:
                raise ErroProjeto('O trecho de prévia ultrapassa o vídeo; reduza início/duração.')
            if duration < 1/30:
                raise ErroProjeto('O trecho de prévia deve ter pelo menos um frame; ajuste Início/Fim.')
            project.edit_scene(self.editing_index, self.scene_name_var.get(), float(start), duration)
        if validate:
            project.validate()
        return project

    def refresh(self, selected=0):
        self._refreshing = True
        try:
            self.title_var.set(self.project.title)
            if getattr(self, 'family_vars', None):
                self.family_vars['title'].set(families.label(self.project.title_font_family))
                self.family_combos['title'].state(['readonly', '!invalid'])
            if hasattr(self, 'title_font_var'):
                self.title_font_var.set(str(self.project.title_font_size if self.project.title_font_size is not None else 44))
                self._font_edited = set()
                self._font_errors = {}
                self.font_notice_var.set('')
                self.font_entries['title'].state(['!invalid'])
            if hasattr(self, 'alignment_var'):
                self.alignment_var.set(self.project.title_alignment)
            if hasattr(self, 'format_var'):
                self.format_var.set(self.project.export_format)
            self.duration_mode_var.set(self.project.duration_mode)
            self.set_time(self.duration_minutes_var, self.duration_seconds_var, self.project.effective_duration())
            self._duration_edited = False
            self.footer_var.set(self.project.show_footer)
            for key, variable in self.color_vars.items():
                variable.set(self.project.colors[key])
                self.color_swatches[key].configure(background=safe_color(self.project.colors.get(key), key))
            self.path_var.set(f'Projeto: {self.project_path}' if self.project_path else 'Projeto ainda não salvo.')
            music = self.project.music
            self.music_var.set(f'{music.source} · {self.format_time(music.duration)}' if music else 'Sem música; menu sem áudio.')
            children = self.tree.get_children()
            if children:
                self.tree.delete(*children)
            for i, scene in enumerate(self.project.scenes):
                self.tree.insert('', 'end', iid=str(i), values=(i+1, scene.name, scene.source.name,
                    *(self.format_time(value, hours=scene.source_duration >= 3600)
                      for value in (scene.start, scene.duration, scene.source_duration))))
            self.editing_index = min(selected, len(self.project.scenes)-1) if self.project.scenes else None
            if self.editing_index is not None:
                self.tree.selection_set(str(self.editing_index))
            self.load_scene_fields()
            self.update_duration_mode()
            self.refresh_duration_hint()
            self.update_folder_button()
        finally:
            self._refreshing = False
            if hasattr(self, 'color_preview'):
                self.color_preview.request()

    def load_scene_fields(self):
        scene = self.project.scenes[self.editing_index] if self.editing_index is not None else None
        self._loading_scene = True
        try:
            self.scene_name_var.set(scene.name if scene else '')
            if getattr(self, 'family_vars', None):
                self.family_vars['scene'].set(families.label(scene.font_family if scene else None))
                self.family_combos['scene'].state(['readonly', '!disabled', '!invalid'] if scene else ['disabled', '!invalid'])
            if hasattr(self, 'scene_font_var'):
                self.scene_font_var.set(str(scene.font_size if scene and scene.font_size is not None else 24))
                self._font_edited.discard('scene')
                self.font_entries['scene'].state(['!disabled', '!invalid'] if scene else ['disabled', '!invalid'])
            self.set_time(self.scene_start_minutes_var, self.scene_start_seconds_var, scene.start if scene else 0,
                          hours=self.scene_start_hours_var)
            end = Fraction(str(scene.start)) + Fraction(str(scene.duration)) if scene else 0
            self.set_time(self.scene_end_minutes_var, self.scene_end_seconds_var, end,
                          hours=self.scene_end_hours_var)
            self.scene_path_var.set(str(scene.source) if scene else 'Selecione uma cena para editar.')
            if hasattr(self, 'scene_duration_var'):
                self.scene_duration_var.set(f'Duração do trecho aplicado: {self.format_time(scene.duration)}'
                                            if scene else 'Duração do trecho: —')
        finally:
            self._loading_scene = False
            self._scene_edited = set()

    def mark_scene_time_edited(self, key):
        if not getattr(self, '_refreshing', False) and not getattr(self, '_loading_scene', False):
            if not hasattr(self, '_scene_edited'):
                self._scene_edited = set()
            self._scene_edited.add(key)

    def mark_duration_edited(self, *args):
        if not getattr(self, '_refreshing', False):
            self._duration_edited = True

    def select_scene(self, event=None):
        if self._refreshing or not self.tree.selection():
            return
        target = int(self.tree.selection()[0])
        if target == self.editing_index:
            return
        try:
            self.project = self.capture()
        except ErroProjeto:
            if self.editing_index is not None:
                self.tree.selection_set(str(self.editing_index))
            raise
        self.refresh(target)

    def adopt(self, project):
        if hasattr(self, 'preview_fonts'):
            for identity in {project.title_font_family, *(s.font_family for s in project.scenes)} - {None}:
                self.preview_fonts.font(identity, size=-12)
        self.project = project
        self.refresh(self.editing_index or 0)

    def apply_scene(self):
        self.adopt(self.capture())
        self.status_var.set('Edição aplicada. Salvar o projeto é opcional.')

    def choose_videos(self):
        project = self.capture()
        paths = filedialog.askopenfilenames(title='Selecionar vídeos preparados (até 6)', parent=self.root,
            filetypes=[('Vídeos', '*.mp4 *.mov *.mkv *.avi *.m4v *.mts *.m2ts *.webm'), ('Todos', '*.*')])
        if not paths:
            return
        if len(project.scenes) + len(paths) > 6:
            raise ErroProjeto('O total deve ser de 1 a 6 vídeos.')
        def operation(progress):
            for path in paths:
                progress(f'Sondando vídeo: {Path(path).name}…')
                project.add_video(services.inspect_media(path))
            return project
        self.run_render(operation, self.adopt, analysis=True)

    def remove_scene(self):
        if self.editing_index is None:
            raise ErroProjeto('Selecione uma cena.')
        project = self.capture(skip_scene=True)
        project.remove_scene(self.editing_index)
        self.adopt(project)

    def move_scene(self, offset):
        if self.editing_index is None:
            raise ErroProjeto('Selecione uma cena.')
        project = self.capture()
        project.move_scene(self.editing_index, offset)
        self.project = project
        self.refresh(self.editing_index + offset)

    def choose_color(self, key):
        initial = self.color_vars[key].get()
        if re.fullmatch(r'0x[0-9a-fA-F]{6}', initial):
            initial = safe_color(initial, key)
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', initial):
            initial = '#ffffff'
        color = colorchooser.askcolor(color=initial, parent=self.root,
                                      title=f'Cor: {key}')[1]
        if color:
            self.color_vars[key].set(color)
            if hasattr(self, 'color_swatches'):
                self.color_swatches[key].configure(background=safe_color(color, key))
            if hasattr(self, 'color_preview'):
                self.color_preview.request()

    def choose_music(self):
        # A seleção não aplica nem descarta os campos ainda em edição.
        project = copy.deepcopy(self.project)
        path = filedialog.askopenfilename(title='Escolher música local', parent=self.root,
            filetypes=[('Áudio', '*.wav *.mp3 *.m4a *.aac *.flac *.ogg'), ('Todos', '*.*')])
        if not path:
            return
        def operation(progress):
            progress('Sondando música…')
            project.music = services.inspect_media(path, audio=True)
            return project
        def done(result):
            self.project.music = result.music
            if self.project.duration_mode == 'musica':
                self._refreshing = True
                try:
                    self.set_time(self.duration_minutes_var, self.duration_seconds_var,
                                  self.project.effective_duration())
                    self._duration_edited = False
                finally:
                    self._refreshing = False
            self.refresh_music_text()
            self.status_var.set('Música selecionada. A duração segue o modo escolhido.')
        self.run_render(operation, done, analysis=True)

    def refresh_music_text(self):
        music = self.project.music
        if hasattr(self, 'music_var'):
            self.music_var.set(f'{music.source} · {self.format_time(music.duration)}' if music
                               else 'Sem música; menu sem áudio.')
        self.refresh_duration_hint()

    def refresh_duration_hint(self):
        if not hasattr(self, 'duration_hint_var'):
            return
        if self.project.duration_mode == 'musica':
            hint = f'Seguindo a música: {self.format_time(self.project.effective_duration())}. Trocar a faixa atualiza a duração.'
        elif not getattr(self, 'project_path', None) and self.project.duration == 60:
            hint = 'Duração inicial: 1 minuto. Para usar a faixa inteira, escolha Usar duração da música.'
        else:
            hint = f'Duração aplicada: {self.format_time(self.project.duration)}. Para usar a faixa inteira, escolha Usar duração da música.'
        self.duration_hint_var.set(hint)

    def clear_music(self):
        project = self.capture()
        project.duration = project.effective_duration()
        project.music = None
        project.duration_mode = 'manual'
        self.adopt(project)

    def update_duration_mode(self, *, finish_edit=True):
        if self.duration_mode_var.get() == 'musica' and self.project.music is None:
            self.duration_mode_var.set('manual')
        for entry in self.duration_entries:
            entry.state(['!disabled'] if self.duration_mode_var.get() == 'manual' else ['disabled'])
        if not finish_edit or getattr(self, '_refreshing', False):
            return
        mode = self.duration_mode_var.get()
        if mode != self.project.duration_mode:
            self.project.duration_mode = mode
            self.set_time(self.duration_minutes_var, self.duration_seconds_var,
                          self.project.effective_duration())
            self._duration_edited = False
            self.status_var.set('Duração da faixa selecionada.' if mode == 'musica'
                                else 'Duração manual: edite e clique em Aplicar duração.')
        self.refresh_duration_hint()

    def duration_pending(self):
        if self.duration_mode_var.get() != self.project.duration_mode:
            return True
        return (self.project.duration_mode == 'manual' and
                (getattr(self, '_duration_edited', False) or
                 (self.duration_minutes_var.get().strip(), self.duration_seconds_var.get().strip())
                 != self.split_time(self.project.duration)))

    def finish_duration_edit(self):
        if getattr(self, '_refreshing', False) or self.duration_mode_var.get() != 'manual':
            return
        duration = (self.read_time(self.duration_minutes_var, self.duration_seconds_var, 'Duração do menu')
                    if self.duration_pending() else self.project.duration)
        if duration < 1/30:
            raise ErroProjeto('Duração do menu: informe pelo menos um segundo.')
        self.project.duration = duration
        self.set_time(self.duration_minutes_var, self.duration_seconds_var, duration)
        self._duration_edited = False
        self.status_var.set('Duração aplicada. Salvar o projeto é opcional.')
        self.refresh_duration_hint()
        music = self.project.music
        if music is None:
            return
        visible, track_visible = self.visible_seconds(duration), self.visible_seconds(music.duration)
        key = (visible, music.source, track_visible)
        if getattr(self, '_last_duration_notice', None) == key:
            return
        self._last_duration_notice = key
        if visible == track_visible:
            return
        message = ('O menu continuará sem som após o fim da música.' if visible > track_visible
                   else 'A música será cortada no final do menu.')
        messagebox.showinfo('Gerador de menus em vídeo', message, parent=self.root)

    def save_as(self, *, after=None):
        project = self.capture(validate=True)
        path = filedialog.asksaveasfilename(title='Salvar projeto como (arquivo novo)',
            parent=self.root, defaultextension='.menu', filetypes=[('Projeto de menu', '*.menu')],
            initialfile='projeto.menu', confirmoverwrite=False)
        if not path:
            return
        path = Path(path)
        def operation(progress):
            progress('Salvando projeto…')
            return services.save_project(project, path)
        def done(result):
            self.project_path = result
            self.saved_state = copy.deepcopy(project)
            self.adopt(project)
            self.status_var.set(f'Projeto salvo: {result}')
            if after:
                after()
        self.run_job(operation, done)

    def confirm_discard(self):
        try:
            dirty = self.capture() != self.saved_state
        except ErroProjeto:
            dirty = True
        return not dirty or messagebox.askyesno('Gerador de menus em vídeo',
            'Continuar e descartar as alterações não salvas deste projeto?', parent=self.root)

    def new_project(self):
        if self.confirm_discard():
            self.project_path = self.last_catalog = None
            self.project = Project()
            self.saved_state = copy.deepcopy(self.project)
            self.refresh()
            self.status_var.set('Novo projeto. Adicione vídeos para começar.')

    def choose_open(self):
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(title='Reabrir projeto animado', parent=self.root,
                                         filetypes=[('Projeto de menu', '*.menu'), ('Projetos anteriores', '*.json')])
        if not path:
            return
        path = Path(path)
        def operation(progress):
            progress('Abrindo projeto e verificando as mídias…')
            return services.open_project(path)
        def done(project):
            self.project_path = path
            self.last_catalog = None
            self.saved_state = copy.deepcopy(project)
            self.adopt(project)
            self.status_var.set('Projeto aberto.')
        self.run_render(operation, done, analysis=True)

    def confirm_duration(self, project):
        duration = project.effective_duration()
        visible = self.visible_seconds(duration)
        if visible <= 360:
            self._confirmed_duration = None
            return True
        if getattr(self, '_confirmed_duration', None) == visible:
            return True
        if not messagebox.askyesno('Gerador de menus em vídeo',
                f'O menu terá {self.format_time(duration)} e poderá levar mais tempo para gerar. Deseja continuar?',
                parent=self.root):
            return False
        self._confirmed_duration = visible
        return True

    def make_preview(self):
        project = self.capture(validate=True)
        if not self.confirm_duration(project):
            return
        def done(catalog):
            self.adopt(project)
            self.finished_render(catalog)
        self.run_render(lambda progress: services.preview_project(project, None, progress),
                     done)

    def make_export(self):
        project = self.capture(validate=True)
        extension = '.' + project.export_format.lower()
        path = filedialog.asksaveasfilename(title=f'Exportar menu {project.export_format}', parent=self.root,
            defaultextension=extension, filetypes=[(f'Vídeo {project.export_format}', '*' + extension)],
            initialfile='menu' + extension, confirmoverwrite=False)
        if not path:
            return
        output = Path(path)
        if (output.suffix.lower() != extension or output.name.endswith((' ', '.'))
                or re.search(r'[<>:"|?*\x00-\x1f]', output.name)
                or re.match(r'(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', output.name)):
            raise ErroProjeto(f'Informe um nome de arquivo {extension} sem caracteres reservados.')
        if not self.confirm_duration(project):
            return
        def done(catalog):
            self.adopt(project)
            self.finished_render(catalog)
        self.run_render(lambda progress: services.render_project(project, None, output,
                     profile='1080p', progress=progress), done)

    def finished_render(self, catalog):
        if not services.output_available(catalog):
            raise ErroProjeto('O vídeo gerado não está disponível. Exporte novamente.')
        self.last_catalog = catalog
        self.status_var.set('Vídeo conferido. Use “Abrir pasta do vídeo” para localizar o arquivo.')
        self.update_folder_button()

    def update_folder_button(self):
        enabled = not self.worker.busy and services.output_available(getattr(self, 'last_catalog', None))
        for name in ('folder_button', 'open_button'):
            if hasattr(self, name):
                getattr(self, name).state(['!disabled'] if enabled else ['disabled'])

    def open_folder(self):
        if self.worker.busy:
            return
        catalog = self.last_catalog
        if not services.output_available(catalog):
            self.update_folder_button()
            raise ErroProjeto('O vídeo não está disponível. Exporte um vídeo antes de abrir a pasta.')
        self.run_job(lambda progress: services.open_output_folder(catalog),
                     lambda result: self.status_var.set(f'Pasta do vídeo: {Path(catalog).parent}'))

    def open_last(self):
        if self.worker.busy:
            return
        if not services.output_available(self.last_catalog):
            self.update_folder_button()
            raise ErroProjeto('O vídeo não está disponível. Exporte um vídeo antes de abrir.')
        catalog = self.last_catalog
        def operation(progress):
            progress('Abrindo menu no player padrão…')
            services.open_player(catalog)
        self.run_job(operation, lambda result: self.status_var.set(f'Enviado ao player: {catalog}'))

    def set_busy(self, busy):
        for widget in self.controls:
            widget.state(['disabled'] if busy else ['!disabled'])
        self.update_folder_button()
        self.cancel_button.state(['!disabled'] if busy and self._cancel_token is not None
                                 and not self._cancel_requested else ['disabled'])
        if busy:
            self.progress.configure(mode='indeterminate', value=0)
            self.progress.start(12)
            self._progress_running = True
        else:
            self.progress.stop()
            self._progress_running = False
            # Restaurar controles não conclui a edição dos campos antigos:
            # o callback pode estar justamente substituindo o projeto.
            self.update_duration_mode(finish_edit=False)
            if hasattr(self, 'font_entries') and self.editing_index is None:
                self.font_entries['scene'].state(['disabled'])
            if getattr(self, 'family_combos', None) and self.editing_index is None:
                self.family_combos['scene'].state(['disabled'])

    def run_job(self, operation, on_done):
        self.worker.submit(operation)
        self._on_done = on_done
        self.set_busy(True)
        self.status_var.set('Iniciando trabalho…')

    def run_render(self, operation, on_done, *, analysis=False):
        if self.worker.busy:
            raise RuntimeError('Aguarde a operação atual terminar.')
        token = CancellationToken()
        def cancellable(progress):
            with cancellation_scope(token), progress_scope(progress):
                result = operation(progress)
                if analysis:
                    # Conclusão e clique Cancelar disputam o mesmo lock. Só o
                    # callback terminal aplica a seleção inteira na thread Tk.
                    token.commit(lambda: None)
                return result
        self._cancel_token = token
        self._cancel_requested = False
        self._analysis_job = analysis
        try:
            self.run_job(cancellable, on_done)
        except Exception:
            self._cancel_token = None
            raise

    def cancel_render(self):
        if self.worker.busy and self._cancel_token is not None and self._cancel_token.cancel():
            self._cancel_requested = True
            self.status_var.set('Cancelando…')
            self.cancel_button.state(['disabled'])

    def show_progress(self, event):
        self.status_var.set(str(event))
        fraction = event.fraction if isinstance(event, MediaProgress) else None
        if fraction is None:
            self.progress.configure(mode='indeterminate', value=0)
            if not getattr(self, '_progress_running', False):
                self.progress.start(12)
                self._progress_running = True
        else:
            self.progress.stop()
            self._progress_running = False
            self.progress.configure(mode='determinate', maximum=100, value=fraction * 100)

    def poll(self):
        try:
            self.update_folder_button()
            for event in self.worker.poll():
                if event.kind == 'progress':
                    if not self._cancel_requested:
                        self.show_progress(event.value)
                else:
                    self._cancel_token = None
                    self._cancel_requested = False
                    self.set_busy(False)
                    if event.kind == 'error':
                        self.show_error(event.value)
                    elif event.kind == 'cancelled':
                        self.status_var.set('Operação cancelada.' if getattr(self, '_analysis_job', False)
                                            else 'Geração cancelada.')
                    else:
                        self.status_var.set('Operação concluída.')
                        try:
                            self._on_done(event.value)
                        except (ErroProjeto, OSError, ValueError) as exc:
                            self.show_error(exc)
                    if self._close_after_job:
                        self._close_after_job = False
                        self.close()
        finally:
            if not self._closed:
                self._poll_token = self.root.after(100, self.poll)

    def close(self):
        if self.worker.busy:
            if self._close_after_job:
                return
            if self._cancel_token is None:
                messagebox.showinfo('Gerador de menus em vídeo',
                    'Aguarde o trabalho terminar antes de fechar. A janela continua respondendo.',
                    parent=self.root)
            elif messagebox.askyesno('Gerador de menus em vídeo',
                    'Cancelar a operação e fechar após a limpeza? Alterações não salvas serão confirmadas depois.',
                    parent=self.root):
                # O diálogo mantém um loop Tk aninhado; poll pode ter terminado
                # o job enquanto a confirmação estava aberta.
                if self.worker.busy:
                    self._close_after_job = True
                    self.cancel_render()
                else:
                    self.close()
            return
        if self.confirm_discard():
            self.shutdown()

    def shutdown(self):
        self._closed = True
        if hasattr(self, 'color_preview'):
            self.color_preview.destroy()
        if self._poll_token:
            self.root.after_cancel(self._poll_token)
            self._poll_token = None
        self.root.destroy()


def run_app(diagnostics):
    root = tk.Tk()
    diagnostics.attach_tk(root)
    App(root)
    root.mainloop()
    return 1 if diagnostics.failures else 0


def main():
    from windows_entry import main as bootstrap
    return bootstrap()
