"""Catálogo fechado de fontes aprovadas; recursos locais, nunca caminhos do JSON."""
import ctypes
import hashlib
import json
from pathlib import Path
import sys

FAMILIES = {'arimo': 'Arimo', 'montserrat': 'Montserrat', 'oswald': 'Oswald',
            'merriweather': 'Merriweather', 'playfairdisplay': 'Playfair Display',
            'cinzel': 'Cinzel', 'dancingscript': 'Dancing Script', 'lobster': 'Lobster'}
CURRENT = 'Atual (Arial no Windows)'
OPTIONS = (CURRENT, *FAMILIES.values())
MANIFEST_SHA256 = '3f7b34337bd197858072f05afac19d08c7efc57a7a5416a6db01eb7d29a3882b'


def _error(message):
    from menu_anisio import ErroProjeto
    return ErroProjeto(message)


def family_id(value, context='Fonte'):
    if value is None:
        return None
    if type(value) is not str or value not in FAMILIES:
        raise _error(f'{context}: família de fonte desconhecida; escolha uma opção do catálogo.')
    return value


def label(value):
    return CURRENT if family_id(value) is None else FAMILIES[value]


def from_label(value):
    if value == CURRENT:
        return None
    for identity, name in FAMILIES.items():
        if value == name:
            return identity
    raise _error('Fonte: família de fonte desconhecida; escolha uma opção do catálogo.')


def resource_root():
    return Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent


def _manifest(root):
    path = root / 'font_assets' / 'manifest.json'
    try:
        raw = path.read_bytes()
        if path.is_symlink() or hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
            raise ValueError('pin do catálogo divergente')
        return json.loads(raw)
    except (OSError, ValueError) as exc:
        raise _error(f'font_assets/manifest.json: catálogo ausente/corrompido; operação bloqueada ({exc}).') from exc


def audit_assets(root=None):
    root = Path(root) if root is not None else resource_root()
    data = _manifest(root)
    expected = {'manifest.json'}
    for row in data['families']:
        for item in row['files']:
            relative = item['path']; expected.add(relative)
            path = root / 'font_assets' / relative
            try:
                if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root.parent):
                    raise ValueError('link não permitido')
                raw = path.read_bytes()
                if len(raw) != item['size_bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
                    raise ValueError('tamanho/SHA256 divergente')
            except (OSError, ValueError) as exc:
                raise _error(f'font_assets/{relative}: asset ausente/corrompido; operação bloqueada ({exc}).') from exc
    actual = {p.relative_to(root / 'font_assets').as_posix() for p in (root / 'font_assets').rglob('*') if p.is_file()}
    if actual != expected:
        raise _error('font_assets: conteúdo fora do catálogo fixo; operação bloqueada.')
    return data


def font_path(identity):
    family_id(identity)
    if identity is None:
        return None
    root = resource_root()
    audit_assets(root)
    return root / 'font_assets' / identity / 'Regular.ttf'


def _windows_add_font():
    add = ctypes.WinDLL('gdi32', use_last_error=True).AddFontResourceExW
    add.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p]
    add.restype = ctypes.c_int
    return add


class PreviewFonts:
    """Registro FR_PRIVATE por processo e verificação efetiva a cada Font Tk."""
    def __init__(self, root, *, factory=None, available=None):
        from tkinter import font as tkfont
        self.root = root
        self.factory = factory or (lambda **kw: tkfont.Font(root=root, **kw))
        self.available = available or (lambda: tkfont.families(root))
        self.registered = {}
        self.records = {}

    def font(self, identity, **options):
        identity = family_id(identity)
        if identity is None:
            # Preserve a prévia Tk anterior; find_font rege o arquivo do vídeo.
            return self.factory(family='Segoe UI', **options)
        path = font_path(identity)
        if identity not in self.registered:
            if sys.platform == 'win32':
                if not _windows_add_font()(str(path), 0x10, None):  # FR_PRIVATE
                    raise _error(f'Fonte {identity}: AddFontResourceExW/FR_PRIVATE falhou; prévia bloqueada.')
            self.registered[identity] = path
        expected = FAMILIES[identity]
        available = self.available()
        if expected.casefold() not in {name.casefold() for name in available}:
            raise _error(f'Fonte {identity}: família {expected} não disponível no Tk após registro; fallback bloqueado.')
        font = self.factory(family=expected, **options)
        actual = font.actual('family')
        if actual.casefold() != expected.casefold():
            raise _error(f'Fonte {identity}: Tk retornou {actual!r}, esperado {expected!r}; fallback bloqueado.')
        self.records[identity] = {'id': identity, 'file': str(path),
                                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                  'requested': expected, 'actual': actual, 'available': True,
                                  'registration': 'FR_PRIVATE' if sys.platform == 'win32' else 'available-local'}
        return font
