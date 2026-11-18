"""End-to-end test of the Bananza launcher against a fake release server on 127.0.0.1 (Windows, MSYS2 clang).

    .venv\\Scripts\\python tests\\launcher_e2e.py [--keep]


The C launcher (a -DDKB_LAUNCHER_TEST build, no windows) does the first install of release A for real: manifest,
download, SHA-256, tar.exe, current.txt, start of A's python\\pythonw.exe (a stub that logs its command line). The
update to B then goes through installer.update in the order the window calls it (check, download, stage,
start_version), with B's window replaced by tests/launcher_e2e_window.py, which runs installer.gui.start_launcher (confirm_start and the
launcher exe's replacement). The opener maps GitHub's https links onto the local server; TLS and GitHub's redirects
are not covered. Scenarios: a tampered zip and a cancel at the first install, the first install and a second start,
two first installs at once, then from installed A: a tampered update, a window that never confirms, a kill between
previous.txt and current.txt, the update to B with the launcher's replacement, a second start, and going back to A.
Everything happens in a temporary folder (kept with --keep); exit code 0 when every check passes.
"""
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
from installer import update  # noqa: E402

CLANG = r'C:\msys64\ucrt64\bin\clang.exe'
WORK = Path(tempfile.mkdtemp(prefix='dkb-launcher-e2e-'))
RESULTS = []


def check(name, ok, detail=''):
    RESULTS.append((name, bool(ok)))
    print(f'{"PASS" if ok else "FAIL"}  {name}{"  -- " + str(detail) if detail and not ok else ""}', flush=True)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---- builds -----------------------------------------------------------------------------------------------------

def build_launcher():
    sys.path.insert(0, str(REPO / 'tools'))
    import make_release
    inc = WORK / 'inc'
    make_release.launcher_header(inc / 'launcher_text.h', 'dkb')
    exe = WORK / 'launcher-test.exe'
    src = [str(s) for s in make_release.launcher_sources('dkb')]
    subprocess.run([CLANG, '-O2', '-mwindows', '-municode', '-fuse-ld=lld', '-Wl,--dependent-load-flag=0x800',
                    '-DDKB_LAUNCHER_TEST', '-I', str(inc), *src, '-o', str(exe)], check=True)
    return exe.read_bytes()


class Release:
    def __init__(self, folder, numbers, commit, launcher_bytes):
        self.version, self.tag = f'{numbers}+{commit}', f'v{numbers}'
        self.folder = folder
        folder.mkdir(parents=True)
        self.launcher_bytes = launcher_bytes
        self.zip = folder / f'DKB-Setup-{self.version}-windows.zip'
        with zipfile.ZipFile(self.zip, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('DKB-Setup/VERSION', self.version + '\n')
            z.write(WORK / 'stub.exe', 'DKB-Setup/python/pythonw.exe')
            z.writestr('DKB-Setup/installer/gui.py', '# the window\n')
            z.writestr('DKB-Setup/DKB-Setup.exe', launcher_bytes)
            z.writestr('DKB-Setup/llvm/bin/deep/file.txt', 'x' * 100000)
        self.launcher = folder / 'DKB-Launcher.exe'
        self.launcher.write_bytes(launcher_bytes)
        self.manifest_text = update.write_manifest(self.version, self.tag, self.zip, 'DKB-Setup', self.launcher)
        self.manifest = update.parse_manifest(self.manifest_text)


# ---- the fake GitHub --------------------------------------------------------------------------------------------

class Server:
    def __init__(self):
        self.files, self.requests, self.delay = {}, [], 0.0
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                server.requests.append(self.path)
                data = server.files.get(self.path)
                if data is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                for i in range(0, len(data), 16384):
                    if server.delay:
                        time.sleep(server.delay)
                    try:
                        self.wfile.write(data[i:i + 16384])
                    except OSError:
                        return
        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, path):
        return f'http://127.0.0.1:{self.port}{path}'

    def publish(self, release, tamper=False):
        """release as GitHub's latest: the manifest at the latest link, the files at the tag's links, the API answer"""
        data = release.zip.read_bytes()
        if tamper:
            data = data[:-200] + bytes(b ^ 0x55 for b in data[-200:])
        name = urllib.request.quote(release.zip.name, safe='')
        self.files = {
            '/latest/dkb-release.txt': release.manifest_text.encode(),
            f'/download/{release.tag}/{update.MANIFEST}': release.manifest_text.encode(),
            f'/download/{release.tag}/{name}': data,
            f'/download/{release.tag}/DKB-Launcher.exe': release.launcher_bytes,
            '/api/latest': json.dumps({
                'tag_name': release.tag, 'html_url': f'https://github.com/{update.REPO}/releases/tag/{release.tag}',
                'body': 'notes', 'draft': False, 'prerelease': False,
                'assets': [{'name': update.MANIFEST, 'size': len(release.manifest_text)},
                           {'name': release.zip.name, 'size': release.zip.stat().st_size,
                            'digest': 'sha256:' + sha(release.zip)},
                           {'name': 'DKB-Launcher.exe', 'size': len(release.launcher_bytes)}]}).encode()}

    def opener(self, url, accept=None, timeout=update.TIMEOUT):
        """installer.update's opener with GitHub's https links mapped onto this server"""
        for prefix, local in ((update.API, '/api/latest'), (update.LATEST_MANIFEST, '/latest/dkb-release.txt'),
                              (update.DOWNLOAD, '/download/')):
            if url.startswith(prefix):
                return urllib.request.urlopen(self.url(local + url[len(prefix):]), timeout=timeout)
        raise OSError(f'not mapped: {url}')


# ---- the player's folder ----------------------------------------------------------------------------------------

class Player:
    def __init__(self, name, launcher_bytes, server):
        self.dir = WORK / name
        self.dir.mkdir(parents=True)
        self.exe = self.dir / 'DKB-Launcher.exe'
        self.exe.write_bytes(launcher_bytes)
        self.base = self.dir / 'DKB-Recomp'
        self.log, self.stub_log = self.dir / 'launcher-messages.txt', self.dir / 'started.txt'
        self.server = server

    def env(self, **extra):
        return {**os.environ, 'DKB_LAUNCHER_LOG': str(self.log), 'STUB_LOG': str(self.stub_log),
                'DKB_LAUNCHER_MANIFEST': self.server.url('/latest/dkb-release.txt'),
                'DKB_LAUNCHER_DOWNLOAD': self.server.url('/download/'), **extra}

    def run(self, wait=True, **extra):
        p = subprocess.Popen([str(self.exe)], cwd=str(self.dir), env=self.env(**extra))
        if wait:
            p.wait(timeout=180)
        return p

    def messages(self):
        return self.log.read_text(encoding='utf-8').splitlines() if self.log.exists() else []

    def started(self, at_least=0):
        """the stub's log lines; waits up to 15 s for at_least of them (the launcher exits before its child logs)"""
        end = time.monotonic() + 15
        while True:
            lines = self.stub_log.read_text(encoding='utf-8').splitlines() if self.stub_log.exists() else []
            if len(lines) >= at_least or time.monotonic() > end:
                time.sleep(0.3)   # and lets the stub process end, so its exe can be deleted
                return self.stub_log.read_text(encoding='utf-8').splitlines() if self.stub_log.exists() else []
            time.sleep(0.1)

    def pointer(self, name):
        return update.read_pointer(self.base, name)

    def versions(self):
        v = self.base / 'versions'
        return sorted(p.name for p in v.iterdir()) if v.exists() else []

    def downloads(self):
        d = self.base / 'downloads'
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def update_to(self, current, window_env=None, spawn=None):
        """installer.gui App.update_worker's calls, in its order"""
        offer = update.check(current, self.server.opener)
        assert update.may_install(offer), offer
        zip_path = update.download(offer.manifest.tools, offer.tag, self.base / 'downloads', opener=self.server.opener,
                                   sleep=lambda s: None)
        folder = update.stage(zip_path, self.base, offer.manifest)
        zip_path.unlink(missing_ok=True)

        def window(cmd, cwd):   # the new version's window: window.py runs gui.start_launcher with these arguments
            return subprocess.Popen([sys.executable, str(HERE / 'launcher_e2e_window.py'), *cmd[1:]], cwd=cwd,
                                    env={**os.environ, 'E2E_REPO': str(REPO), **(window_env or {})})
        return offer, folder, update.start_version(self.base, offer.tag, self.exe, ('--lang', 'en'),
                                                   spawn=spawn or window, timeout=60)


def main():
    if not Path(CLANG).is_file():
        print(f'skipped: no {CLANG}')
        return 0
    subprocess.run([CLANG, '-O1', '-mwindows', '-municode', str(HERE / 'launcher_e2e_stub.c'), '-o', str(WORK / 'stub.exe')],
                   check=True)
    launcher = build_launcher()
    a = Release(WORK / 'release-a', '0.5.0', 'aaaaaaa', launcher + b'\0release A')   # an overlay: another exe hash
    b = Release(WORK / 'release-b', '0.6.0', 'bbbbbbb', launcher + b'\0release B')
    server = Server()

    # -- first install, failure paths
    server.publish(a, tamper=True)
    p = Player('tampered-first', a.launcher_bytes, server)
    p.run()
    check('first install: a tampered zip is refused with the checksum message',
          any('checksum' in m for m in p.messages()), p.messages())
    check('first install: a tampered zip leaves no version, pointer, download or start',
          p.versions() == [] and p.downloads() == [] and p.started() == [], (p.versions(), p.downloads(), p.started()))

    server.publish(a)
    p = Player('cancelled-first', a.launcher_bytes, server)
    p.run(DKB_LAUNCHER_CANCEL='unpack')
    check('first install: a cancel after the download unpacks, keeps and starts nothing',
          p.versions() == [] and p.downloads() == [] and p.started() == [] and p.messages() == [p.messages()[0]],
          (p.versions(), p.downloads(), p.started(), p.messages()))

    # -- first install of A
    p = Player('player', a.launcher_bytes, server)
    before = len(server.requests)
    p.run()
    started = p.started(1)
    check('first install: versions\\v0.5.0 unpacked, current.txt names it, downloads empty',
          p.versions() == ['current.txt', 'v0.5.0'] and p.pointer('current') == 'v0.5.0' and p.downloads() == [],
          (p.versions(), p.downloads()))
    check('first install: only the confirmation was shown', len(p.messages()) == 1 and 'downloads them' in p.messages()[0],
          p.messages())
    check('first install: A started from its folder with --launcher and --base',
          len(started) == 1 and started[0].split('\t')[0] == str(p.base / 'versions' / 'v0.5.0') and
          f'--launcher "{p.exe}" --base "{p.base}"' in started[0], started)
    check('first install: the manifest and the zip were requested once each', len(server.requests) - before == 2,
          server.requests[before:])
    before = len(server.requests)
    p.run()
    check('second start of A: no request, A starts again', len(server.requests) == before and len(p.started(2)) == 2,
          server.requests[before:])

    # -- two first installs at once: the second waits for the first, then starts what it installed
    server.delay = 0.2
    q = Player('twice', a.launcher_bytes, server)
    before = len(server.requests)
    first, second = q.run(wait=False), None
    time.sleep(0.5)
    second = q.run(wait=False)
    first.wait(timeout=300), second.wait(timeout=300)
    server.delay = 0.0
    zips = [r for r in server.requests[before:] if r.endswith('.zip')]
    check('two launchers at once: one download, both start v0.5.0, one version folder',
          len(zips) == 1 and len(q.started(2)) == 2 and q.versions() == ['current.txt', 'v0.5.0'],
          (server.requests[before:], q.started(), q.versions(), q.messages()))

    # -- the update to B, failure paths first
    shutil.copytree(p.base, WORK / 'a-installed')   # every update scenario starts from this installed A
    exe_a = p.exe.read_bytes()

    def reset():
        shutil.rmtree(p.base)
        shutil.copytree(WORK / 'a-installed', p.base)
        p.exe.write_bytes(exe_a)

    server.publish(b, tamper=True)
    try:
        p.update_to(a.version)
        check('update: a tampered zip is refused', False)
    except update.UpdateError as e:
        check('update: a tampered zip is refused (update.checksum)', e.id == 'update.checksum', e.id)
    check('update: after the tampered zip, A is current, nothing staged, downloads empty',
          p.pointer('current') == 'v0.5.0' and p.versions() == ['current.txt', 'v0.5.0'] and p.downloads() == [],
          (p.versions(), p.downloads()))

    server.publish(b)
    reset()

    def no_confirm(cmd, cwd):   # B's window exits without confirming: its python is the stub, which only logs
        return subprocess.Popen([str(Path(cwd) / 'python' / 'pythonw.exe'), *cmd[1:]], cwd=cwd, env=p.env())
    offer, folder, result = p.update_to(a.version, spawn=no_confirm)
    p.started(1)
    check('update: a window that never confirms gives "failed" and A stays current',
          result == 'failed' and p.pointer('current') == 'v0.5.0' and p.pointer('previous') is None, result)
    n = len(p.started())
    p.run()
    check('update: after the failed start the launcher starts A', len(p.started(n + 1)) == n + 1 and
          p.started()[-1].split('\t')[0].endswith('v0.5.0'), p.started()[-1:])

    reset()
    offer, folder, result = p.update_to(a.version, window_env={'E2E_KILL_BEFORE': 'current'})
    check('update: killed between previous.txt and current.txt: "failed", A still current',
          result == 'failed' and p.pointer('current') == 'v0.5.0', (result, p.pointer('current'), p.pointer('previous')))
    n = len(p.started())
    p.run()
    check('update: after that kill the launcher starts A', len(p.started(n + 1)) == n + 1 and
          p.started()[-1].split('\t')[0].endswith('v0.5.0'), p.started()[-1:])

    # -- the update to B
    reset()
    before = len(server.requests)
    offer, folder, result = p.update_to(a.version)
    check('update: B checked, downloaded, verified, unpacked; its window confirmed ("started")',
          result == 'started' and folder == p.base / 'versions' / 'v0.6.0' and update.complete(folder, b.version), result)
    check('update: current.txt is B, previous.txt is A, both folders kept, downloads empty',
          p.pointer('current') == 'v0.6.0' and p.pointer('previous') == 'v0.5.0' and
          p.versions() == ['current.txt', 'previous.txt', 'v0.5.0', 'v0.6.0'] and p.downloads() == [],
          (p.versions(), p.downloads()))
    check('update: the launcher exe replaced by B\'s, with no .old or .new left',
          sha(p.exe) == hashlib.sha256(b.launcher_bytes).hexdigest() and sorted(x.name for x in p.dir.iterdir()
                                                                                if x.name.startswith('DKB-Launcher')) == ['DKB-Launcher.exe'],
          sorted(x.name for x in p.dir.iterdir()))
    check('update: requests were the API, the manifest and the zip', len(server.requests) - before == 3,
          server.requests[before:])
    before, n = len(server.requests), len(p.started())
    p.run()   # now B's launcher binary
    check('second start after the update: B starts, no request', len(server.requests) == before and
          len(p.started(n + 1)) == n + 1 and p.started()[-1].split('\t')[0].endswith('v0.6.0'), p.started()[-1:])
    offer = update.check(b.version, server.opener)
    check('the check from B: B is the latest, nothing to install', offer.direction == 0 and not update.may_install(offer))

    # -- Go back to A (the home screen's button: start_version of previous.txt's tag, as App.go_back does)
    def window(cmd, cwd):
        return subprocess.Popen([sys.executable, str(HERE / 'launcher_e2e_window.py'), *cmd[1:]], cwd=cwd,
                                env={**os.environ, 'E2E_REPO': str(REPO)})
    from installer import guilogic
    tag = guilogic.previous_version(p.base, 'v0.6.0')
    before = len(server.requests)
    result = update.start_version(p.base, tag, p.exe, ('--lang', 'en'), spawn=window, timeout=60)
    check('go back: A confirms, current.txt A, previous.txt B, both kept, A\'s launcher back, no request',
          tag == 'v0.5.0' and result == 'started' and p.pointer('current') == 'v0.5.0' and
          p.pointer('previous') == 'v0.6.0' and p.versions() == ['current.txt', 'previous.txt', 'v0.5.0', 'v0.6.0'] and
          sha(p.exe) == hashlib.sha256(a.launcher_bytes).hexdigest() and len(server.requests) == before,
          (tag, result, p.versions()))
    offer = update.check(a.version, server.opener)
    folder = update.stage(Path('unused.zip'), p.base, offer.manifest)   # B's folder is complete: reused, not unpacked
    check('go back: B is offered again and its folder is reused without a download',
          update.may_install(offer) and folder == p.base / 'versions' / 'v0.6.0')

    failed = [name for name, ok in RESULTS if not ok]
    print(f'\n{len(RESULTS) - len(failed)} of {len(RESULTS)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    try:
        code = main()
    finally:
        if '--keep' in sys.argv:
            print(f'kept {WORK}')
        else:
            shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(code)
