# Windows x64 only. Onedir, windowed, no UPX; Python's standard Tk hook collects Tcl/Tk.
import platform
from pathlib import Path
import sys

if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
    raise RuntimeError('Build exige runner Windows x64; cross-build Linux não suportado.')

root = Path(SPECPATH).resolve().parent
vendor = root / '.vendor'
sys.path.insert(0, str(root))
from font_families import audit_assets
font_manifest = audit_assets(root)  # Missing/tampered TTF, OFL or catalog blocks Analysis.
font_datas = [(str(root / 'font_assets' / item['path']),
               'font_assets/' + str(Path(item['path']).parent).replace('\\', '/'))
              for row in font_manifest['families'] for item in row['files']]
font_datas.append((str(root / 'font_assets' / 'manifest.json'), 'font_assets'))
a = Analysis(
    [str(root / 'windows_entry.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(vendor / 'notices'), 'notices'),
           (str(root / 'windows_build' / 'client-download-manifest.json'), 'windows_build'),
           (str(root / 'README.md'), '.'), (str(root / 'BUILD-WINDOWS.md'), '.')] + font_datas,
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['licensing', 'license_native', 'windows_build.ci_activation', 'windows_build.pipeline', 'tests'], noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name='Sistema-Menu-Anisio', debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    contents_directory='_internal',
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Sistema-Menu-Anisio')
