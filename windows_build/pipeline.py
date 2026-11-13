"""Disposable Windows runner helpers. No import/validation network; stdlib only."""
import argparse
import importlib.metadata
import hashlib
import json
from pathlib import Path
import shutil
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client_download import (HTTPSRedirect, digest, ensure_tools, load_manifest, require,
                             safe_members, validate_manifest)

from font_families import FAMILIES, MANIFEST_SHA256, audit_assets

FONT_FILES = ('font_assets/manifest.json', *(f'font_assets/{identity}/{name}'
    for identity in FAMILIES for name in ('Regular.ttf', 'OFL.txt')))

WHEEL_SHA256 = 'bc10eb1a787f99fea613509f55b902fbd2d8b73ff5f51ff245ea29a481d97d41'
TCL_TERMS_URL = 'https://raw.githubusercontent.com/python/cpython-bin-deps/5a51bb7b87efa119332c52b04a84c49d3042279c/amd64/tcllicense.terms'
TCL_TERMS_SHA256 = '41613eabfc08921a7da9c4bfd7f3ce5d5406f55214b253d89f392ed86eebeb8f'
TCL_TERMS_SIZE = 2295
REQUIRED_FILES = (
    'Gerador-de-Menus.exe', '_internal/windows_build/client-download-manifest.json',
    '_internal/README.md', '_internal/BUILD-WINDOWS.md',
    '_internal/notices/runtime/Python-LICENSE.txt',
    '_internal/notices/runtime/Tcl-license.terms', '_internal/notices/runtime/Tk-license.terms',
    *(f'_internal/{name}' for name in FONT_FILES))


def validate_manifest_file(path):
    audit_assets(Path(__file__).resolve().parents[1])
    return validate_manifest(json.loads(Path(path).read_text(encoding='utf-8')))


def fetch_tcl_terms(destination):
    """Exact pinned CRLF bytes; only the tiny license, never a native binary."""
    import hashlib
    try:
        with urllib.request.build_opener(HTTPSRedirect()).open(TCL_TERMS_URL, timeout=30) as stream:
            body = stream.read(TCL_TERMS_SIZE + 1)
    except Exception:
        raise ValueError('Não foi possível obter termos Tcl fixados; build bloqueado.') from None
    require(len(body) == TCL_TERMS_SIZE and hashlib.sha256(body).hexdigest() == TCL_TERMS_SHA256,
            'Tamanho/SHA256 dos termos Tcl divergente; build bloqueado.')
    destination.write_bytes(body)


def runtime_notices(directory):
    target = Path(directory) / 'notices' / 'runtime'
    target.mkdir(parents=True, exist_ok=True)
    base = Path(sys.base_prefix)
    for source, name in ((base / 'LICENSE.txt', 'Python-LICENSE.txt'),
                         (base / 'tcl' / 'tk8.6' / 'license.terms', 'Tk-license.terms')):
        require(source.is_file() and source.stat().st_size > 0, f'Licença runtime ausente: {source}.')
        shutil.copyfile(source, target / name)
    tcl = base / 'tcl' / 'tcl8.6' / 'license.terms'
    if tcl.is_file():
        require(tcl.stat().st_size > 0, 'Termos Tcl vazios.')
        shutil.copyfile(tcl, target / 'Tcl-license.terms')
    else:
        fetch_tcl_terms(target / 'Tcl-license.terms')
        (target / 'Tcl-provenance.txt').write_text(
            f'{TCL_TERMS_URL}\nsha256={TCL_TERMS_SHA256}\nsize_bytes={TCL_TERMS_SIZE}\n'
            'cpython-bin-deps tcltk-8.6.15.0, immutable commit; exact bytes without newline conversion.\n',
            encoding='utf-8')
    distribution = importlib.metadata.distribution('pyinstaller')
    licenses = [p for p in distribution.files or [] if '.dist-info/' in str(p).replace('\\', '/')
                and ('.dist-info/licenses/' in str(p).replace('\\', '/')
                     or Path(str(p)).name.upper().startswith(('COPYING', 'LICENSE')))]
    require(licenses, 'Licença/exceção PyInstaller ausente do wheel.')
    for file in licenses:
        source = Path(distribution.locate_file(file))
        require(source.is_file() and source.stat().st_size > 0, 'Notice PyInstaller ausente/vazio.')
        destination = target / 'PyInstaller' / Path(str(file)).name
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, destination)


def audit_names(files, directories=()):
    names = {name.casefold(): size for name, size in files.items()}
    for name in REQUIRED_FILES:
        require(names.get(name.casefold(), 0) > 0, f'Pacote incompleto: {name}')
    require(any(name.startswith('_internal/notices/runtime/pyinstaller/') and size > 0
                for name, size in names.items()), 'Notices PyInstaller obrigatórios.')
    for name in list(names) + list(directories):
        lowered = name.casefold().rstrip('/')
        parts = lowered.split('/')
        require(not any(p in ('tools', '.git', '.local-evidence', 'tests', '.vendor', '__pycache__', 'ci-logs', 'ci-licenses') for p in parts),
                'Conteúdo desnecessário/ferramentas no pacote.')
        require(not any(part.startswith(('ci_activation', 'license_vectors', 'licensing', 'license_native', 'issuer')) for part in parts)
                and not lowered.endswith(('.pem', '.key', '.pfx', '.p12'))
                and parts[-1] not in ('activation.bin', 'activation-request.json')
                and not any('private' in part or 'github_token' in part for part in parts),
                'Segredo, estado de ativação ou helper CI proibido no pacote.')
        require(not lowered.endswith(('.ttf', '.otf', '.woff', '.woff2', '.ttc'))
                or name in {f'_internal/{item}'.casefold() for item in FONT_FILES},
                'Fonte fora do catálogo fixo no pacote.')
        require(not lowered.endswith('.exe') or lowered == 'gerador-de-menus.exe', 'Executável inesperado no pacote.')
        require(not lowered.endswith(('.zip', '.7z', '.tar', '.gz', '.xz', '.whl', '.part', '.lock'))
                or lowered == '_internal/base_library.zip', 'Arquivo de download/fontes no pacote.')


def audit_python_library_archive(path):
    """The one allowed nested ZIP contains Python bytecode, never native tools."""
    files = safe_members(path)
    require(files and all(name.casefold().endswith('.pyc') for name in files),
            'base_library.zip deve conter somente bytecode Python.')
    require(not any(part.startswith(('licensing', 'license_native', 'issuer', 'ci_activation', 'license_vectors'))
                    for name in files for part in name.casefold().split('/')),
            'Recurso legado de ativação/emissor proibido no runtime Python.')


def audit_fonts(files, read):
    """Pins do catálogo e de cada TTF/OFL, tanto na árvore quanto no ZIP final."""
    prefix = '_internal/font_assets/'
    expected = {f'_internal/{name}' for name in FONT_FILES}
    require({name for name in files if name.startswith(prefix)} == expected,
            'font_assets: conteúdo ausente ou fora do catálogo fixo.')
    raw = read(prefix + 'manifest.json')
    require(hashlib.sha256(raw).hexdigest() == MANIFEST_SHA256,
            'font_assets/manifest.json: SHA256 do catálogo divergente.')
    for row in json.loads(raw)['families']:
        for item in row['files']:
            name = prefix + item['path']
            body = read(name)
            require(len(body) == item['size_bytes'] and hashlib.sha256(body).hexdigest() == item['sha256'],
                    f'{name}: tamanho/SHA256 divergente.')


def audit_package(path):
    root = Path(path)
    files, directories = {}, []
    for p in root.rglob('*'):
        require(not p.is_symlink() and not (hasattr(p, 'is_junction') and p.is_junction()), 'Link no pacote.')
        name = p.relative_to(root).as_posix()
        if p.is_file(): files[name] = p.stat().st_size
        else: directories.append(name)
    audit_names(files, directories)
    audit_fonts(files, lambda name: (root / name).read_bytes())
    library = root / '_internal' / 'base_library.zip'
    if library.is_file():
        audit_python_library_archive(library)


def audit_archive(path):
    files = safe_members(path)
    prefix = 'Gerador-de-Menus/'
    require(all(name.startswith(prefix) for name in files), 'Raiz inesperada no ZIP final.')
    with __import__('zipfile').ZipFile(path) as archive:
        audit_fonts({name[len(prefix):]: info.file_size for name, info in files.items()},
                    lambda name: archive.read(prefix + name))
        directories = []
        for info in archive.infolist():
            require(info.filename == prefix or info.filename.startswith(prefix), 'Raiz inesperada no ZIP final.')
            if info.is_dir(): directories.append(info.filename[len(prefix):])
        library = prefix + '_internal/base_library.zip'
        if library in files:
            import io
            require(files[library].file_size <= 32 * 1024**2,
                    'base_library.zip excedeu limite de auditoria.')
            audit_python_library_archive(io.BytesIO(archive.read(library)))
    audit_names({name[len(prefix):]: info.file_size for name, info in files.items()}, directories)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['validate', 'fetch-tools', 'notices', 'wheel', 'audit', 'audit-archive'])
    parser.add_argument('path', type=Path)
    parser.add_argument('--consent-download', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.operation == 'validate': validate_manifest_file(args.path)
        elif args.operation == 'fetch-tools':
            require(sys.platform == 'win32', 'Download binário permitido somente no runner Windows.')
            require(args.consent_download, 'Exige --consent-download explícito para testes do runner.')
            args.path.mkdir(parents=True, exist_ok=True)
            print(json.dumps(ensure_tools(args.path.absolute(), load_manifest(), consent=True)))
        elif args.operation == 'notices': runtime_notices(args.path)
        elif args.operation == 'wheel':
            require(args.path.name == 'pyinstaller-6.16.0-py3-none-win_amd64.whl' and digest(args.path) == WHEEL_SHA256,
                    'Wheel PyInstaller Windows x64 diverge do hash revisado.')
        elif args.operation == 'audit-archive': audit_archive(args.path)
        else: audit_package(args.path)
    except Exception as exc:
        print(f'BUILD BLOQUEADO: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
