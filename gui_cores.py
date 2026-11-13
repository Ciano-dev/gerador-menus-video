"""Modelo puro e adaptador Tk da miniatura; nunca acessa fontes de mídia."""
import math
import re
import tkinter as tk
from tkinter import font as tkfont

from menu_video import ErroProjeto, animated_layout, fit_text_pixels

DEFAULT_COLORS = {'fundo': '#101f33', 'texto': '#f3ead6', 'destaque': '#c7a362'}


def safe_color(value, key='fundo'):
    if isinstance(value, str) and re.fullmatch(r'0x[0-9a-fA-F]{6}', value):
        value = '#' + value[2:]
    return value if isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value) else DEFAULT_COLORS[key]


def draw_model(title, colors, names, width, height, *, title_alignment='esquerda', title_size=None, scene_sizes=None, title_family=None, scene_families=None):
    """Ajusta 1080p a uma caixa 16:9, sem validar/mutar o projeto."""
    try:
        width, height = float(width), float(height)
        if not all(math.isfinite(x) and x > 1 for x in (width, height)):
            raise ValueError
    except (ValueError, TypeError):
        width, height = 320, 180
    scale = min(width / 1920, height / 1080)
    palette = {key: safe_color((colors or {}).get(key), key) for key in DEFAULT_COLORS}
    names = list(names or [])[:6]
    illustrative = not names
    shown = names or ['Exemplo 1', 'Exemplo 2']
    items = []

    def add(kind, tag, x, y, w, h, fill, text='', size=24, manual=False):
        items.append({'kind': kind, 'tag': tag, 'box': tuple(v * scale for v in (x, y, x+w, y+h)),
                      'fill': fill, 'text': '' if text is None else str(text),
                      'size': max(1 if manual else 4, round(size * scale)),
                      'autofit': not manual, 'reference_size': size})

    add('rectangle', 'background', 0, 0, 1920, 1080, palette['fundo'])
    add('text', 'title', 88, 0, 1744, 198, palette['texto'], title,
        title_size if title_size is not None else 44, title_size is not None)
    items[-1]['font_family'] = title_family
    items[-1]['alignment'] = title_alignment
    items[-1]['vertical_center'] = 99 * scale
    items[-1]['fit_height'] = 122 * scale
    add('rectangle', 'line', 88, 198, 1744, max(2, 1 / scale), palette['destaque'])
    scene_sizes = list(scene_sizes or [])
    scene_families = list(scene_families or [])
    for i, (name, cell) in enumerate(zip(shown, animated_layout('1080p')), 1):
        selected_size = scene_sizes[i-1] if i <= len(scene_sizes) else None
        x, y, w, h, pad = (cell[k] for k in ('x', 'y', 'w', 'h', 'pad'))
        label_width = w - cell['label'] - (cell['label'] - pad - cell['vw'])
        add('rectangle', f'card-{i}', x, y, w, h, '#192c42')
        add('rectangle', f'video-{i}', x+pad, y+pad, cell['vw'], cell['vh'], '#000000')
        add('text', f'number-{i}', x+cell['label'], y+18, label_width, 38, palette['destaque'], f'{i:02d}', 32)
        add('text', f'name-{i}', x+cell['label'], y+66, label_width, h-66-pad, palette['texto'], name,
            selected_size if selected_size is not None else 24, selected_size is not None)
        items[-1]['font_family'] = scene_families[i-1] if i <= len(scene_families) else None
    return {'size': (1920 * scale, 1080 * scale), 'palette': palette,
            'names': names, 'illustrative': illustrative, 'items': items}


def fit_text(value, width, height, size, font_factory, *, autofit=True):
    """Mede somente com Tk e mantém textos longos dentro do card."""
    if not autofit:
        font = font_factory(size=-size, family='Segoe UI')
        def measure(text, pixels):
            lines = text.split('\n')
            return max(font.measure(line) for line in lines), len(lines) * font.metrics('linespace')
        wrapped, _ = fit_text_pixels(value, width, height, size, measure, autofit=False)
        return wrapped, font
    def wrap(font):
        lines, line = [], ''
        for char in value.replace('\r', ' ').replace('\t', ' '):
            if char == '\n':
                lines.append(line)
                line = ''
            elif line and font.measure(line + char) > width:
                lines.append(line)
                line = char
            else:
                line += char
        return lines + [line]
    for pixels in range(size, 3, -1):
        font = font_factory(size=-pixels, family='Segoe UI')
        lines = wrap(font)
        if len(lines) * font.metrics('linespace') <= height and all(font.measure(line) <= width for line in lines):
            return '\n'.join(lines), font
    font = font_factory(size=-4, family='Segoe UI')
    lines = wrap(font)
    count = max(0, int(height // font.metrics('linespace')))
    if not count:
        return '', font
    kept = lines[:count]
    if len(lines) > count:
        last = kept[-1]
        while last and font.measure(last + '…') > width:
            last = last[:-1]
        kept[-1] = last + '…' if font.measure('…') <= width else ''
    return '\n'.join(line if font.measure(line) <= width else '' for line in kept), font


class ColorPreview:
    def __init__(self, canvas, title, colors, names, *, font_factory=None, alignment=None, sizes=None, warning=None, families=None, preview_fonts=None):
        self.canvas, self.title, self.colors, self.names = canvas, title, colors, names
        self.alignment = alignment
        self.sizes, self.warning = sizes, warning
        self.families, self.preview_fonts = families, preview_fonts
        self.font_factory = font_factory or (lambda **kw: tkfont.Font(root=canvas, **kw))
        self.redraw_count, self.model = 0, None
        self._idle, self._destroyed, self._fonts = None, False, []
        self._traces = [(variable, variable.trace_add('write', self.request))
                        for variable in (title, *colors.values(), *((alignment,) if alignment is not None else ()))]
        self._binds = [(event, canvas.bind(event, callback, add='+')) for event, callback in
                       [('<Configure>', self.request), ('<Destroy>', self._on_destroy)]]
        self.request()

    def request(self, *args):
        if not self._destroyed and self._idle is None:
            self._idle = self.canvas.after_idle(self.redraw)

    def redraw(self):
        self._idle = None
        if self._destroyed:
            return
        def read(variable):
            try:
                return variable.get()
            except tk.TclError:
                return ''
        title_size, scene_sizes = self.sizes() if self.sizes else (None, None)
        title_family, scene_families = self.families() if self.families else (None, None)
        model = draw_model(read(self.title), {k: read(v) for k, v in self.colors.items()},
                           self.names(), self.canvas.winfo_width(), self.canvas.winfo_height(),
                           title_size=title_size, scene_sizes=scene_sizes,
                           title_family=title_family, scene_families=scene_families,
                           title_alignment=read(self.alignment) if self.alignment is not None else 'esquerda')
        self.canvas.delete('palette')
        self._fonts = []
        warnings = []
        for item in model['items']:
            x, y, right, bottom = item['box']
            options = {'fill': item['fill'], 'tags': ('palette', item['tag'])}
            if item['kind'] == 'rectangle':
                self.canvas.create_rectangle(x, y, right, bottom, outline='', **options)
            else:
                try:
                    font_factory = self.font_factory
                    identity = item.get('font_family')
                    if identity is not None:
                        def font_factory(*, family, **options):
                            return self.preview_fonts.font(identity, **options)
                    value, font = fit_text(item['text'], right-x, item.get('fit_height', bottom-y), item['size'], font_factory,
                                           autofit=item['autofit'])
                    item['overflow'] = False
                except ErroProjeto as exc:
                    item['overflow'] = True
                    label = 'Título' if item['tag'] == 'title' else f'Cena {item["tag"].split("-")[-1]}'
                    if identity is not None and 'não cabe' not in str(exc):
                        item['font_error'] = str(exc)
                        warnings.append(str(exc))
                    else:
                        warnings.append(f'{label} não cabe na prévia com {item["reference_size"]} px. '
                                        'Reduza o tamanho ou edite o texto; a exportação verificará as medidas reais.')
                    # Mark the reserved area; do not show a clipped or silently reduced text.
                    self.canvas.create_rectangle(x, y, right, bottom, outline=model['palette']['destaque'],
                                                 tags=('palette', f'overflow-{item["tag"]}'))
                    continue
                self._fonts.append(font)
                centered = item.get('alignment') == 'centralizado'
                drawn = self.canvas.create_text((x+right)/2 if centered else x, y, text=value, font=font,
                                        anchor='n' if centered else 'nw',
                                        justify='center' if centered else 'left', **options)
                if 'vertical_center' in item:
                    bbox = self.canvas.bbox(drawn)
                    if bbox:
                        self.canvas.move(drawn, 0, item['vertical_center'] - (bbox[1]+bbox[3])/2)
                        item['box'] = tuple(self.canvas.bbox(drawn))
        self.model = model
        self.redraw_count += 1
        if self.warning:
            self.warning(' '.join(warnings))

    def _on_destroy(self, event):
        if event.widget == self.canvas:
            self.destroy()

    def destroy(self):
        if self._destroyed:
            return
        self._destroyed = True
        if self._idle is not None:
            self.canvas.after_cancel(self._idle)
            self._idle = None
        for variable, token in self._traces:
            variable.trace_remove('write', token)
        for event, token in self._binds:
            self.canvas.unbind(event, token)
        self._traces.clear()
        self._binds.clear()
        self._fonts.clear()
