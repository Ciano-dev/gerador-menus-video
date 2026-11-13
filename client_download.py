"""Consented portable tools. Stdlib only; importing this module never uses the network.

The OS lock survives as a marked file, but is released by the OS on process exit.
Only a unique staging directory created by this call is ever removed. Complete
pairs become visible together via directory rename; unknown/corrupt files stay.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

MAX_EXTRACTED = 1024 * 1024 * 1024
MAX_ENTRIES = 4096
HOSTS = {'github.com', 'release-assets.githubusercontent.com', 'raw.githubusercontent.com'}
LOCK_MARKER = b'MenuVideo client-download lock v1\n'
# Exact prior marker only; preserve existing locks without exposing private branding.
LEGACY_LOCK_MARKER_SHA256 = '3b9f1b0e2137b1a494bab737dac538f1036b605664d7e0a92077d52c1d692078'
PINNED_URL = ('https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/'
              'ffmpeg-n9.0.2-39-g7adfbe40dc-win64-gpl-9.0.zip')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def redirect_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        valid = (parsed.scheme == 'https' and parsed.hostname in HOSTS
                 and parsed.port in (None, 443) and not parsed.netloc.endswith(':')
                 and parsed.username is None and parsed.password is None
                 and '#' not in value and not any(c.isspace() or ord(c) < 32 for c in value)
                 and '\\' not in value)
    except (ValueError, TypeError):
        valid = False
    require(valid, 'Destino HTTPS do fornecedor recusado.')  # Never include signed query.
    return value


def initial_url(value):
    redirect_url(value)
    require(value == PINNED_URL, 'URL inicial exige a release fixada exata, sem query.')
    return value


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirect_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def member_name(value):
    require(isinstance(value, str) and value and '\\' not in value, 'Membro ZIP inválido.')
    parts = value.rstrip('/').split('/')
    require(not value.startswith('/') and all(p not in ('', '.', '..') for p in parts), 'Traversal ZIP recusado.')
    require(not any(ord(c) < 32 or c in ':<>"|?*' for c in value), 'Caminho ZIP Windows inválido.')
    require(all(not p.endswith((' ', '.')) and not re.fullmatch(
        r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p) for p in parts), 'Caminho ZIP reservado.')
    return value


def validate_manifest(data):
    require(data.get('schema_version') == 1 and data.get('policy') == 'client-consented-download'
            and data.get('target') == 'windows-x64', 'Manifesto client-download inválido.')
    item = data['binary']
    initial_url(item['url'])
    require(isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256']), 'SHA256 inválido.')
    require(type(item['size_bytes']) is int and 0 < item['size_bytes'] <= 256 * 1024**2, 'Tamanho de download inválido.')
    for name in ('ffmpeg', 'ffprobe'):
        member = member_name(item[name + '_member'])
        require(PurePosixPath(member).name == name + '.exe' and PurePosixPath(member).parent.name == 'bin',
                'Selecionar somente o par bin/ffmpeg.exe e bin/ffprobe.exe.')
    return data


def load_manifest(path=None):
    if path is None:
        if getattr(sys, 'frozen', False):
            location = getattr(sys, '_MEIPASS', None)
            require(location and Path(location).is_absolute(), 'Manifesto interno do pacote indisponível.')
            base = Path(location)
        else:
            base = Path(__file__).parent
        path = base / 'windows_build' / 'client-download-manifest.json'
    return validate_manifest(json.loads(Path(path).read_text(encoding='utf-8')))


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def linked(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def tools_root(app_root, create=False):
    app_root = Path(app_root)
    require(app_root.is_absolute() and app_root.is_dir() and not linked(app_root), 'Pasta portátil inválida.')
    root = app_root / 'tools'
    require(not linked(root), 'Pasta tools não pode ser link.')
    if create:
        root.mkdir(exist_ok=True)
    require(not root.exists() or root.is_dir(), 'tools existente não é diretório.')
    return root


def verified_cache(app_root, data):
    validate_manifest(data)
    root = tools_root(app_root)
    identity = fingerprint(data)
    for directory in sorted(root.glob('ffmpeg-' + identity + '-*')):
        if linked(directory) or not directory.is_dir():
            continue
        try:
            receipt = directory / 'receipt.json'
            if linked(receipt) or not receipt.is_file() or receipt.stat().st_size > 4096:
                continue
            record = json.loads(receipt.read_text(encoding='utf-8'))
            if record['manifest_sha256'] != identity or record['archive_sha256'] != data['binary']['sha256']:
                continue
            hashes = record['tools']
            if set(hashes) != {'ffmpeg.exe', 'ffprobe.exe'}:
                continue
            if all(not linked(directory / name) and (directory / name).is_file()
                   and (directory / name).stat().st_size > 0 and digest(directory / name) == hashes[name]
                   for name in hashes):
                return {'directory': str(directory), 'cache_verified': True, 'downloaded': False,
                        'download_requests': 0, 'download_bytes': 0, 'manifest_sha256': identity,
                        'archive_sha256': record['archive_sha256']}
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return None


def cancelled(cancel):
    if cancel is not None and cancel.is_set():
        raise ValueError('Download cancelado; tente novamente na próxima abertura.')


@contextmanager
def tool_lock(root, timeout=120, cancel=None):
    path = root / '.client-download.lock'
    require(not linked(path), 'Trava lock não pode ser link.')
    created = False
    try:
        stream = path.open('x+b')
        created = True
    except FileExistsError:
        stream = path.open('r+b')
    with stream:
        started = time.monotonic()
        acquired = False
        try:
            while not acquired:
                cancelled(cancel)
                try:
                    # Lock beyond the marker: Windows mandatory byte locks must not
                    # prevent another launch from reading the ownership marker.
                    stream.seek(4096)
                    if sys.platform == 'win32':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    require(time.monotonic() - started < timeout, 'Outra abertura mantém a trava; tente novamente.')
                    time.sleep(0.1)
            stream.seek(0)
            marker = stream.read(len(LOCK_MARKER) + 1)
            require(marker in (b'', LOCK_MARKER)
                    or hashlib.sha256(marker).hexdigest() == LEGACY_LOCK_MARKER_SHA256,
                    'Arquivo lock desconhecido; preservado sem alteração.')
            if created:
                stream.seek(0)
                stream.write(LOCK_MARKER)
                stream.flush()
            # A zero-length preexisting lock is inert: use it without modifying it.
            # This also recovers a crash between exclusive creation and marker write.
            yield
        finally:
            if acquired:
                stream.seek(4096)
                if sys.platform == 'win32':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_UN)


def download(item, destination, progress, cancel):
    initial_url(item['url'])
    total = 0
    value = hashlib.sha256()
    try:
        with urllib.request.build_opener(HTTPSRedirect()).open(item['url'], timeout=30) as source, destination.open('xb') as out:
            while True:
                cancelled(cancel)
                block = source.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                require(total <= item['size_bytes'], 'Download excedeu tamanho fixado.')
                out.write(block)
                value.update(block)
                progress(total, item['size_bytes'])
        require(total == item['size_bytes'], 'Download incompleto; tamanho divergente.')
        require(value.hexdigest() == item['sha256'], 'SHA256 do download divergente.')
        return total
    except Exception:
        # urllib exceptions can embed signed redirect URLs, including in chained traceback.
        raise ValueError('Download do fornecedor falhou ou tamanho/SHA256 divergiu; tente novamente.') from None


def safe_members(path):
    total, seen, files = 0, set(), {}
    with zipfile.ZipFile(path) as archive:
        require(len(archive.infolist()) <= MAX_ENTRIES, 'ZIP com entradas demais.')
        for info in archive.infolist():
            name = member_name(info.orig_filename)
            require(name == info.filename, 'Nome ZIP normalizado não pode ocultar conteúdo inválido.')
            folded = name.rstrip('/').casefold()
            require(folded not in seen, 'Membro ZIP duplicado/colisão de caixa.')
            seen.add(folded)
            mode = info.external_attr >> 16
            require(stat.S_IFMT(mode) in (0, stat.S_IFREG, stat.S_IFDIR), 'Link/entrada ZIP especial recusado.')
            require(not info.flag_bits & 1, 'ZIP criptografado recusado.')
            total += info.file_size
            require(0 <= info.file_size <= MAX_EXTRACTED and total <= MAX_EXTRACTED, 'ZIP excedeu limite de extração.')
            if not info.is_dir():
                files[name] = info
    file_names = {name.casefold() for name in files}
    for name in seen:
        require(not any('/'.join(name.split('/')[:i]) in file_names for i in range(1, len(name.split('/')))),
                'Colisão arquivo/diretório ZIP.')
    return files


def ensure_tools(app_root, data, consent=False, progress=lambda done, total: None, cancel=None):
    validate_manifest(data)
    cached = verified_cache(app_root, data)
    if cached:
        return cached
    require(consent is True, 'Download exige consentimento explícito.')
    root = tools_root(app_root, create=True)
    with tool_lock(root, cancel=cancel):
        cached = verified_cache(app_root, data)
        if cached:
            return cached
        stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=root))
        try:
            archive_path = stage / 'vendor.zip.part'
            count = download(data['binary'], archive_path, progress, cancel)
            files = safe_members(archive_path)
            pair = stage / 'pair'
            pair.mkdir()
            hashes = {}
            with zipfile.ZipFile(archive_path) as archive:
                for name in ('ffmpeg', 'ffprobe'):
                    cancelled(cancel)
                    member = data['binary'][name + '_member']
                    require(member in files and files[member].file_size > 0, 'Par executável ausente/vazio no ZIP.')
                    destination = pair / (name + '.exe')
                    with archive.open(member) as source, destination.open('xb') as target:
                        remaining = files[member].file_size
                        while block := source.read(min(1024 * 1024, remaining + 1)):
                            cancelled(cancel)
                            remaining -= len(block)
                            require(remaining >= 0, 'Extração excedeu tamanho declarado.')
                            target.write(block)
                        require(remaining == 0, 'Extração incompleta.')
                    hashes[destination.name] = digest(destination)
            record = {'manifest_sha256': fingerprint(data), 'archive_sha256': data['binary']['sha256'], 'tools': hashes}
            (pair / 'receipt.json').write_text(json.dumps(record, sort_keys=True), encoding='utf-8')
            cancelled(cancel)
            destination = root / ('ffmpeg-' + fingerprint(data) + '-' + stage.name.removeprefix('.stage-'))
            require(not destination.exists(), 'Destino de cache existente; preservado.')
            pair.rename(destination)
            result = verified_cache(app_root, data)
            require(result is not None, 'Cache publicado não passou na verificação.')
            result.update(downloaded=True, download_requests=1, download_bytes=count)
            return result
        finally:
            # stage is exclusively ours; no glob cleanup, no deletion of stale/unknown data.
            shutil.rmtree(stage)
