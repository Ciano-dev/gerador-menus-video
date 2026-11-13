"""Gate Tk real da etapa 2; screenshots somente por GDI Windows de janela visível."""
import copy
from collections import Counter
import json
from pathlib import Path
import re
import sys
import time

from native_color_checks import (bounds, contains, capture_panel, CAPTURE_METHOD,
                                 canvas_items, validate_item_geometry)


BUTTON_LABELS = ('Novo projeto', 'Abrir projeto…', 'Salvar projeto como…',
    'Adicionar vídeos…', 'Remover', 'Subir', 'Descer', 'Aplicar nome e trecho',
    *('Escolher cor…',)*3, 'Escolher música…', 'Retirar música', 'Aplicar duração',
    'Exportar vídeo (1080p)…', 'Abrir último vídeo', 'Abrir pasta do vídeo', 'Cancelar')


def validate_interface_report(data, *, require_captures=True):
    from gui_cores import draw_model
    try:
        assert data['schema_version'] == 1
        rows = data['cases']
        assert len(rows) == 4
        assert {(r['scale'], tuple(r['size'])) for r in rows} == {
            (s, (w, 768)) for s in (100, 150) for w in (1024, 1366)}
        for row in rows:
            assert row['scene_headings'] == ['Nome da cena', 'Arquivo de vídeo']
            measures = row['button_measures']
            assert Counter(m['text'] for m in measures) == Counter(BUTTON_LABELS)
            window = row['button_window']
            assert window[2:] == row['size']
            for measured in measures:
                rect = measured['bounds']
                requested = measured['requested_size']
                assert len(requested) == 2 and all(v > 0 for v in requested)
                assert rect[2] >= requested[0] and rect[3] >= requested[1]
                assert 0 < measured['text_width'] <= rect[2]
                assert contains(measured['parent'], rect)
                assert window[0] <= rect[0] and rect[0]+rect[2] <= window[0]+window[2]
            assert row['new_manual_seconds'] == 60 and row['legacy_seconds'] == 15
            assert row['music_manual_seconds'] == 37.25 and row['music_mode'] == 'musica'
            assert row['format_values'] == ['MP4', 'AVI'] and row['format_readonly'] is True
            assert row['alignment_values'] == ['esquerda', 'centralizado']
            assert len(row['alignment_probes']) == 2
            for probe, alignment in zip(row['alignment_probes'], row['alignment_values']):
                assert probe['alignment'] == alignment
                width, height = probe['canvas_size']
                assert width >= 200 and abs(width / height - 16 / 9) < .02
                model = draw_model('', {}, [], width, height, title_alignment=alignment)
                title = next(item for item in model['items'] if item['tag'] == 'title')
                validate_item_geometry(probe['title'], title)
            assert row['selection_preserved'] and row['pending_preserved'] and row['scrollable']
            assert row['time_columns_aligned'] and row['external_disabled'] and row['disabled_legible']
            assert row['background'] == '#252525' and row['field'] == '#1e1e1e'
            assert row['focus'] == '#78b8ed'
            assert abs(row['tk_scaling'] - 4/3 * row['scale']/100) < .04
            assert len(row['views']) == 4
            assert {v['group'] for v in row['views']} == {'scenes', 'visual', 'audio', 'export'}
            for view in row['views']:
                assert view['visible'] and contains(view['window'], view['viewport'])
                assert contains(view['viewport'], view['target']) and contains(view['window'], view['cancel'])
                assert contains(view['window'], view['progress']) and contains(view['window'], view['status'])
                assert view['controls'] and all(contains(view['viewport'], control) for control in view['controls'])
                if require_captures:
                    shot = view['capture']
                    assert shot['method'] == CAPTURE_METHOD
                    assert (shot['width'], shot['height']) == tuple(row['size'])
                    assert re.fullmatch('[0-9a-f]{64}', shot['sha256']) and shot['path'].endswith('.png')
    except (AssertionError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError('Evidência nativa de interface/rolagem/DPI incompleta ou inválida.') from exc


def probe_interface(app, directory, *, capture=False):
    """Cada escala nasce em um interpretador Tk próprio; não simula pixels/DPI do SO."""
    import tkinter as tk
    from tkinter import filedialog, ttk, font as tkfont
    from menu_gui import App, UI_COLORS
    from gui_autoria import Project
    import gui_servicos as services
    directory = Path(directory)
    directory.mkdir()
    rows = []
    original = app.root
    original.withdraw()
    try:
        for scale in (100, 150):
            for width in (1024, 1366):
                root = tk.Tk()
                candidate = None
                try:
                    root.tk.call('tk', 'scaling', 4/3 * scale/100)
                    candidate = App(root)
                    errors = []
                    candidate.show_error = lambda error: errors.append(str(error))
                    root.report_callback_exception = lambda kind, error, tb: errors.append(str(error))
                    root.geometry(f'{width}x768+0+0')
                    root.update()
                    if (root.winfo_width(), root.winfo_height()) != (width, 768):
                        raise ValueError('O desktop nativo não comporta a geometria solicitada.')
                    new_seconds = candidate.project.effective_duration()
                    seed = copy.deepcopy(app.project)
                    seed.duration_mode, seed.duration = 'manual', 15
                    seed.export_format, seed.title_alignment = 'MP4', 'esquerda'
                    legacy = services.project_data(seed, directory)
                    legacy.pop('formato'); legacy.pop('alinhamento_titulo')
                    path = directory / f'legacy-{scale}-{width}.menu'
                    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding='utf-8')
                    previous = filedialog.askopenfilename
                    try:
                        filedialog.askopenfilename = lambda **kwargs: str(path)
                        candidate.choose_open()
                        deadline = time.monotonic() + 15
                        while candidate.worker.busy and time.monotonic() < deadline:
                            root.update()
                            time.sleep(.005)
                        root.update()
                        if errors or candidate.worker.busy or candidate.project_path != path:
                            raise ValueError('Load nativo não concluiu pelo worker.')
                    finally:
                        filedialog.askopenfilename = previous
                    legacy_seconds = candidate.project.duration
                    seed.duration_mode, seed.duration = 'musica', 37.25
                    music_path = services.save_project(seed, directory / f'music-{scale}-{width}.menu')
                    loaded = services.open_project(music_path)
                    candidate.adopt(loaded)
                    music_seconds, music_mode = candidate.project.duration, candidate.project.duration_mode
                    candidate.duration_mode_var.set('manual'); candidate.update_duration_mode()
                    candidate.duration_seconds_var.set('17')
                    candidate.scene_name_var.set('Nome pendente')
                    selected = candidate.tree.selection()
                    # Usa o callback real, sondagem real no worker e nenhuma aplicação de campos.
                    previous = filedialog.askopenfilename
                    try:
                        filedialog.askopenfilename = lambda **kwargs: str(seed.music.source)
                        candidate.choose_music()
                        deadline = time.monotonic() + 15
                        while candidate.worker.busy and time.monotonic() < deadline:
                            root.update(); time.sleep(.005)
                        root.update()
                        if errors or candidate.worker.busy:
                            raise ValueError('Seleção nativa não concluiu.')
                    finally:
                        filedialog.askopenfilename = previous
                    pending = (candidate.duration_pending() and candidate.duration_seconds_var.get() == '17'
                               and candidate.scene_name_var.get() == 'Nome pendente' and candidate.project.duration == 37.25)
                    row = {'scale': scale, 'size': [width, 768], 'tk_scaling': float(root.tk.call('tk', 'scaling')),
                           'new_manual_seconds': new_seconds, 'legacy_seconds': legacy_seconds,
                           'music_manual_seconds': music_seconds, 'music_mode': music_mode,
                           'format_values': list(candidate.format_combo.cget('values')),
                           'format_readonly': candidate.format_combo.instate(['readonly']),
                           'alignment_values': [], 'alignment_probes': [],
                           'selection_preserved': candidate.tree.selection() == selected,
                           'pending_preserved': pending, 'scrollable': candidate.viewport_canvas.yview() != (0.0, 1.0),
                           'external_disabled': all(b.instate(['disabled']) for b in (candidate.open_button, candidate.folder_button)),
                           'background': candidate.style.lookup('TFrame', 'background'),
                           'field': candidate.style.lookup('TEntry', 'fieldbackground'),
                           'focus': candidate.style.lookup('TEntry', 'bordercolor', ('focus',)),
                           'disabled_legible': candidate.style.lookup('TEntry', 'foreground', ('disabled',)) == UI_COLORS['disabled'],
                           'views': []}
                    row['scene_headings'] = [candidate.tree.heading(key, 'text') for key in ('name', 'file')]
                    row['button_window'] = bounds(root)
                    row['button_measures'] = []
                    for button in [c for c in candidate.controls if isinstance(c, ttk.Button)] + [candidate.cancel_button]:
                        font = tkfont.Font(root=root, font=candidate.style.lookup(
                            button.cget('style') or 'TButton', 'font') or candidate.ui_font)
                        row['button_measures'].append({'text': str(button.cget('text')),
                            'bounds': bounds(button), 'parent': bounds(button.master),
                            'requested_size': [button.winfo_reqwidth(), button.winfo_reqheight()],
                            'text_width': font.measure(button.cget('text'))})
                    for alignment in ('esquerda', 'centralizado'):
                        candidate.alignment_var.set(alignment); root.update()
                        canvas = candidate.color_canvas
                        title, = (item for item in canvas_items(canvas) if item['tag'] == 'title')
                        row['alignment_probes'].append({
                            'alignment': alignment,
                            'canvas_size': [canvas.winfo_width(), canvas.winfo_height()], 'title': title})
                        row['alignment_values'].append(alignment)
                    row['time_columns_aligned'] = all(
                        candidate.scene_time_entries['start', unit].winfo_rooty() == candidate.scene_time_entries['end', unit].winfo_rooty()
                        and candidate.scene_time_entries['start', unit].winfo_width() == candidate.scene_time_entries['end', unit].winfo_width()
                        for unit in ('hours', 'minutes', 'seconds'))
                    for group, target in [('scenes', candidate.scene_editor), ('visual', candidate.visual_group),
                                          ('audio', candidate.audio_group), ('export', candidate.export_group)]:
                        canvas = candidate.viewport_canvas
                        root.update_idletasks()
                        top = target.winfo_rooty() - canvas.winfo_rooty() + canvas.canvasy(0)
                        canvas.yview_moveto(max(0, top-6) / max(1, canvas.bbox('all')[3]))
                        root.update()
                        view = {'group': group, 'window': bounds(root), 'viewport': bounds(canvas),
                                'target': bounds(target), 'cancel': bounds(candidate.cancel_button),
                                'progress': bounds(candidate.progress), 'status': bounds(candidate.bottom),
                                'visible': bool(target.winfo_viewable())}
                        controls = ([*candidate.scene_time_entries.values(), candidate.apply_scene_button]
                                    if group == 'scenes' else [target])
                        if group in ('scenes', 'visual'):
                            controls = [*controls, candidate.family_combos['scene' if group == 'scenes' else 'title']]
                        view['controls'] = [bounds(control) for control in controls]
                        if capture:
                            view['capture'] = capture_panel(candidate, directory / f'ui-{scale}-{width}-{group}.png', bounds(root), window=True)
                        row['views'].append(view)
                    rows.append(row)
                finally:
                    if candidate:
                        candidate.shutdown()
                    else:
                        root.destroy()
    finally:
        original.deiconify(); original.update()
    report = {'schema_version': 1, 'platform': sys.platform, 'cases': rows,
              'scaling_method': 'Tk scaling 96/144 DPI; independent interpreters; OS DPI acceptance still manual'}
    validate_interface_report(report, require_captures=capture)
    (directory / 'interface.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
