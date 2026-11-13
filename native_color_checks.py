"""Provas de Canvas Tk real e captura Windows; somente fixtures sintéticas de CI."""
from contextlib import contextmanager
import copy
import ctypes
import hashlib
from pathlib import Path
import re
import struct
import sys
import threading
import zlib

BASE_PALETTE = {'fundo': '#101f33', 'texto': '#f3ead6', 'destaque': '#c7a362'}
ALT_PALETTE = {'fundo': '#f0e4cc', 'texto': '#192030', 'destaque': '#a52a50'}


@contextmanager
def no_media_calls(counter):
    import gui_servicos as services
    import menu_anisio as motor
    saved = []
    def forbidden(*args, **kwargs):
        counter.append('forbidden-media-call')
        raise AssertionError('Redraw chamou serviço de mídia.')
    for module, names in [(services, ('inspect_media', 'preview_project', 'render_project', 'save_project', 'open_project')),
                           (motor, ('resolve_tool', 'probe_media', 'run_media', 'measure_text_pixels'))]:
        for name in names:
            saved.append((module, name, getattr(module, name)))
            setattr(module, name, forbidden)
    try:
        yield
    finally:
        for module, name, original in saved:
            setattr(module, name, original)


def bounds(widget):
    return [widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_width(), widget.winfo_height()]


def contains(outer, inner):
    x, y, w, h = outer
    a, b, c, d = inner
    return x <= a and y <= b and a+c <= x+w and b+d <= y+h


def show_panel(app):
    root, viewport = app.root, app.viewport_canvas
    root.update_idletasks()
    box = viewport.bbox('all')
    viewport.yview_moveto(max(0, (app.visual_group.winfo_y() - 12) / max(1, box[3])))
    root.update()


def canvas_items(canvas):
    """Leia geometria e opções dos itens desenhados, sem usar o model da miniatura."""
    items = []
    for item in canvas.find_withtag('palette'):
        tags = canvas.gettags(item)
        text = canvas.type(item) == 'text'
        items.append({'tag': next(tag for tag in tags if tag != 'palette'),
                      'fill': str(canvas.itemcget(item, 'fill')),
                      'text': str(canvas.itemcget(item, 'text')) if text else '',
                      'coords': canvas.coords(item), 'bbox': list(canvas.bbox(item) or []),
                      **({key: str(canvas.itemcget(item, key)) for key in ('anchor', 'justify')} if text else {})})
    return items


def validate_item_geometry(item, spec):
    """Contrato do desenho de gui_cores.ColorPreview, inclusive alinhamento Tk."""
    x, y, right, bottom = spec['box']
    if spec['kind'] == 'rectangle':
        coords = spec['box']
    else:
        centered = spec.get('alignment') == 'centralizado'
        assert item['anchor'] == ('n' if centered else 'nw')
        assert item['justify'] == ('center' if centered else 'left')
        if 'vertical_center' in spec:
            # Read the genuine drawn block bounds, never the old allocated region.
            a, b, c, d = item['bbox']
            assert all(__import__('math').isfinite(v) for v in (a, b, c, d))
            assert a <= c and b <= d and a >= x-2 and c <= right+2
            assert d-b <= spec['fit_height']+2 and b >= y-1 and d <= bottom+1
            assert abs((b+d)/2 - spec['vertical_center']) <= .51
            assert len(item['coords']) == 2
            assert abs(item['coords'][0] - ((x+right)/2 if centered else x)) < .01
            assert abs(item['coords'][1]-b) <= 1
            return
        coords = ((x + right) / 2 if centered else x, y)
    assert len(item['coords']) == len(coords)
    assert all(abs(a-b) < .01 for a, b in zip(item['coords'], coords))


def snapshot(app, phase, ticks):
    canvas = app.color_canvas
    items = canvas_items(canvas)
    panel, view, screen = bounds(app.visual_group), bounds(app.viewport_canvas), [0, 0, app.root.winfo_screenwidth(), app.root.winfo_screenheight()]
    return {'phase': phase, 'palette': {key: variable.get() for key, variable in app.color_vars.items()},
            'alignment': app.alignment_var.get(),
            'title': app.title_var.get(), 'names': [scene.name for scene in app.project.scenes],
            'canvas_bounds': bounds(canvas), 'panel_bounds': panel, 'viewport_bounds': view,
            'screen_bounds': screen, 'visible': bool(canvas.winfo_viewable()) and contains(view, panel) and contains(screen, panel),
            'redraw_count': app.color_preview.redraw_count, 'eventloop_ticks': ticks[0], 'items': items}


CAPTURE_METHOD = 'Win32.GDI.BitBlt'


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32),
                ('biHeight', ctypes.c_int32), ('biPlanes', ctypes.c_uint16),
                ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
                ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32),
                ('biYPelsPerMeter', ctypes.c_int32), ('biClrUsed', ctypes.c_uint32),
                ('biClrImportant', ctypes.c_uint32)]


class RGBQUAD(ctypes.Structure):
    _fields_ = [('rgbBlue', ctypes.c_uint8), ('rgbGreen', ctypes.c_uint8),
                ('rgbRed', ctypes.c_uint8), ('rgbReserved', ctypes.c_uint8)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', RGBQUAD * 1)]


def _win32_capture_api():
    # Win32 LONG/BOOL remain 32 bits even where the host C long is 64 bits.
    ptr, i32, u32 = ctypes.c_void_p, ctypes.c_int32, ctypes.c_uint32
    user = ctypes.WinDLL('user32', use_last_error=True)
    gdi = ctypes.WinDLL('gdi32', use_last_error=True)
    for library, signatures in (
        (user, {'GetAncestor': ([ptr, u32], ptr), 'SetForegroundWindow': ([ptr], i32),
                'GetForegroundWindow': ([], ptr), 'GetDC': ([ptr], ptr),
                'ReleaseDC': ([ptr, ptr], i32)}),
        (gdi, {'CreateCompatibleDC': ([ptr], ptr),
               'CreateCompatibleBitmap': ([ptr, i32, i32], ptr),
               'SelectObject': ([ptr, ptr], ptr),
               'BitBlt': ([ptr, i32, i32, i32, i32, ptr, i32, i32, u32], i32),
               'GetDIBits': ([ptr, ptr, u32, u32, ptr, ctypes.POINTER(BITMAPINFO), u32], i32),
               'DeleteObject': ([ptr], i32), 'DeleteDC': ([ptr], i32)})):
        for name, (arguments, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
    return user, gdi


def _encode_png(width, height, bgr):
    """Encode only a top-down 24-bit DIB; row padding is never image content."""
    if any(type(value) is not int or not 0 < value <= 0x7fffffff for value in (width, height)):
        raise ValueError('Dimensões PNG inválidas.')
    stride = (width * 3 + 3) & ~3
    if len(bgr) != stride * height:
        raise ValueError('Buffer BGR inválido.')
    rows = bytearray()
    for y in range(height):
        row = bgr[y * stride:y * stride + width * 3]
        rgb = bytearray(width * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = row[2::3], row[1::3], row[0::3]
        rows.append(0)  # PNG filter None, in the original top-down order.
        rows.extend(rgb)
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data +
                struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))
    return (b'\x89PNG\r\n\x1a\n' +
            chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def capture_panel(app, path, panel_bounds, *, window=False):
    """Capture only the real foreground Tk panel, on Tk's main thread."""
    if sys.platform != 'win32':
        raise RuntimeError('Captura nativa exige Windows.')
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('Captura exige thread principal Tk.')
    if (not isinstance(panel_bounds, (list, tuple)) or len(panel_bounds) != 4 or
            any(type(value) is not int or not -0x80000000 <= value <= 0x7fffffff
                for value in panel_bounds) or min(panel_bounds[2:]) <= 0):
        raise ValueError('Bounds do painel inválidos.')
    root = app.root
    root.lift()
    root.update()
    screen_bounds = [0, 0, root.winfo_screenwidth(), root.winfo_screenheight()]
    target = root if window else app.visual_group
    outers = (screen_bounds, bounds(root)) if window else (screen_bounds, bounds(root), bounds(app.viewport_canvas))
    if (list(panel_bounds) != bounds(target) or not target.winfo_viewable() or
            not all(contains(outer, panel_bounds) for outer in
                    outers)):
        raise ValueError('Bounds fora do painel visível/tela.')
    x, y, width, height = panel_bounds
    path = Path(path).absolute()
    user, gdi = _win32_capture_api()
    invalid_handles = (None, 0, -1, ctypes.c_void_p(-1).value)
    hwnd = user.GetAncestor(root.winfo_id(), 2)  # GA_ROOT, not Tk's inner HWND.
    if hwnd in invalid_handles:
        raise RuntimeError('GetAncestor falhou.')
    if not user.SetForegroundWindow(hwnd):
        raise RuntimeError('SetForegroundWindow falhou.')
    if user.GetForegroundWindow() != hwnd:
        raise RuntimeError('Janela Tk não está foreground.')
    screen = memory = bitmap = old = None
    selected = False
    try:
        screen = user.GetDC(None)
        if screen in invalid_handles:
            screen = None
            raise RuntimeError('GetDC falhou.')
        memory = gdi.CreateCompatibleDC(screen)
        if memory in invalid_handles:
            memory = None
            raise RuntimeError('CreateCompatibleDC falhou.')
        bitmap = gdi.CreateCompatibleBitmap(screen, width, height)
        if bitmap in invalid_handles:
            bitmap = None
            raise RuntimeError('CreateCompatibleBitmap falhou.')
        old = gdi.SelectObject(memory, bitmap)
        if old in invalid_handles:
            raise RuntimeError('SelectObject falhou.')
        selected = True
        if user.GetForegroundWindow() != hwnd:
            raise RuntimeError('Janela Tk perdeu foreground antes de BitBlt.')
        if not gdi.BitBlt(memory, 0, 0, width, height, screen, x, y, 0x00CC0020):
            raise RuntimeError('BitBlt falhou.')
        if user.GetForegroundWindow() != hwnd:
            raise RuntimeError('Janela Tk perdeu foreground durante BitBlt.')
        if gdi.SelectObject(memory, old) in invalid_handles:
            raise RuntimeError('SelectObject restore falhou.')
        selected = False  # GetDIBits requires the bitmap to be unselected.
        stride = (width * 3 + 3) & ~3
        pixels = ctypes.create_string_buffer(stride * height)
        info = BITMAPINFO()
        info.bmiHeader = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height,
                                        1, 24, 0, 0, 0, 0, 0, 0)
        if gdi.GetDIBits(screen, bitmap, 0, height, pixels, ctypes.byref(info), 0) != height:
            raise RuntimeError('GetDIBits falhou ou retornou linhas incompletas.')
        raw = _encode_png(width, height, pixels.raw)
        if path.write_bytes(raw) != len(raw):
            raise OSError('Gravação PNG incompleta.')
    finally:
        errors = []
        def cleanup(name, function, *args, handle=False):
            try:
                result = function(*args)
                failed = result in invalid_handles if handle else not result
                if failed:
                    errors.append(name)
                    return False
                return True
            except Exception:
                errors.append(name)
                return False
        if selected:
            selected = not cleanup('SelectObject restore', gdi.SelectObject, memory, old, handle=True)
        if selected and memory:
            # A failed restore must not leave a selected bitmap being deleted.
            for _ in range(2):
                if cleanup('DeleteDC', gdi.DeleteDC, memory):
                    memory = None
                    selected = False
                    break
        if bitmap and not selected:
            cleanup('DeleteObject', gdi.DeleteObject, bitmap)
        if memory and not selected:
            cleanup('DeleteDC', gdi.DeleteDC, memory)
        if screen:
            cleanup('ReleaseDC', user.ReleaseDC, None, screen)
        if errors and sys.exc_info()[0] is None:
            raise RuntimeError('Cleanup GDI falhou: ' + ', '.join(errors))
    return {'phase': path.stem.rsplit('-', 1)[-1], 'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
            'width': width, 'height': height, 'method': CAPTURE_METHOD}


def probe_color_preview(app, capture_prefix=None):
    """Aciona os callbacks existentes e lê os itens efetivos, nunca um mock Canvas."""
    from gui_autoria import Project, Scene
    from tkinter import colorchooser
    source = app.project.scenes[0].source if app.project.scenes else Path('sintetica.mp4')
    seed = Project(title='Cores do menu – ação', colors=dict(BASE_PALETTE),
                   scenes=[Scene(source, 12, f'Cena sintética {i}', duration=3) for i in range(1, 7)])
    original_project, saved = app.project, copy.deepcopy(app.saved_state)
    original_index = app.editing_index
    previous_chooser = colorchooser.askcolor
    calls, ticks, captures, probes = [], [0], [], []
    mutation_checks = []
    try:
        if capture_prefix:
            width = min(app.root.winfo_width(), app.root.winfo_screenwidth() - 40)
            height = min(app.root.winfo_height(), app.root.winfo_screenheight() - 40)
            app.root.geometry(f'{width}x{height}+20+20')
        app.adopt(seed)
        show_panel(app)
        def pump():
            app.root.after_idle(lambda: ticks.__setitem__(0, ticks[0] + 1))
            app.root.update()
        with no_media_calls(calls):
            pump()
            probes.append(snapshot(app, 'before', ticks))
        if capture_prefix:
            captures.append(capture_panel(app, str(capture_prefix) + '-before.png', probes[-1]['panel_bounds']))
        # Duração em edição deve continuar pendente e intocada pelo redraw.
        app.duration_seconds_var.set('17')
        preserved = (copy.deepcopy(app.project), copy.deepcopy(app.saved_state), app.last_catalog,
                     app.editing_index, app.tree.selection(), app.duration_seconds_var.get())
        with no_media_calls(calls):
            for key, color in ALT_PALETTE.items():
                colorchooser.askcolor = lambda **kw: ((0, 0, 0), color)
                app.choose_color(key)
                pump()
                effective = str(app.color_canvas.itemcget('background' if key == 'fundo' else 'title' if key == 'texto' else 'line', 'fill'))
                if effective.lower() != color:
                    raise AssertionError(f'Confirmação não atualizou {key}.')
            colorchooser.askcolor = lambda **kw: (None, None)
            app.choose_color('fundo')
            if app.color_vars['fundo'].get() != ALT_PALETTE['fundo']:
                raise AssertionError('Cancelar seletor mudou a cor.')
            app.title_var.set('Cores alternadas – ação')
            pump()
            if preserved != (app.project, app.saved_state, app.last_catalog, app.editing_index,
                             app.tree.selection(), app.duration_seconds_var.get()) or not app.duration_pending():
                raise AssertionError('Redraw alterou estado de autoria/duração/seleção.')
            probes.append(snapshot(app, 'after', ticks))
        if capture_prefix:
            captures.append(capture_panel(app, str(capture_prefix) + '-after.png', probes[-1]['panel_bounds']))
        with no_media_calls(calls):
            app.refresh()
            pump()
            app.scene_name_var.set('Cena editada')
            app.apply_scene()
            pump()
            mutation_checks.append(app.color_preview.model['names'][0] == 'Cena editada')
            app.move_scene(1)
            pump()
            mutation_checks.append(app.color_preview.model['names'][1] == 'Cena editada')
            app.remove_scene()
            pump()
            mutation_checks.append(len(app.color_preview.model['names']) == 5)
            added = copy.deepcopy(app.project)
            added.scenes.append(Scene(source, 12, 'Cena adicionada', duration=3))
            app.adopt(added)
            pump()
            mutation_checks.append(app.color_preview.model['names'][-1] == 'Cena adicionada')
            app.adopt(Project(title='', scenes=[]))
            pump()
            mutation_checks.append(app.color_preview.model['illustrative'] and not app.project.scenes)
            app.adopt(copy.deepcopy(seed))
            pump()
            mutation_checks.append(app.color_preview.model['palette'] == BASE_PALETTE)
        if not all(mutation_checks) or app.saved_state != saved:
            raise AssertionError('Mutação/restore da interface não refletiu no Canvas.')
        return {'schema_version': 1, 'probes': probes, 'captures': captures, 'media_calls': len(calls),
                'state_preserved': True, 'cancel_preserved': True, 'mutation_checks': mutation_checks}
    finally:
        colorchooser.askcolor = previous_chooser
        app.project = original_project
        app.refresh(original_index or 0)
        app.root.update()


def probe_font_sizes(app, capture_prefix=None):
    """Novo contrato de fonte pelo Tk real, reaproveitando captura e bloqueio de mídia."""
    from gui_autoria import Project, Scene
    from tkinter import font as tkfont
    original, selected = app.project, app.editing_index
    saved = copy.deepcopy(app.saved_state)
    source = original.scenes[0].source if original.scenes else Path('synthetic.mp4')
    seed = Project(title='Título', scenes=[Scene(source, 12, 'Cena'), Scene(source, 12, 'Outra')])
    calls, checks, captures = [], {}, []
    try:
        app.adopt(seed)
        # adopt preserva a seleção anterior; os eventos abaixo verificam a cena 0 do seed.
        app.refresh(0)
        show_panel(app)
        controls = [{'from': float(widget.cget('from')), 'to': float(widget.cget('to')),
                     'increment': float(widget.cget('increment')),
                     'editable': not widget.instate(['readonly']) and not widget.instate(['disabled'])}
                    for widget in (app.font_entries['title'], app.font_entries['scene'])]
        checks['legacy_defaults'] = (app.title_font_var.get(), app.scene_font_var.get()) == ('44', '24') and seed.title_font_size is None and seed.scenes[0].font_size is None
        with no_media_calls(calls):
            app.duration_seconds_var.set('17')
            app.scene_name_var.set('Nome pendente')
            app.scene_start_seconds_var.set('5')
            pending = (app.duration_seconds_var.get(), app.scene_name_var.get(), app.scene_start_seconds_var.get())
            title, scene = app.font_entries['title'], app.font_entries['scene']
            title.focus_force(); app.root.update()
            app.title_font_var.set('60')
            title.event_generate('<Return>'); app.root.update()
            checks['enter'] = seed.title_font_size == 60
            scene.focus_force(); app.root.update()
            scene.event_generate('<Up>'); app.root.update()
            checks['spin_up'] = seed.scenes[0].font_size == 25
            scene.event_generate('<Down>'); app.root.update()
            checks['spin_down'] = seed.scenes[0].font_size == 24
            before = copy.deepcopy(app.project)
            app.scene_font_var.set('')
            scene.event_generate('<Return>'); app.root.update()
            checks['invalid_preserved'] = (app.project == before and app.scene_font_var.get() == ''
                                          and bool(app.font_notice_var.get()))
            app.scene_font_var.set('31')
            title.focus_force(); app.root.update()
            checks['focusout'] = seed.scenes[0].font_size == 31
            checks['pending_preserved'] = (pending == (app.duration_seconds_var.get(), app.scene_name_var.get(), app.scene_start_seconds_var.get())
                                          and seed.duration == 60 and seed.scenes[0].name == 'Cena'
                                          and seed.scenes[0].start == 0 and app.duration_pending()
                                          and app.saved_state == saved)
            checks['independence'] = seed.scenes[1].font_size is None
            canvas = app.color_canvas
            pixels = []
            for tag, reference in (('title', 60), ('name-1', 31)):
                font = tkfont.Font(root=app.root, name=str(canvas.itemcget(tag, 'font')), exists=True)
                pixels.append({'tag': tag, 'reference': reference, 'effective': abs(int(font.cget('size')))})
            canvas_size = [canvas.winfo_width(), canvas.winfo_height()]
        if capture_prefix:
            show_panel(app)
            captures.append(capture_panel(app, str(capture_prefix) + '-manual.png', bounds(app.visual_group)))
        with no_media_calls(calls):
            app.refresh()
            app.move_scene(1)
            moved = [s.font_size for s in app.project.scenes] == [None, 31]
            app.remove_scene()
            checks['reorder_remove'] = moved and len(app.project.scenes) == 1 and app.project.scenes[0].font_size is None
            overflow = Project(title='W' * 80, title_font_size=200,
                               scenes=[Scene(source, 12, 'W' * 80, font_size=200)])
            app.adopt(overflow); app.root.update()
            items = {item['tag']: item for item in app.color_preview.model['items']}
            checks['overflow'] = all(items[tag].get('overflow') and items[tag]['text'] == 'W' * 80
                                     for tag in ('title', 'name-1')) and 'não cabe' in app.font_preview_warning_var.get()
        if capture_prefix:
            show_panel(app)
            captures.append(capture_panel(app, str(capture_prefix) + '-overflow.png', bounds(app.visual_group)))
        report = {'schema_version': 1, 'controls': controls, 'checks': checks, 'media_calls': len(calls),
                  'pixels': pixels, 'canvas_size': canvas_size, 'captures': captures}
        validate_font_report(report, require_captures=bool(capture_prefix))
        return report
    finally:
        app.project = original
        app.refresh(selected or 0)
        app.root.update()


def validate_font_report(data, *, require_captures=True):
    check_path = None
    try:
        assert data['schema_version'] == 1 and data['media_calls'] == 0
        assert len(data['controls']) == 2
        for control in data['controls']:
            assert (control['from'], control['to'], control['increment']) == (1, 200, 1)
            assert control['editable'] is True
        for key in ('legacy_defaults', 'spin_up', 'spin_down', 'enter', 'focusout', 'invalid_preserved',
                    'pending_preserved', 'independence', 'reorder_remove', 'overflow'):
            check_path = f'checks.{key}'
            assert data['checks'][key] is True
        check_path = None
        width, height = data['canvas_size']
        assert width > 1 and height > 1
        scale = min(width/1920, height/1080)
        assert len(data['pixels']) == 2
        for item, tag, reference in zip(data['pixels'], ('title', 'name-1'), (60, 31)):
            assert item['tag'] == tag and item['reference'] == reference
            assert item['effective'] == max(1, round(reference * scale))
        if require_captures:
            assert len(data['captures']) == 2
            for capture, phase in zip(data['captures'], ('manual', 'overflow')):
                assert capture['phase'] == phase and capture['method'] == CAPTURE_METHOD
                assert capture['width'] > 0 and capture['height'] > 0
                assert re.fullmatch('[0-9a-f]{64}', capture['sha256']) and capture['path'].endswith('.png')
            assert data['captures'][0]['sha256'] != data['captures'][1]['sha256']
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        detail = f' Check: {check_path}.' if check_path else ''
        raise ValueError('Evidência nativa de tamanho de fonte incompleta ou inválida.' + detail) from exc


def validate_color_report(data, *, require_captures=True):
    from gui_cores import draw_model
    try:
        assert data['schema_version'] == 1 and data['media_calls'] == 0
        assert data['state_preserved'] is True and data['cancel_preserved'] is True
        assert len(data['mutation_checks']) == 6 and all(x is True for x in data['mutation_checks'])
        probes = data['probes']
        assert len(probes) == 2
        for probe, phase, palette in zip(probes, ('before', 'after'), (BASE_PALETTE, ALT_PALETTE)):
            assert probe['phase'] == phase and probe['palette'] == palette and probe['visible'] is True
            assert len(probe['names']) == 6 and probe['eventloop_ticks'] > 0 and probe['redraw_count'] > 0
            assert contains(probe['viewport_bounds'], probe['panel_bounds'])
            assert contains(probe['screen_bounds'], probe['panel_bounds'])
            for outer in ('panel_bounds', 'viewport_bounds', 'screen_bounds'):
                assert contains(probe[outer], probe['canvas_bounds'])
            width, height = probe['canvas_bounds'][2:]
            assert width >= 200 and abs(width / height - 16 / 9) < .02
            items = {item['tag']: item for item in probe['items']}
            assert len(items) == len(probe['items']) == 27
            assert probe['alignment'] in ('esquerda', 'centralizado')
            expected = draw_model(probe['title'], palette, probe['names'], width, height,
                                  title_alignment=probe['alignment'])
            for spec in expected['items']:
                item = items[spec['tag']]
                validate_item_geometry(item, spec)
                if spec['kind'] == 'text' and item['text']:
                    x, y, right, bottom = item['bbox']
                    a, b, c, d = spec['box']
                    assert x >= a-2 and y >= b-2 and right <= c+2 and bottom <= d+2
            assert items['background']['fill'].lower() == palette['fundo']
            assert items['title']['fill'].lower() == palette['texto'] and items['title']['text'] == probe['title']
            assert items['line']['fill'].lower() == palette['destaque']
            for i, name in enumerate(probe['names'], 1):
                assert items[f'card-{i}']['fill'].lower() == '#192c42'
                assert items[f'video-{i}']['fill'].lower() == '#000000'
                assert items[f'name-{i}']['fill'].lower() == palette['texto']
                assert items[f'name-{i}']['text'].replace('\n', '') == name
                assert items[f'number-{i}']['fill'].lower() == palette['destaque']
                assert items[f'number-{i}']['text'] == f'{i:02d}'
        assert probes[1]['redraw_count'] > probes[0]['redraw_count']
        assert probes[1]['eventloop_ticks'] > probes[0]['eventloop_ticks']
        if require_captures:
            assert len(data['captures']) == 2
            for capture, probe in zip(data['captures'], probes):
                assert capture['phase'] == probe['phase'] and capture['method'] == CAPTURE_METHOD
                assert (capture['width'], capture['height']) == tuple(probe['panel_bounds'][2:])
                assert re.fullmatch('[0-9a-f]{64}', capture['sha256']) and capture['path'].lower().endswith('.png')
            assert data['captures'][0]['sha256'] != data['captures'][1]['sha256']
    except (AssertionError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError('Evidência nativa da miniatura ausente ou inválida.') from exc


FAMILY_CHECKS = ('legacy_defaults', 'all_nine_actual', 'selected_event', 'enter_event', 'focus',
                 'pending_preserved', 'invalid_prepared', 'invalid_preserved', 'sizes_preserved', 'independence',
                 'persisted', 'current_restored', 'ui_numbers_preserved', 'combos_fit', 'reorder_remove', 'preview_roles')


def validate_family_report(data, *, require_captures=True):
    import font_families as fonts
    def require(condition, key):
        if not condition:
            raise ValueError(f'Evidência de famílias inválida: {key}.')
    try:
        require(data['schema_version'] == 1 and data['success'] is True and data['errors'] == [], 'success')
        require(data['manifest_sha256'] == fonts.MANIFEST_SHA256, 'manifest.sha256')
        require(type(data['media_calls']) is int and data['media_calls'] == 0, 'media_calls')
        for key in FAMILY_CHECKS:
            require(data['checks'].get(key) is True, 'checks.' + key)
        rows = data['families']
        require([row['id'] for row in rows] == [None, *fonts.FAMILIES], 'families.ids')
        pins = {row['id']: row['files'][0]['sha256'] for row in fonts.audit_assets()['families']}
        for row in rows:
            identity = row['id']
            expected = fonts.FAMILIES[identity] if identity else row['expected']
            require(bool(expected) and row['actual'] == expected, f'families.{identity}.actual')
            require(bool(row['file']) and re.fullmatch('[0-9a-f]{64}', row['sha256']), f'families.{identity}.file')
            if identity:
                require(row['sha256'] == pins[identity] and Path(row['file']).name == 'Regular.ttf', f'families.{identity}.sha256')
        require(data['persisted'] == {'title': 'cinzel', 'scenes': ['arimo', 'lobster']}, 'persisted')
        scale = min(data['canvas_size'][0]/1920, data['canvas_size'][1]/1080)
        require(len(data['manual_fonts']) == 2, 'manual_fonts.count')
        for row, tag, family, reference in zip(data['manual_fonts'], ('title', 'name-2'), ('Cinzel', 'Lobster'), (60, 31)):
            require(row['tag'] == tag and row['actual'] == family and row['reference'] == reference
                    and row['effective'] == max(1, round(reference*scale)), f'manual_fonts.{tag}')
        require(len(data['controls']) == 2, 'controls.count')
        for row, scope in zip(data['controls'], ('title', 'scene')):
            require(row['scope'] == scope and row['values'] == list(fonts.OPTIONS) and row['readonly'] is True, f'controls.{scope}.readonly')
            require(contains(row['parent'], row['bounds']) and row['bounds'][2] >= row['requested_width']
                    and 0 < row['text_width'] <= row['bounds'][2] - 15, f'controls.{scope}.fit')
        if require_captures:
            require(len(data['captures']) == 2, 'captures.count')
            for row, scope in zip(data['captures'], ('title', 'scene')):
                require(row['phase'] == scope and row['method'] == CAPTURE_METHOD and row['width'] > 0 and row['height'] > 0
                        and row['path'].endswith('.png') and re.fullmatch('[0-9a-f]{64}', row['sha256']), f'captures.{scope}')
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError(f'Evidência de famílias incompleta: {exc}.') from exc


def probe_font_families(app, directory, *, capture=False):
    """Tk real: 9 famílias, foco/eventos, campos pendentes e save/reopen reais.

    Relatório parcial sempre gravado em finally, inclusive falha de registro.
    Não fabrica fonte nativa e não substitui boundary APIs.
    """
    import json
    from tkinter import font as tkfont
    import font_families as fonts
    import gui_servicos as services
    import menu_anisio as motor
    from gui_autoria import Project, Scene
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    original, selected, saved = app.project, app.editing_index, app.saved_state
    source = original.scenes[0].source
    source_duration = original.scenes[0].source_duration
    seed = Project(title='Título', duration=min(.1, source_duration),
                   scenes=[Scene(source, source_duration, 'Cena', duration=min(.1, source_duration)),
                           Scene(source, source_duration, 'Outra', duration=min(.1, source_duration))])
    report = {'schema_version': 1, 'manifest_sha256': fonts.MANIFEST_SHA256, 'success': False, 'errors': [], 'checks': {}, 'families': [],
              'controls': [], 'captures': [], 'media_calls': 0, 'focus_events': [], 'invalid_states': {}}
    calls = []
    original_geometry = app.root.geometry() if capture else None
    def check(key, value, details=None):
        report['checks'][key] = bool(value)
        if not value:
            detail = ' ' + json.dumps(details, ensure_ascii=False, sort_keys=True) if details is not None else ''
            raise ValueError(f'checks.{key}: diagnóstico nativo de famílias falhou.{detail}')
    def focus(widget, target):
        widget.focus_force(); app.root.update()
        focused = app.root.focus_get()
        state = {'target': target, 'matches': focused == widget,
                 'focused_widget': str(focused) if focused is not None else None}
        report['focus_events'].append(state)
        check('focus', state['matches'], state)
    def edit_size(scope, value):
        widget = app.font_entries[scope]
        focus(widget, 'size.' + scope)
        getattr(app, scope + '_font_var').set(value)
        widget.event_generate('<Return>'); app.root.update()
    def invalid_state(phase, key):
        # Somente escalares da fixture: sem serializar widgets ou campos de projeto.
        notice = app.font_notice_var.get()
        state = {'entry': app.scene_font_var.get(), 'edited': 'scene' in app._font_edited,
                 'notice_present': bool(notice), 'notice_length': len(notice),
                 'widget_invalid': app.font_entries['scene'].instate(['invalid']),
                 'model_size': app.project.scenes[0].font_size}
        report['invalid_states'][phase] = state
        check(key, state['entry'] == '' and state['edited'] and state['notice_present']
              and state['widget_invalid'] and state['model_size'] is None, state)
    def actual(tag):
        canvas = app.color_canvas
        font = tkfont.Font(root=app.root, name=str(canvas.itemcget(tag, 'font')), exists=True)
        return font.actual('family')
    def choose(scope, identity, event='<<ComboboxSelected>>'):
        widget = app.family_combos[scope]
        focus(widget, 'family.' + scope)
        app.family_vars[scope].set(fonts.label(identity))
        widget.event_generate(event); app.root.update()
        value = app.project.title_font_family if scope == 'title' else app.project.scenes[app.editing_index].font_family
        if value != identity:
            raise ValueError(f'Fonte {identity}: seleção não aplicada; {app.status_var.get()}')
    try:
        if capture:
            app.root.update_idletasks()
            width = min(app.root.winfo_width(), app.root.winfo_screenwidth()-40)
            height = min(app.root.winfo_height(), app.root.winfo_screenheight()-80)
            app.root.geometry(f'{width}x{height}+20+20')
            app.root.update()
        app.adopt(seed); app.refresh(0); show_panel(app)
        with no_media_calls(calls):
            check('legacy_defaults', seed.title_font_family is None and all(s.font_family is None for s in seed.scenes))
            number_family, ui_font = actual('number-1'), app.ui_font
            legacy = actual('title')
            app.title_var.set('Título pendente')
            app.scene_name_var.set('Nome pendente'); app.scene_start_seconds_var.set('5')
            app.duration_seconds_var.set('17')
            edit_size('scene', '')
            invalid_state('before_families', 'invalid_prepared')
            pending = (app.title_var.get(), app.scene_name_var.get(), app.scene_start_seconds_var.get(), app.duration_seconds_var.get())
            for identity in (None, *fonts.FAMILIES):
                choose('title', identity)
                observed = actual('title')
                expected = fonts.FAMILIES[identity] if identity else legacy
                check('all_nine_actual', observed == expected)
                path = fonts.font_path(identity) if identity else motor.find_font(None)
                report['families'].append({'id': identity, 'actual': observed, 'expected': expected,
                    'file': str(path), 'sha256': motor.sha256(path),
                    'registration': app.preview_fonts.records.get(identity, {}).get('registration', 'legacy')})
            check('selected_event', seed.title_font_family == 'lobster')
            choose('title', 'cinzel', '<Return>')
            check('enter_event', seed.title_font_family == 'cinzel')
            choose('scene', 'arimo')
            invalid_state('after_families', 'invalid_preserved')
            check('pending_preserved', pending == (app.title_var.get(), app.scene_name_var.get(), app.scene_start_seconds_var.get(), app.duration_seconds_var.get())
                  and seed.title == 'Título' and seed.scenes[0].name == 'Cena' and seed.scenes[0].start == 0
                  and app.duration_pending() and app.saved_state is saved)
            check('sizes_preserved', seed.title_font_size is None and all(s.font_size is None for s in seed.scenes))
            check('independence', seed.scenes[0].font_family == 'arimo' and seed.scenes[1].font_family is None and seed.title_font_family == 'cinzel')
            check('ui_numbers_preserved', app.ui_font == ui_font and actual('number-1') == number_family)
            choose('title', None)
            check('current_restored', actual('title') == legacy and seed.title_font_size is None)
            # Agora descarte deliberadamente os pendentes já comprovados para testar seleção/save.
            app.refresh(0)
            choose('title', 'cinzel'); choose('scene', 'arimo')
            app.tree.selection_set('1'); app.select_scene(); app.root.update()
            choose('scene', 'lobster', '<Return>')
            edit_size('title', '60')
            edit_size('scene', '31')
        check('preview_roles', actual('title') == 'Cinzel' and actual('name-1') == 'Arimo' and actual('name-2') == 'Lobster')
        report['canvas_size'] = [app.color_canvas.winfo_width(), app.color_canvas.winfo_height()]
        report['manual_fonts'] = []
        for tag, reference in (('title', 60), ('name-2', 31)):
            font = tkfont.Font(root=app.root, name=str(app.color_canvas.itemcget(tag, 'font')), exists=True)
            report['manual_fonts'].append({'tag': tag, 'actual': font.actual('family'), 'reference': reference,
                                           'effective': abs(int(font.cget('size')))})
        path = services.save_project(app.project, directory / 'families.menu')
        reopened = services.open_project(path)
        check('persisted', reopened == app.project)
        report['persisted'] = {'title': reopened.title_font_family, 'scenes': [s.font_family for s in reopened.scenes]}
        for scope, target in (('title', app.visual_group), ('scene', app.scene_editor)):
            widget = app.family_combos[scope]
            widget.focus_force(); app.root.update()
            viewport = app.viewport_canvas
            top = target.winfo_rooty() - viewport.winfo_rooty() + viewport.canvasy(0)
            viewport.yview_moveto(max(0, top-6) / max(1, viewport.bbox('all')[3])); app.root.update()
            ui = tkfont.Font(root=app.root, font=app.ui_font)
            control = {'scope': scope, 'values': list(widget.cget('values')), 'readonly': widget.instate(['readonly']),
                       'bounds': bounds(widget), 'parent': bounds(widget.master), 'requested_width': widget.winfo_reqwidth(),
                       'text_width': max(ui.measure(value) for value in fonts.OPTIONS), 'viewport': bounds(viewport)}
            report['controls'].append(control)
            check('combos_fit', contains(control['parent'], control['bounds']) and contains(control['viewport'], control['bounds'])
                  and control['bounds'][2] >= control['requested_width'] and control['text_width'] <= control['bounds'][2]-15)
            if capture:
                shot = capture_panel(app, directory / ('manual-'+scope+'.png'), bounds(app.root), window=True)
                report['captures'].append(shot)
        with no_media_calls(calls):
            app.move_scene(-1)
            moved = [s.font_family for s in app.project.scenes] == ['lobster', 'arimo']
            app.remove_scene()
            check('reorder_remove', moved and [s.font_family for s in app.project.scenes] == ['arimo'])
        report['media_calls'] = len(calls)
        report['success'] = True
        validate_family_report(report, require_captures=capture)
        return report
    except Exception as exc:
        report['success'] = False
        report['errors'].append(f'{type(exc).__name__}: {exc}')
        raise
    finally:
        report['media_calls'] = len(calls)
        app.last_family_report = report
        (directory / 'font-families.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        app.project, app.saved_state = original, saved
        if original_geometry is not None:
            app.root.geometry(original_geometry)
        app.refresh(selected or 0)
        if selected is None:
            app.editing_index = None
            app.tree.selection_remove(*app.tree.selection())
            app.load_scene_fields()
        app.root.update()
