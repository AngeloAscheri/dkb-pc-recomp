"""AMD FSR 4 for upscaler = fsr4 (docs/dkb-dlss.md): the two Vulkan libraries of AMD's FidelityFX SDK, fetched from
AMD's own repository when the user asks for them and accepts AMD's license, never shipped with this project.

runtime/vk_fsr4.c loads amd_fidelityfx_loader_vk.dll and amd_fidelityfx_upscaler_vk.dll from fsr_dir or from the
folder of dkb.exe. They come only as prebuilt, signed binaries; there is no source for them, so a GPL program does not
carry them. The terms have changed between SDK releases: in v2.1.0 to v2.3.0 docs/license.md left the signedbin
libraries under AMD's binary-only license (no reverse engineering), and v2.4.0 lists them among the MIT files. So the
release is pinned (TAG) and so is each file's SHA-256, the license among them: the setup shows that text and asks the
user to accept it before anything is fetched, and a new release means new hashes here and a new acceptance.

The files are read from raw.githubusercontent.com at the tag, a public download that needs no login. The license is
saved next to the libraries (its MIT part asks that the notice go with copies).
"""
import hashlib
import os
import urllib.error
import urllib.request
from pathlib import Path

TAG = 'v2.4.0'
REPO = 'https://raw.githubusercontent.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/' + TAG + '/'
PAGE = 'https://github.com/GPUOpen-LibrariesAndSDKs/FidelityFX-SDK/blob/' + TAG + '/docs/license.md'
LICENSE = ('docs/license.md', 'AMD-FidelityFX-SDK-license.md', 65681,
           '410f9c138e766e846acf2dc93db768198def0293a017d7e3296815b451688dbe')
FILES = (
    ('Kits/FidelityFX/signedbin/amd_fidelityfx_loader_vk.dll', 'amd_fidelityfx_loader_vk.dll', 39184,
     '38a56a01fb3565c0757d9f5c1e4e69de4b65e5f2e5769b323b200dc1f142bab9'),
    ('Kits/FidelityFX/signedbin/amd_fidelityfx_upscaler_vk.dll', 'amd_fidelityfx_upscaler_vk.dll', 36135696,
     '4a7e895af6e0b615d40a84d59eb8c2b1054ad91cdd0c739567940270d51bdab7'),
)
CHUNK = 1 << 20
TIMEOUT = 60


class FetchError(Exception):
    """A file could not be fetched or did not match its pinned size and SHA-256: (what, detail)."""


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(CHUNK), b''):
            h.update(block)
    return h.hexdigest()


def _get(url, size, sha, out, progress=None, base=None):
    """Streams url into out (a temporary name next to it), checks size and SHA-256, then moves it into place."""
    part = out.with_name(out.name + '.part')
    h, got = hashlib.sha256(), 0
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as r, open(part, 'wb') as f:
            for block in iter(lambda: r.read(CHUNK), b''):
                got += len(block)
                if got > size:
                    raise FetchError(out.name, f'more than the expected {size} bytes')
                h.update(block)
                f.write(block)
                if progress:
                    progress((base or 0) + got)
    except (urllib.error.URLError, OSError) as e:
        part.unlink(missing_ok=True)
        raise FetchError(out.name, str(getattr(e, 'reason', e))) from e
    except FetchError:
        part.unlink(missing_ok=True)
        raise
    if got != size or h.hexdigest() != sha:
        part.unlink(missing_ok=True)
        raise FetchError(out.name, f'{got} bytes with SHA-256 {h.hexdigest()}, expected {size} bytes with {sha}')
    os.replace(part, out)


def license_text(repo=REPO):
    """AMD's license of the pinned release as text, fetched and checked against its pinned SHA-256."""
    path, _, size, sha = LICENSE
    try:
        with urllib.request.urlopen(repo + path, timeout=TIMEOUT) as r:
            data = r.read(size + 1)
    except (urllib.error.URLError, OSError) as e:
        raise FetchError('license', str(getattr(e, 'reason', e))) from e
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
        raise FetchError('license', f'{len(data)} bytes with SHA-256 {hashlib.sha256(data).hexdigest()}, expected {sha}')
    return data.decode('utf-8')


def total_bytes():
    return sum(size for _, _, size, _ in (LICENSE, *FILES))


def installed(folder):
    """True when every library and the license are in folder with their pinned size and SHA-256."""
    try:
        return all((Path(folder) / name).stat().st_size == size and _sha256(Path(folder) / name) == sha
                   for _, name, size, sha in (LICENSE, *FILES))
    except OSError:
        return False


def fetch(folder, progress=None, repo=REPO):
    """Fetches the license and the libraries of TAG into folder, each checked before it takes its name. A file
    already there with the pinned hash is kept. progress(done_bytes) reports the bytes fetched so far."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    done = 0
    for path, name, size, sha in (LICENSE, *FILES):
        out = folder / name
        try:
            keep = out.stat().st_size == size and _sha256(out) == sha
        except OSError:
            keep = False
        if not keep:
            _get(repo + path, size, sha, out, progress, done)
        done += size
        if progress:
            progress(done)
