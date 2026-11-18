"""Update tests: the notice, the release manifest, the check, the download, the unpack, the switch of versions and the
launcher's own replacement. Every lookup goes through an injected opener; nothing reaches the network.

    .venv\\Scripts\\python tests\\installer_update_test.py
"""
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from installer import update  # noqa: E402
from installer.update import UpdateError  # noqa: E402


def release(tag, **extra):
    return lambda: {'tag_name': tag, 'html_url': f'https://example.invalid/{tag}', **extra}


def broken():
    raise OSError('offline')


class NoticeTest(unittest.TestCase):
    def test_newer(self):
        self.assertEqual(update.newer_release('0.2.0+33f8bbc', release('v0.3.0')), ('v0.3.0', 'https://example.invalid/v0.3.0'))
        self.assertIsNotNone(update.newer_release('0.2.0-dev+abc', release('v0.2.1')))
        self.assertIsNotNone(update.newer_release('0.9.0', release('v0.10.0')))   # numeric, not text, order

    def test_equal_and_older(self):
        self.assertIsNone(update.newer_release('0.2.0+33f8bbc', release('v0.2.0')))
        self.assertIsNone(update.newer_release('0.2.0', release('v0.1.9')))

    def test_failures_return_none(self):
        self.assertIsNone(update.newer_release('0.2.0', broken))
        self.assertIsNone(update.newer_release('0.2.0', lambda: None))
        self.assertIsNone(update.newer_release('0.2.0', lambda: {'oops': 1}))
        self.assertIsNone(update.newer_release('unknown', release('v0.3.0')))
        with patch.object(update.OPENER, 'open', side_effect=OSError) as opened:
            self.assertIsNone(update.fetch_latest())
        self.assertEqual(opened.call_args[0][0].full_url, update.API)
        self.assertEqual(opened.call_args[0][0].get_header('User-agent'), update.USER_AGENT)   # generic, nothing else

    def test_no_clear_text(self):
        with self.assertRaises(UpdateError):
            update.http_open('http://github.com/x')
        handler = update.HttpsRedirects()
        with self.assertRaises(UpdateError):
            handler.redirect_request(None, None, 302, 'Found', {}, 'http://objects.example.invalid/zip')

    def test_prerelease_ignored(self):
        self.assertIsNone(update.newer_release('0.2.0', release('v0.3.0-rc1')))
        self.assertIsNone(update.newer_release('0.2.0', release('v0.3.0', prerelease=True)))

    def test_switch(self):
        with patch.dict(os.environ, {'DKB_NO_UPDATE_CHECK': '1'}):
            self.assertTrue(update.disabled())
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('DKB_NO_UPDATE_CHECK', None)
            self.assertFalse(update.disabled())
            self.assertTrue(update.disabled(True))

    def test_text_in_both_languages(self):
        for lang in ('en', 'ru'):
            text = json.loads((ROOT / 'installer' / 'text' / f'{lang}.json').read_text(encoding='utf-8'))
            self.assertIn('{tag}', text['ui.update_available'])
            self.assertIn('{url}', text['warn.update_available'])
        en = json.loads((ROOT / 'installer' / 'text' / 'en.json').read_text(encoding='utf-8'))
        ru = json.loads((ROOT / 'installer' / 'text' / 'ru.json').read_text(encoding='utf-8'))
        self.assertNotEqual(en['ui.update_available'], ru['ui.update_available'])

    def test_every_update_error_has_text(self):
        source = (ROOT / 'installer' / 'update.py').read_text(encoding='utf-8')
        ids = set(__import__('re').findall(r"UpdateError\('(update\.\w+)'", source))
        for lang in ('en', 'ru'):
            text = json.loads((ROOT / 'installer' / 'text' / f'{lang}.json').read_text(encoding='utf-8'))
            self.assertEqual(sorted(ids - set(text)), [], lang)


class VersionTest(unittest.TestCase):
    def test_compare(self):
        self.assertEqual(update.compare('0.4.1+ac46642', 'v0.4.1'), 0)   # the commit does not count
        self.assertEqual(update.compare('0.4', '0.4.0'), 0)
        self.assertEqual(update.compare('v0.10.0', '0.9.9'), 1)
        self.assertEqual(update.compare('0.4.1', '0.4.2+abc'), -1)
        self.assertEqual(update.compare('0.4.1-dev+abc', '0.4.1'), 0)
        self.assertIsNone(update.compare('unknown', '0.4.1'))
        self.assertIsNone(update.compare('v0.5.0-rc1', '0.4.1'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Files:
    """A release in a temporary folder: the tools zip with DKB-Setup/ inside, the launcher exe, the manifest."""

    def __init__(self, folder, version='0.5.0+abc1234', tag='v0.5.0', extra_members=(), version_text=None):
        self.folder = Path(folder)
        self.version, self.tag = version, tag
        self.launcher_bytes = b'MZ launcher ' + version.encode()
        self.zip = self.folder / f'DKB-Setup-{version}-windows.zip'
        python = update.version_python(Path('DKB-Setup')).as_posix()
        with zipfile.ZipFile(self.zip, 'w') as z:
            z.writestr('DKB-Setup/VERSION', (version_text or version) + '\n')
            z.writestr(python, b'python')
            z.writestr('DKB-Setup/installer/gui.py', b'# window')
            z.writestr(f'DKB-Setup/{update.LAUNCHER_EXE}', self.launcher_bytes)
            z.writestr('DKB-Setup/tools/x/y.txt', b'deep')
            for name, data in extra_members:
                z.writestr(name, data)
        self.launcher = self.folder / 'DKB-Launcher.exe'
        self.launcher.write_bytes(self.launcher_bytes)
        self.text = update.write_manifest(version, tag, self.zip, 'DKB-Setup', self.launcher)
        self.manifest = update.parse_manifest(self.text)
        self.zip_bytes = self.zip.read_bytes()

    def api(self, **change):
        info = {'tag_name': self.tag, 'html_url': f'https://github.com/{update.REPO}/releases/tag/{self.tag}',
                'body': '- Faster\n- Fixed a crash', 'draft': False, 'prerelease': False,
                'assets': [{'name': update.MANIFEST, 'size': len(self.text)},
                           {'name': self.zip.name, 'size': len(self.zip_bytes), 'digest': 'sha256:' + sha(self.zip_bytes)},
                           {'name': self.launcher.name, 'size': len(self.launcher_bytes)}]}
        info.update(change)
        return json.dumps(info).encode()


class Opener:
    """URL -> bytes, an exception, or a list of answers taken one per request. Records every request."""

    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def __call__(self, url, accept=None, timeout=None):
        self.requests.append(url)
        answer = self.answers.get(url, OSError(f'no route to {url}'))
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer if hasattr(answer, 'read') else io.BytesIO(answer)


class Interrupted(io.BytesIO):
    """A response that ends with a reset connection after `cut` bytes."""

    def __init__(self, data, cut):
        super().__init__(data[:cut])

    def read(self, n=-1):
        block = super().read(n)
        if not block:
            raise ConnectionResetError('connection reset')
        return block


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.files = Files(self.work.name)

    def tearDown(self):
        self.work.cleanup()

    def test_written_manifest_reads_back(self):
        m = self.files.manifest
        self.assertEqual((m.version, m.tag, m.tools.top), ('0.5.0+abc1234', 'v0.5.0', 'DKB-Setup'))
        self.assertEqual(m.tools.sha256, sha(self.files.zip_bytes))
        self.assertEqual(m.launcher.size, len(self.files.launcher_bytes))
        self.assertTrue(m.tools.url('v0.5.0').endswith('/v0.5.0/DKB-Setup-0.5.0%2Babc1234-windows.zip'))
        self.assertEqual(update.parse_manifest(self.files.text.encode() + b'\nlater-field x\n'), m)   # newer fields

    def test_tampered_manifests_are_refused(self):
        good = self.files.text
        tools = next(line for line in good.splitlines() if line.startswith('tools '))
        cases = {
            'header': good.replace('dkb-release 1', 'dkb-release 2'),
            'checksum': good.replace(tools.split()[1], 'Z' * 64),
            'size': good.replace(tools, tools.replace(tools.split()[2], '0')),
            'path': good.replace(tools.split()[3], '../evil.zip'),
            'top': good.replace(' DKB-Setup\n', ' ..\n'),
            'version': good.replace('tag v0.5.0', 'tag v0.6.0'),
            'twice': good + 'tag v0.5.0\n',
            'binary': good.encode() + b'\xff',
        }
        for name, text in cases.items():
            with self.subTest(name), self.assertRaises(UpdateError) as e:
                update.parse_manifest(text)
            self.assertEqual(e.exception.id, 'update.manifest_bad')

    def test_truncated_manifests_are_refused(self):
        good = self.files.text
        for cut in (0, 10, good.index('tools'), good.index('launcher') + 12, len(good) - 30):
            with self.subTest(cut=cut), self.assertRaises(UpdateError):
                update.parse_manifest(good[:cut])


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.files = Files(self.work.name)
        f = self.files
        self.answers = {update.API: f.api(), update.release_url(f.tag, update.MANIFEST): f.text.encode()}

    def tearDown(self):
        self.work.cleanup()

    def test_newer_release_is_offered_with_its_notes(self):
        opener = Opener(self.answers)
        offer = update.check('0.4.1+ac46642', opener)
        self.assertEqual((offer.tag, offer.version, offer.direction), ('v0.5.0', '0.5.0+abc1234', 1))
        self.assertIn('Fixed a crash', offer.notes)
        self.assertTrue(update.may_install(offer))
        self.assertTrue(all(u.startswith('https://') for u in opener.requests))
        self.assertEqual(len(opener.requests), 2)   # the API and the manifest: nothing is downloaded before Update

    def test_same_and_older(self):
        self.assertEqual(update.check('0.5.0+abc1234', Opener(self.answers)).direction, 0)
        self.assertFalse(update.may_install(update.check('0.5.0+other', Opener(self.answers))))
        older = update.check('0.6.0', Opener(self.answers))
        self.assertEqual(older.direction, -1)
        self.assertFalse(update.may_install(older))                         # no downgrade
        self.assertTrue(update.may_install(older, allow_downgrade=True))    # unless asked

    def test_digest_or_size_disagreeing_with_the_manifest(self):
        f = self.files
        for assets in ([{'name': update.MANIFEST, 'size': 1},
                        {'name': f.zip.name, 'size': len(f.zip_bytes), 'digest': 'sha256:' + '0' * 64},
                        {'name': f.launcher.name, 'size': len(f.launcher_bytes)}],
                       [{'name': update.MANIFEST, 'size': 1}, {'name': f.zip.name, 'size': 5},
                        {'name': f.launcher.name, 'size': len(f.launcher_bytes)}],
                       [{'name': update.MANIFEST, 'size': 1}, {'name': f.zip.name, 'size': len(f.zip_bytes)}]):
            self.answers[update.API] = f.api(assets=assets)
            with self.subTest(assets=len(assets)), self.assertRaises(UpdateError) as e:
                update.check('0.4.1', Opener(self.answers))
            self.assertEqual(e.exception.id, 'update.manifest_mismatch')

    def test_manifest_of_another_tag(self):
        self.answers[update.API] = self.files.api(tag_name='v0.5.1')
        self.answers[update.release_url('v0.5.1', update.MANIFEST)] = self.files.text.encode()
        with self.assertRaises(UpdateError) as e:
            update.check('0.4.1', Opener(self.answers))
        self.assertEqual(e.exception.id, 'update.manifest_mismatch')

    def test_api_down_falls_back_to_the_download_link(self):
        self.answers[update.API] = OSError('HTTP 403 rate limit')
        self.answers[update.LATEST_MANIFEST] = self.files.text.encode()
        offer = update.check('0.4.1', Opener(self.answers))
        self.assertEqual((offer.tag, offer.direction, offer.notes), ('v0.5.0', 1, ''))
        self.assertTrue(update.may_install(offer))

    def test_offline(self):
        with self.assertRaises(UpdateError) as e:
            update.check('0.4.1', Opener({}))
        self.assertEqual(e.exception.id, 'update.offline')

    def test_release_without_manifest_offers_only_its_page(self):
        self.answers[update.API] = self.files.api(assets=[])
        offer = update.check('0.4.1', Opener(self.answers))
        self.assertIsNone(offer.manifest)
        self.assertFalse(update.may_install(offer))
        self.assertEqual(offer.direction, 1)

    def test_prerelease_is_not_offered(self):
        self.answers[update.API] = self.files.api(prerelease=True)
        with self.assertRaises(UpdateError):
            update.check('0.4.1', Opener(self.answers))


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.files = Files(self.work.name)
        self.url = self.files.manifest.tools.url(self.files.tag)
        self.out = Path(self.work.name) / 'downloads'

    def tearDown(self):
        self.work.cleanup()

    def get(self, answer, **kw):
        opener = Opener({self.url: answer})
        path = update.download(self.files.manifest.tools, self.files.tag, self.out, opener, sleep=lambda s: None, **kw)
        return path, opener

    def leftovers(self):
        return sorted(p.name for p in self.out.iterdir()) if self.out.exists() else []

    def test_good_download_is_kept_under_its_checksum(self):
        seen = []
        path, _ = self.get(self.files.zip_bytes, progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(path.name, self.files.manifest.tools.sha256 + '.zip')
        self.assertEqual(path.read_bytes(), self.files.zip_bytes)
        self.assertEqual(seen[-1], (len(self.files.zip_bytes),) * 2)
        self.assertEqual(self.leftovers(), [path.name])
        again, opener = self.get(OSError('must not be asked'))   # a checked file is used again
        self.assertEqual((again, opener.requests), (path, []))

    def test_tampered_download_is_deleted(self):
        bad = bytearray(self.files.zip_bytes)
        bad[100] ^= 1
        with self.assertRaises(UpdateError) as e:
            self.get(bytes(bad))
        self.assertEqual(e.exception.id, 'update.checksum')
        self.assertEqual(self.leftovers(), [])

    def test_truncated_download_fails_after_the_retries(self):
        cut = self.files.zip_bytes[:len(self.files.zip_bytes) // 2]
        with self.assertRaises(UpdateError) as e:
            self.get([io.BytesIO(cut), io.BytesIO(cut), io.BytesIO(cut)])
        self.assertEqual(e.exception.id, 'update.download_failed')
        self.assertEqual(self.leftovers(), [])

    def test_interrupted_download_is_tried_again(self):
        data = self.files.zip_bytes
        path, opener = self.get([Interrupted(data, len(data) // 3), data])
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(sha(path.read_bytes()), self.files.manifest.tools.sha256)

    def test_longer_than_the_manifest_says(self):
        with self.assertRaises(UpdateError) as e:
            self.get(self.files.zip_bytes + b'more')
        self.assertEqual(e.exception.id, 'update.download_size')
        self.assertEqual(self.leftovers(), [])

    def test_cancel(self):
        with self.assertRaises(UpdateError) as e:
            self.get(self.files.zip_bytes, cancelled=lambda: True)
        self.assertEqual(e.exception.id, 'update.cancelled')
        self.assertEqual(self.leftovers(), [])


class StageTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.base = Path(self.work.name) / 'base with space и юникод'
        (self.base / 'versions').mkdir(parents=True)
        update.write_pointer(self.base, 'current', 'v0.4.1')
        (self.base / 'versions' / 'v0.4.1').mkdir()

    def tearDown(self):
        self.work.cleanup()

    def stage(self, files):
        return update.stage(files.zip, self.base, files.manifest)

    def assert_untouched(self):
        names = sorted(p.name for p in (self.base / 'versions').iterdir())
        self.assertEqual(names, ['current.txt', 'v0.4.1'])
        self.assertEqual(update.read_pointer(self.base, 'current'), 'v0.4.1')

    def test_good_zip_becomes_a_version_folder(self):
        files = Files(self.work.name)
        folder = self.stage(files)
        self.assertEqual(folder, self.base / 'versions' / 'v0.5.0')
        self.assertTrue(update.complete(folder, files.version))
        self.assertEqual((folder / 'tools' / 'x' / 'y.txt').read_bytes(), b'deep')
        self.assertFalse((self.base / 'versions' / 'v0.5.0.part').exists())
        self.assertEqual(update.read_pointer(self.base, 'current'), 'v0.4.1')   # only the new window's start switches
        self.assertEqual(self.stage(files), folder)   # a complete folder is used again

    def test_member_leaving_the_folder_is_refused(self):
        for name in ('DKB-Setup/../../evil.txt', 'Other/file', '/abs/file', 'DKB-Setup/a\\..\\b'):
            files = Files(self.work.name, extra_members=[(name, b'x')])
            with self.subTest(name), self.assertRaises(UpdateError) as e:
                self.stage(files)
            self.assertEqual(e.exception.id, 'update.zip_bad')
            self.assert_untouched()
            self.assertFalse((Path(self.work.name) / 'evil.txt').exists())

    def test_zip_of_another_version_is_rolled_back(self):
        files = Files(self.work.name, version_text='0.4.9+0000000')
        with self.assertRaises(UpdateError) as e:
            self.stage(files)
        self.assertEqual(e.exception.id, 'update.stage_failed')
        self.assert_untouched()

    def test_damaged_zip_is_rolled_back(self):
        files = Files(self.work.name)
        files.zip.write_bytes(files.zip_bytes[:len(files.zip_bytes) // 2])
        with self.assertRaises(UpdateError):
            self.stage(files)
        self.assert_untouched()

    def test_incomplete_folder_of_the_same_tag_is_replaced(self):
        files = Files(self.work.name)
        stale = self.base / 'versions' / 'v0.5.0'
        stale.mkdir()
        (stale / 'leftover').write_text('x')
        folder = self.stage(files)
        self.assertTrue(update.complete(folder, files.version))
        self.assertFalse((folder / 'leftover').exists())


class FakeProcess:
    def __init__(self, on_poll):
        self.on_poll, self.polls = on_poll, 0

    def poll(self):
        self.polls += 1
        return self.on_poll(self.polls)


class SwitchTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.base = Path(self.work.name)
        for tag in ('v0.3.0', 'v0.4.1', 'v0.5.0'):
            (self.base / 'versions' / tag).mkdir(parents=True)
        update.write_pointer(self.base, 'current', 'v0.4.1')
        update.write_pointer(self.base, 'previous', 'v0.3.0')

    def tearDown(self):
        self.work.cleanup()

    def test_confirmed_start_switches_and_prunes(self):
        self.assertTrue(update.confirm_start(self.base, 'v0.5.0'))
        self.assertEqual(update.read_pointer(self.base, 'current'), 'v0.5.0')
        self.assertEqual(update.read_pointer(self.base, 'previous'), 'v0.4.1')
        self.assertEqual(sorted(p.name for p in (self.base / 'versions').iterdir() if p.is_dir()), ['v0.4.1', 'v0.5.0'])
        self.assertFalse(update.confirm_start(self.base, 'v0.5.0'))   # the next start of the same version changes nothing

    def spawn(self, on_poll, seen):
        def spawn(cmd, cwd):
            seen.append((cmd, cwd))
            return FakeProcess(on_poll)
        return spawn

    def test_new_version_that_confirms(self):
        seen = []

        def confirm(polls):
            if polls == 2:
                update.confirm_start(self.base, 'v0.5.0')
            return None
        result = update.start_version(self.base, 'v0.5.0', self.base / 'DKB-Launcher.exe', ('--lang', 'en'),
                                      spawn=self.spawn(confirm, seen), sleep=lambda s: None)
        self.assertEqual(result, 'started')
        cmd, cwd = seen[0]
        self.assertEqual(cmd[1:3], ['-m', 'installer.gui'])
        self.assertIn('--after-update', cmd)
        self.assertEqual(cmd[cmd.index('--launcher') + 1], str(self.base / 'DKB-Launcher.exe'))
        self.assertEqual(cwd, str(self.base / 'versions' / 'v0.5.0'))

    def test_new_version_that_fails_leaves_the_old_one_current(self):
        result = update.start_version(self.base, 'v0.5.0', None, spawn=self.spawn(lambda polls: 1, []),
                                      sleep=lambda s: None)
        self.assertEqual(result, 'failed')
        self.assertEqual(update.read_pointer(self.base, 'current'), 'v0.4.1')
        self.assertTrue((self.base / 'versions' / 'v0.4.1').is_dir())

    def test_slow_start(self):
        result = update.start_version(self.base, 'v0.5.0', None, spawn=self.spawn(lambda polls: None, []),
                                      timeout=1, sleep=lambda s: None)
        self.assertEqual(result, 'slow')
        self.assertEqual(update.read_pointer(self.base, 'current'), 'v0.4.1')

    def test_pointers_refuse_junk(self):
        (self.base / 'versions' / 'current.txt').write_text('..\\..\\evil\n')
        self.assertIsNone(update.read_pointer(self.base, 'current'))
        self.assertIsNone(update.read_pointer(self.base, 'missing'))

    def test_own_tag(self):
        self.assertEqual(update.own_tag(self.base, self.base / 'versions' / 'v0.5.0'), 'v0.5.0')
        self.assertIsNone(update.own_tag(self.base, ROOT))


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.dir = Path(self.work.name) / 'Игры DKB'
        self.dir.mkdir()
        self.launcher = self.dir / 'DKB-Launcher.exe'
        self.launcher.write_bytes(b'MZ old')
        self.new = self.dir / 'new-DKB-Setup.exe'
        self.new.write_bytes(b'MZ new')

    def tearDown(self):
        self.work.cleanup()

    def test_replaced_by_rename(self):
        self.assertTrue(update.replace_launcher(self.launcher, self.new))
        self.assertEqual(self.launcher.read_bytes(), b'MZ new')
        self.assertEqual((self.dir / 'DKB-Launcher.exe.old').read_bytes(), b'MZ old')
        self.assertFalse((self.dir / 'DKB-Launcher.exe.new').exists())
        self.assertFalse(update.replace_launcher(self.launcher, self.new))   # already the same
        update.clean_old_launcher(self.launcher)
        self.assertFalse((self.dir / 'DKB-Launcher.exe.old').exists())

    def test_failed_rename_puts_the_old_launcher_back(self):
        real = os.replace
        calls = []

        def replace(a, b):
            calls.append((a, b))
            if len(calls) == 2:
                raise PermissionError('denied')
            return real(a, b)
        with patch.object(update.os, 'replace', side_effect=replace), self.assertRaises(PermissionError):
            update.replace_launcher(self.launcher, self.new)
        self.assertEqual(self.launcher.read_bytes(), b'MZ old')
        self.assertFalse((self.dir / 'DKB-Launcher.exe.new').exists())

    def test_only_an_exe_is_replaced(self):
        other = self.dir / 'notes.txt'
        other.write_bytes(b'x')
        self.assertFalse(update.replace_launcher(other, self.new))
        self.assertFalse(update.replace_launcher(self.dir / 'missing.exe', self.new))


class ReleaseToolTest(unittest.TestCase):
    """tools/make_release.py: the launcher's strings and the files published beside the zip."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / 'tools'))
        import make_release
        self.m = make_release
        self.work = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.work.cleanup()

    def test_launcher_header(self):
        out = Path(self.work.name) / 'launcher_text.h'
        self.m.launcher_header(out, 'dkb')
        text = out.read_text(encoding='ascii')
        for key in self.m.LAUNCHER_TEXT:
            for lang in ('EN', 'RU'):
                self.assertIn(f'#define LAUNCHER_{key.upper()}_{lang} L"', text)
        self.assertIn(f'#define LAUNCHER_REPO L"{update.REPO}"', text)
        self.assertNotIn('\\u000a', text)   # C allows no universal character name for a newline
        self.assertIn('\\n', text)
        self.m.launcher_header(out, 'totk')
        self.assertIn('#define LAUNCHER_REPO L""', out.read_text(encoding='ascii'))   # TotK: only the setup

    def test_manifest_and_launcher_beside_the_zip(self):
        folder = Path(self.work.name) / 'DKB-Setup'
        folder.mkdir()
        (folder / 'DKB-Setup.exe').write_bytes(b'MZ launcher')
        semver = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
        zip_path = Path(self.work.name) / f'DKB-Setup-{semver}+abc1234-windows.zip'
        zip_path.write_bytes(b'PK zip')
        with patch.object(self.m, 'GAME', 'dkb'), patch.object(self.m, 'version', return_value=semver + '+abc1234'), \
                patch('builtins.print'):
            self.m.write_launcher_release(folder, zip_path)
        manifest = update.parse_manifest((zip_path.parent / update.MANIFEST).read_bytes())
        self.assertEqual((manifest.tag, manifest.version), ('v' + semver, semver + '+abc1234'))
        self.assertEqual(manifest.tools.sha256, sha(b'PK zip'))
        self.assertEqual(manifest.tools.top, 'DKB-Setup')
        self.assertEqual((zip_path.parent / self.m.LAUNCHER).read_bytes(), b'MZ launcher')
        self.assertEqual(manifest.launcher.sha256, sha(b'MZ launcher'))


if __name__ == '__main__':
    unittest.main()
