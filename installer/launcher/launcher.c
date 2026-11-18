/* DKB-Setup.exe and DKB-Launcher.exe: one program with two jobs (docs/installer.md, "Launcher and updates").

   Beside python\pythonw.exe (the unpacked release zip, or a version folder of the launcher) it is the setup: it starts
   python\pythonw.exe -m installer.gui with this exe's folder as the working directory and passes its own arguments on.

   Anywhere else it is the launcher, the one file a player downloads (DKB-Launcher.exe on the release page). It keeps
   everything in DKB-Recomp beside itself:
       versions\current.txt, previous.txt   the tag that starts, and the one before it (installer/update.py)
       versions\<tag>\                      an unpacked release zip
       downloads\                           the zip while it downloads, deleted once unpacked
   and starts versions\<current>\python\pythonw.exe -m installer.gui --launcher <this exe> --base <DKB-Recomp> and its
   own arguments; when that folder lacks its Python (an antivirus removed it), previous.txt's. The window does every
   update from then on, this exe's own replacement included. Only when no version is there (the first start) does this
   program fetch one itself: dkb-release.txt of the latest release, then the tools zip it names, written to
   <sha256>.zip.part and kept only when its size and SHA-256 match; Windows' tar.exe unpacks it into versions\<tag>.part,
   which becomes versions\<tag> once it holds the Python and the VERSION the manifest names. Nothing downloaded runs
   before that check. A leftover <this exe>.old (the update renames the running exe away) is deleted at start.

   Strings come from the catalog (installer/text/<lang>.json, ids launcher.* and dkb.launcher.*), with {name} holes this
   file fills; tools/make_release.py writes them into launcher_text.h with LAUNCHER_REPO (empty for TotK, which has no
   launcher releases: there the program is only the setup). DKB_LAUNCHER_BASE replaces the DKB-Recomp folder, and
   DKB_LAUNCHER_MANIFEST and DKB_LAUNCHER_DOWNLOAD the two GitHub links (http only to this machine), for tests.
   Build: llvm/bin/llvm-windres -I runtime -O coff launcher.rc -o launcher_rc.o, then
   llvm/bin/clang -O2 -mwindows -municode -Wl,--dependent-load-flag=0x800 launcher.c launcher_rc.o -lwinhttp -lbcrypt
   -lcomctl32 -o DKB-Setup.exe. The flag (LOAD_LIBRARY_SEARCH_SYSTEM32) makes Windows load winhttp.dll and bcrypt.dll,
   which are not KnownDLLs, from System32 only, never from the folder the exe was downloaded to. */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <winhttp.h>
#include <bcrypt.h>
#include <commctrl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wchar.h>

#include "launcher_text.h"

#define LANGID_RUSSIAN 0x19
#define PATH_CHARS 1024
#define CMD_CHARS 8192
#define TEXT_CHARS 2048
#define MANIFEST_LIMIT (64u << 10)
#define TOOLS_LIMIT (8ull << 30)
#define WM_STATUS (WM_APP + 1)
#define WM_DONE (WM_APP + 2)

static int russian;
#define T(id) (russian ? LAUNCHER_##id##_RU : LAUNCHER_##id##_EN)

static HWND window, status_text, status_bar;
static wchar_t status_line[TEXT_CHARS];
static volatile LONG status_percent = -1, cancelled;

/* ---- small helpers ---------------------------------------------------------------------------------------------- */

/* tmpl with each {name} replaced by its value (pairs: name, value, ..., NULL); other text is copied as it is, so a
   catalog string is never a format string */
static void fill(wchar_t *out, size_t cap, const wchar_t *tmpl, const wchar_t *const *pairs)
{
    size_t n = 0;
    while (*tmpl && n + 1 < cap) {
        const wchar_t *end = *tmpl == L'{' ? wcschr(tmpl, L'}') : NULL;
        const wchar_t *value = NULL;
        for (const wchar_t *const *p = pairs; end && p && p[0]; p += 2)
            if (wcslen(p[0]) == (size_t)(end - tmpl - 1) && !wcsncmp(p[0], tmpl + 1, (size_t)(end - tmpl - 1))) {
                value = p[1];
                break;
            }
        if (!value) {
            out[n++] = *tmpl++;
            continue;
        }
        while (*value && n + 1 < cap) out[n++] = *value++;
        tmpl = end + 1;
    }
    out[n] = 0;
}

static void say(const wchar_t *text, UINT flags)
{
    MessageBoxW(window, text, T(TITLE), flags);
}

static void say_with(const wchar_t *tmpl, const wchar_t *name, const wchar_t *value)
{
    wchar_t text[TEXT_CHARS];
    const wchar_t *pairs[] = {name, value, NULL};
    fill(text, TEXT_CHARS, tmpl, pairs);
    say(text, MB_OK | MB_ICONERROR);
}

static int is_file(const wchar_t *path)
{
    DWORD a = GetFileAttributesW(path);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static int is_dir(const wchar_t *path)
{
    DWORD a = GetFileAttributesW(path);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

/* A folder and what it holds; a link inside is removed, never followed */
static void remove_tree(const wchar_t *dir)
{
    wchar_t pattern[PATH_CHARS], path[PATH_CHARS];
    WIN32_FIND_DATAW fd;
    if (swprintf(pattern, PATH_CHARS, L"%ls\\*", dir) < 0) return;
    HANDLE h = FindFirstFileW(pattern, &fd);
    if (h != INVALID_HANDLE_VALUE) {
        do {
            if (!wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L"..")) continue;
            if (swprintf(path, PATH_CHARS, L"%ls\\%ls", dir, fd.cFileName) < 0) continue;
            if (fd.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) {
                if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) RemoveDirectoryW(path);
                else DeleteFileW(path);
            } else if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                remove_tree(path);
            } else {
                SetFileAttributesW(path, FILE_ATTRIBUTE_NORMAL);
                DeleteFileW(path);
            }
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    RemoveDirectoryW(dir);
}

/* The first line of a small text file, ASCII, at most cap - 1 characters */
static void read_line(const wchar_t *path, char *out, int cap)
{
    out[0] = 0;
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    DWORD got = 0;
    if (!ReadFile(f, out, (DWORD)cap - 1, &got, NULL)) got = 0;
    CloseHandle(f);
    out[got] = 0;
    for (char *p = out; *p; p++)
        if (*p == '\r' || *p == '\n') { *p = 0; break; }
}

/* text into path through path.tmp, so a stop half way never leaves a cut-off file */
static int write_file(const wchar_t *path, const char *text)
{
    wchar_t tmp[PATH_CHARS];
    DWORD put = 0;
    if (swprintf(tmp, PATH_CHARS, L"%ls.tmp", path) < 0) return 0;
    HANDLE f = CreateFileW(tmp, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    if (f == INVALID_HANDLE_VALUE) return 0;
    int ok = WriteFile(f, text, (DWORD)strlen(text), &put, NULL) && put == strlen(text) && FlushFileBuffers(f);
    CloseHandle(f);
    return ok && MoveFileExW(tmp, path, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
}

/* A release tag as the manifest and the version pointers write it: v and up to four dotted numbers */
static int valid_tag(const char *t)
{
    size_t n = strlen(t);
    if (n < 2 || n > 31 || t[0] != 'v' || t[1] < '0' || t[1] > '9' || t[n - 1] == '.') return 0;
    for (size_t i = 1; i < n; i++)
        if (!((t[i] >= '0' && t[i] <= '9') || (t[i] == '.' && t[i - 1] != '.'))) return 0;
    return 1;
}

/* A file name of the release (no folder, no quote, no space): letters, digits and ._+- */
static int valid_name(const char *s)
{
    size_t n = strlen(s);
    if (!n || n > 127 || s[0] == '.' || s[0] == '-') return 0;
    for (size_t i = 0; i < n; i++)
        if (!((s[i] >= '0' && s[i] <= '9') || (s[i] >= 'a' && s[i] <= 'z') || (s[i] >= 'A' && s[i] <= 'Z') ||
              s[i] == '.' || s[i] == '_' || s[i] == '+' || s[i] == '-'))
            return 0;
    return 1;
}

static void widen(const char *s, wchar_t *out, int cap)
{
    if (!MultiByteToWideChar(CP_UTF8, 0, s, -1, out, cap)) out[0] = 0;
}

/* ---- starting a version ----------------------------------------------------------------------------------------- */

/* folder\python\pythonw.exe -m installer.gui <head> <args>, in folder. 0 when that Python is not there */
static int start_python(const wchar_t *folder, const wchar_t *head, const wchar_t *args)
{
    wchar_t python[PATH_CHARS], cmd[CMD_CHARS];
    STARTUPINFOW si = {sizeof si};
    PROCESS_INFORMATION pi;
    if (swprintf(python, PATH_CHARS, L"%ls\\python\\pythonw.exe", folder) < 0 || !is_file(python)) return 0;
    if (swprintf(cmd, CMD_CHARS, L"\"%ls\" -m installer.gui%ls%ls%ls%ls", python, head[0] ? L" " : L"", head,
                 args[0] ? L" " : L"", args) < 0)
        return 0;
    if (!CreateProcessW(python, cmd, NULL, NULL, FALSE, 0, NULL, folder, &si, &pi)) return 0;
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 1;
}

/* The version named by base\versions\<pointer>.txt */
static int start_installed(const wchar_t *base, const wchar_t *pointer, const wchar_t *head, const wchar_t *args)
{
    wchar_t path[PATH_CHARS], wtag[64];
    char tag[64];
    if (swprintf(path, PATH_CHARS, L"%ls\\versions\\%ls.txt", base, pointer) < 0) return 0;
    read_line(path, tag, sizeof tag);
    if (!valid_tag(tag)) return 0;
    widen(tag, wtag, 64);
    if (swprintf(path, PATH_CHARS, L"%ls\\versions\\%ls", base, wtag) < 0) return 0;
    return start_python(path, head, args);
}

/* ---- HTTPS ------------------------------------------------------------------------------------------------------ */

typedef int (*Sink)(void *user, const void *data, DWORD n);
static DWORD http_status;   /* the last answer's status; 0 when no server answered */

/* GET url into sink, at most limit bytes, following redirects (a release's download link goes to GitHub's file host),
   never from https down to http. http is accepted for this machine only (the tests' local server). 1 when the whole
   answer arrived with status 200 */
static int http_get(const wchar_t *url, Sink sink, void *user, unsigned long long limit, unsigned long long expect)
{
    URL_COMPONENTS uc = {sizeof uc};
    wchar_t host[256], path[2048];
    uc.lpszHostName = host, uc.dwHostNameLength = 256;
    uc.lpszUrlPath = path, uc.dwUrlPathLength = 2048;
    http_status = 0;
    if (!WinHttpCrackUrl(url, 0, 0, &uc)) return 0;
    if (uc.nScheme != INTERNET_SCHEME_HTTPS && wcscmp(host, L"127.0.0.1") && wcscmp(host, L"localhost")) return 0;
    HINTERNET s = WinHttpOpen(L"dkb-recomp-launcher", WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY, NULL, NULL, 0);
    if (!s) s = WinHttpOpen(L"dkb-recomp-launcher", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, NULL, NULL, 0);
    if (!s) return 0;
    WinHttpSetTimeouts(s, 30000, 30000, 30000, 60000);   /* a stalled download fails after a minute without data */
    DWORD policy = WINHTTP_OPTION_REDIRECT_POLICY_DISALLOW_HTTPS_TO_HTTP;
    WinHttpSetOption(s, WINHTTP_OPTION_REDIRECT_POLICY, &policy, sizeof policy);
    HINTERNET c = WinHttpConnect(s, host, uc.nPort, 0);
    HINTERNET r = c ? WinHttpOpenRequest(c, L"GET", path, NULL, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES,
                                         uc.nScheme == INTERNET_SCHEME_HTTPS ? WINHTTP_FLAG_SECURE : 0)
                    : NULL;
    int ok = 0;
    if (r && WinHttpSendRequest(r, WINHTTP_NO_ADDITIONAL_HEADERS, 0, WINHTTP_NO_REQUEST_DATA, 0, 0, 0) &&
        WinHttpReceiveResponse(r, NULL)) {
        DWORD code = 0, size = sizeof code;
        WinHttpQueryHeaders(r, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX, &code,
                            &size, WINHTTP_NO_HEADER_INDEX);
        http_status = code;
        if (code == 200) {
            static char buf[1 << 16];
            unsigned long long got = 0;
            ok = 1;
            for (;;) {
                DWORD n = 0;
                if (cancelled || !WinHttpReadData(r, buf, sizeof buf, &n)) { ok = 0; break; }
                if (!n) break;
                got += n;
                if (got > limit || !sink(user, buf, n)) { ok = 0; break; }
                if (expect) {
                    InterlockedExchange(&status_percent, (LONG)(got * 100 / expect));
                    PostMessageW(window, WM_STATUS, 0, 0);
                }
            }
        }
    }
    if (r) WinHttpCloseHandle(r);
    if (c) WinHttpCloseHandle(c);
    WinHttpCloseHandle(s);
    return ok;
}

typedef struct { char data[MANIFEST_LIMIT + 1]; DWORD n; } Text;

static int text_sink(void *user, const void *data, DWORD n)
{
    Text *t = (Text *)user;
    if (t->n + n > MANIFEST_LIMIT) return 0;
    memcpy(t->data + t->n, data, n);
    t->n += n;
    t->data[t->n] = 0;
    return 1;
}

typedef struct { HANDLE file; BCRYPT_HASH_HANDLE hash; } FileSink;

static int file_sink(void *user, const void *data, DWORD n)
{
    FileSink *f = (FileSink *)user;
    DWORD put = 0;
    return WriteFile(f->file, data, n, &put, NULL) && put == n && BCryptHashData(f->hash, (PUCHAR)data, n, 0) == 0;
}

/* ---- the manifest (dkb-release.txt, installer/update.py parse_manifest) ----------------------------------------- */

typedef struct {
    char version[64], tag[64], sha[65], name[128], top[128];
    unsigned long long size;
} Manifest;

static int parse_manifest(char *text, Manifest *m)
{
    int header = 0, have = 0;
    memset(m, 0, sizeof *m);
    for (char *line = strtok(text, "\r\n"); line; line = strtok(NULL, "\r\n")) {
        char word[16] = "";
        int skip = 0;
        if (!header) {
            if (strcmp(line, "dkb-release 1")) return 0;
            header = 1;
            continue;
        }
        if (sscanf(line, " %15s%n", word, &skip) != 1) continue;
        const char *rest = line + skip;
        int bit = !strcmp(word, "version") ? 1 : !strcmp(word, "tag") ? 2 : !strcmp(word, "tools") ? 4 : 0;
        if (!bit) continue;   /* (another field: for a later format) */
        if (have & bit) return 0;   /* twice: as installer/update.py, not trusted */
        have |= bit;
        if (bit == 1 && sscanf(rest, " %63s", m->version) != 1) return 0;
        if (bit == 2 && sscanf(rest, " %63s", m->tag) != 1) return 0;
        if (bit == 4 && sscanf(rest, " %64s %llu %127s %127s", m->sha, &m->size, m->name, m->top) != 4) return 0;
    }
    size_t numbers = strlen(m->tag) - 1;   /* version 0.4.2+abc1234 belongs to tag v0.4.2 */
    if (have != 7 || !valid_tag(m->tag) || !valid_name(m->version) || !valid_name(m->name) || !valid_name(m->top) ||
        strncmp(m->version, m->tag + 1, numbers) || (m->version[numbers] && m->version[numbers] != '+') ||
        strlen(m->sha) != 64 || !m->size || m->size > TOOLS_LIMIT)
        return 0;
    for (int i = 0; i < 64; i++)
        if (!((m->sha[i] >= '0' && m->sha[i] <= '9') || (m->sha[i] >= 'a' && m->sha[i] <= 'f'))) return 0;
    return 1;
}

/* ---- the first install ------------------------------------------------------------------------------------------ */

static void status(const wchar_t *text, LONG percent)
{
    wcsncpy(status_line, text, TEXT_CHARS - 1);
    InterlockedExchange(&status_percent, percent);
    PostMessageW(window, WM_STATUS, 0, 0);
}

typedef struct {
    wchar_t base[PATH_CHARS];
    wchar_t error[TEXT_CHARS];   /* why it stopped, for the message box; empty after a cancel */
    int ok;
} Job;

static void job_fail(Job *job, const wchar_t *tmpl, const wchar_t *name, const wchar_t *value)
{
    const wchar_t *pairs[] = {name, value, NULL};
    if (!cancelled) fill(job->error, TEXT_CHARS, tmpl, pairs);
}

static const wchar_t *env_or(const wchar_t *name, const wchar_t *fallback, wchar_t *buf, DWORD cap)
{
    DWORD n = GetEnvironmentVariableW(name, buf, cap);
    return n && n < cap ? buf : fallback;
}

static int sha_hex(BCRYPT_HASH_HANDLE h, char out[65])
{
    unsigned char d[32];
    if (BCryptFinishHash(h, d, 32, 0)) return 0;
    for (int i = 0; i < 32; i++) sprintf(out + 2 * i, "%02x", d[i]);
    return 1;
}

/* The tools zip of m into zip (through zip.part), checked against the manifest's size and SHA-256 */
static int fetch_tools(Job *job, const Manifest *m, const wchar_t *url, const wchar_t *zip)
{
    wchar_t part[PATH_CHARS], line[TEXT_CHARS], total[32], wtag[64];
    swprintf(part, PATH_CHARS, L"%ls.part", zip);
    swprintf(total, 32, L"%llu", (m->size + (1 << 20) - 1) >> 20);
    widen(m->tag, wtag, 64);
    const wchar_t *pairs[] = {L"tag", wtag, L"total", total, NULL};
    for (int attempt = 0; attempt < 3 && !cancelled; attempt++) {
        if (attempt) Sleep(3000u << attempt);
        fill(line, TEXT_CHARS, T(DOWNLOADING), pairs);
        status(line, 0);
        BCRYPT_ALG_HANDLE alg;
        FileSink sink;
        if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_SHA256_ALGORITHM, NULL, 0)) break;
        if (BCryptCreateHash(alg, &sink.hash, NULL, 0, NULL, 0, 0)) { BCryptCloseAlgorithmProvider(alg, 0); break; }
        sink.file = CreateFileW(part, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_FLAG_SEQUENTIAL_SCAN, NULL);
        if (sink.file == INVALID_HANDLE_VALUE) {
            BCryptDestroyHash(sink.hash);
            BCryptCloseAlgorithmProvider(alg, 0);
            job_fail(job, T(WRITE_FAILED), L"path", part);
            return 0;
        }
        LARGE_INTEGER size = {0};
        int got = http_get(url, file_sink, &sink, m->size, m->size);
        got = got && GetFileSizeEx(sink.file, &size) && (unsigned long long)size.QuadPart == m->size;
        CloseHandle(sink.file);
        char have[65] = "";
        int hashed = sha_hex(sink.hash, have);
        BCryptDestroyHash(sink.hash);
        BCryptCloseAlgorithmProvider(alg, 0);
        if (got && hashed && !strcmp(have, m->sha)) return MoveFileExW(part, zip, MOVEFILE_REPLACE_EXISTING);
        DeleteFileW(part);
        if (got) {   /* the whole file came and it is not the release's: nothing of it is kept, nothing runs */
            job_fail(job, T(CHECKSUM), NULL, NULL);
            return 0;
        }
        if (http_status && http_status != 200 && http_status < 500 && http_status != 429) break;
    }
    wchar_t code[32];
    swprintf(code, 32, L"HTTP %lu", http_status);
    job_fail(job, T(NETWORK), L"detail", http_status ? code : L"no answer");
    return 0;
}

static int install_job(Job *job)
{
    wchar_t url[PATH_CHARS * 2], override[PATH_CHARS], versions[PATH_CHARS], downloads[PATH_CHARS];
    static Text text;
    Manifest m;
    swprintf(versions, PATH_CHARS, L"%ls\\versions", job->base);
    swprintf(downloads, PATH_CHARS, L"%ls\\downloads", job->base);
    status(T(CHECKING), -1);
    const wchar_t *manifest = env_or(L"DKB_LAUNCHER_MANIFEST",
                                     L"https://github.com/" LAUNCHER_REPO L"/releases/latest/download/dkb-release.txt",
                                     override, PATH_CHARS);
    text.n = 0;
    if (!http_get(manifest, text_sink, &text, MANIFEST_LIMIT, 0)) {
        if (http_status == 404) job_fail(job, T(NO_RELEASE), L"url", L"https://github.com/" LAUNCHER_REPO L"/releases");
        else {
            wchar_t code[32];
            swprintf(code, 32, L"HTTP %lu", http_status);
            job_fail(job, T(NETWORK), L"detail", http_status ? code : L"no answer");
        }
        return 0;
    }
    if (!parse_manifest(text.data, &m)) {
        job_fail(job, T(NO_RELEASE), L"url", L"https://github.com/" LAUNCHER_REPO L"/releases");
        return 0;
    }
    wchar_t wtag[64], wname[128], wtop[128], wsha[65], escaped[256];
    widen(m.tag, wtag, 64), widen(m.name, wname, 128), widen(m.top, wtop, 128), widen(m.sha, wsha, 65);
    size_t k = 0;   /* the '+' of a version is %2B in GitHub's links */
    for (const wchar_t *p = wname; *p && k + 4 < 256; p++) {
        if (*p == L'+') escaped[k++] = L'%', escaped[k++] = L'2', escaped[k++] = L'B';
        else escaped[k++] = *p;
    }
    escaped[k] = 0;
    wchar_t dl[PATH_CHARS];
    swprintf(url, PATH_CHARS * 2, L"%ls%ls/%ls",
             env_or(L"DKB_LAUNCHER_DOWNLOAD", L"https://github.com/" LAUNCHER_REPO L"/releases/download/", dl, PATH_CHARS),
             wtag, escaped);
    CreateDirectoryW(versions, NULL);
    CreateDirectoryW(downloads, NULL);
    if (!is_dir(versions) || !is_dir(downloads)) {
        job_fail(job, T(WRITE_FAILED), L"path", job->base);
        return 0;
    }
    wchar_t zip[PATH_CHARS], rel[PATH_CHARS], part[PATH_CHARS], top[PATH_CHARS], final[PATH_CHARS], check[PATH_CHARS];
    swprintf(zip, PATH_CHARS, L"%ls\\%ls.zip", downloads, wsha);
    if (!fetch_tools(job, &m, url, zip)) return 0;

    /* Unpacked by Windows' own tar.exe, run in the new folder with the zip as a relative ASCII path, so a user folder
       outside the ANSI code page cannot reach tar's narrow arguments */
    status(T(UNPACKING), -1);
    swprintf(part, PATH_CHARS, L"%ls\\%ls.part", versions, wtag);
    swprintf(final, PATH_CHARS, L"%ls\\%ls", versions, wtag);
    swprintf(top, PATH_CHARS, L"%ls\\%ls", part, wtop);
    remove_tree(part);
    if (!CreateDirectoryW(part, NULL)) {
        job_fail(job, T(WRITE_FAILED), L"path", part);
        return 0;
    }
    wchar_t sys[MAX_PATH], tar[CMD_CHARS];
    GetSystemDirectoryW(sys, MAX_PATH);
    swprintf(rel, PATH_CHARS, L"..\\..\\downloads\\%ls.zip", wsha);
    swprintf(tar, CMD_CHARS, L"\"%ls\\tar.exe\" -xf \"%ls\"", sys, rel);
    STARTUPINFOW si = {sizeof si};
    PROCESS_INFORMATION pi;
    DWORD code = 1;
    si.dwFlags = STARTF_USESHOWWINDOW, si.wShowWindow = SW_HIDE;
    if (CreateProcessW(NULL, tar, NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, part, &si, &pi)) {
        WaitForSingleObject(pi.hProcess, INFINITE);
        GetExitCodeProcess(pi.hProcess, &code);
        CloseHandle(pi.hThread), CloseHandle(pi.hProcess);
    }
    char version[64];
    swprintf(check, PATH_CHARS, L"%ls\\VERSION", top);
    read_line(check, version, sizeof version);
    for (char *p = version; *p; p++)
        if (*p == ' ' || *p == '\t') { *p = 0; break; }
    swprintf(check, PATH_CHARS, L"%ls\\python\\pythonw.exe", top);
    if (code != 0 || strcmp(version, m.version) || !is_file(check)) {
        remove_tree(part);
        DeleteFileW(zip);
        job_fail(job, T(UNPACK_FAILED), L"path", final);
        return 0;
    }
    if (is_dir(final)) {   /* an incomplete folder of the same tag (it had no Python, or this would not run) */
        wchar_t trash[PATH_CHARS];
        swprintf(trash, PATH_CHARS, L"%ls.trash", final);
        remove_tree(trash);
        MoveFileW(final, trash);
        remove_tree(trash);
    }
    if (!MoveFileW(top, final)) {
        remove_tree(part);
        job_fail(job, T(WRITE_FAILED), L"path", final);
        return 0;
    }
    remove_tree(part);
    DeleteFileW(zip);
    char pointer[80];
    wchar_t current[PATH_CHARS];
    snprintf(pointer, sizeof pointer, "%s\n", m.tag);
    swprintf(current, PATH_CHARS, L"%ls\\current.txt", versions);
    if (!write_file(current, pointer)) {
        job_fail(job, T(WRITE_FAILED), L"path", current);
        return 0;
    }
    return 1;
}

static Job job;

static DWORD WINAPI install_thread(LPVOID user)
{
    (void)user;
    job.ok = install_job(&job);
    PostMessageW(window, WM_DONE, 0, 0);
    return 0;
}

static LRESULT CALLBACK window_proc(HWND h, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_STATUS: {
        LONG p = status_percent;
        SetWindowTextW(status_text, status_line);
        if (p < 0) SendMessageW(status_bar, PBM_SETMARQUEE, TRUE, 30);
        else SendMessageW(status_bar, PBM_SETMARQUEE, FALSE, 0), SendMessageW(status_bar, PBM_SETPOS, (WPARAM)p, 0);
        return 0;
    }
    case WM_CLOSE:   /* the download stops at its next block; the window closes when the worker is done */
        InterlockedExchange(&cancelled, 1);
        return 0;
    case WM_DONE:   /* why it stopped, over the progress window, before it closes */
        if (!job.ok && job.error[0]) MessageBoxW(h, job.error, T(TITLE), MB_OK | MB_ICONERROR);
        DestroyWindow(h);
        return 0;
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(h, msg, wp, lp);
}

/* The latest release's tools into base, with a progress window. 0 when it did not get there (already said why) */
static int install_latest(HINSTANCE inst, const wchar_t *base)
{
    wchar_t text[TEXT_CHARS];
    const wchar_t *pairs[] = {L"base", base, NULL};
    fill(text, TEXT_CHARS, T(CONFIRM), pairs);
    if (MessageBoxW(NULL, text, T(TITLE), MB_OKCANCEL | MB_ICONINFORMATION) != IDOK) return 0;
    CreateDirectoryW(base, NULL);
    if (!is_dir(base)) {
        say_with(T(WRITE_FAILED), L"path", base);
        return 0;
    }
    wcsncpy(job.base, base, PATH_CHARS - 1);
    INITCOMMONCONTROLSEX icc = {sizeof icc, ICC_PROGRESS_CLASS};
    InitCommonControlsEx(&icc);
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = window_proc, wc.hInstance = inst, wc.lpszClassName = L"DkbLauncher";
    wc.hIcon = LoadIconW(inst, MAKEINTRESOURCEW(1));
    wc.hCursor = LoadCursorW(NULL, (LPCWSTR)IDC_ARROW);
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    RegisterClassW(&wc);
    const int ww = 520, wh = 140;
    window = CreateWindowW(L"DkbLauncher", T(TITLE), WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX,
                           (GetSystemMetrics(SM_CXSCREEN) - ww) / 2, (GetSystemMetrics(SM_CYSCREEN) - wh) / 2, ww, wh,
                           NULL, NULL, inst, NULL);
    status_text = CreateWindowW(L"STATIC", L"", WS_CHILD | WS_VISIBLE, 16, 16, ww - 48, 22, window, NULL, inst, NULL);
    status_bar = CreateWindowW(PROGRESS_CLASSW, NULL, WS_CHILD | WS_VISIBLE | PBS_MARQUEE, 16, 48, ww - 48, 20, window,
                               NULL, inst, NULL);
    HFONT font = CreateFontW(-15, 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, L"Segoe UI");
    SendMessageW(status_text, WM_SETFONT, (WPARAM)font, TRUE);
    ShowWindow(window, SW_SHOWNORMAL);
    HANDLE worker = CreateThread(NULL, 0, install_thread, NULL, 0, NULL);
    if (!worker) return 0;
    MSG msg;
    while (GetMessageW(&msg, NULL, 0, 0) > 0) TranslateMessage(&msg), DispatchMessageW(&msg);
    WaitForSingleObject(worker, INFINITE);
    CloseHandle(worker);
    window = NULL;
    return job.ok;
}

/* ---- entry ------------------------------------------------------------------------------------------------------ */

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE previous, LPWSTR args, int show)
{
    wchar_t exe[PATH_CHARS], folder[PATH_CHARS], base[PATH_CHARS], head[CMD_CHARS], old[PATH_CHARS];
    (void)previous, (void)show;
    russian = (GetUserDefaultUILanguage() & 0x3ff) == LANGID_RUSSIAN;
    if (!args) args = L"";
    DWORD n = GetModuleFileNameW(NULL, exe, PATH_CHARS);
    if (n == 0 || n >= PATH_CHARS) { say(T(PYTHON_MISSING), MB_OK | MB_ICONERROR); return 1; }
    wcscpy(folder, exe);
    wchar_t *slash = wcsrchr(folder, L'\\');
    if (slash) *slash = 0;

    /* the setup: an unpacked release folder */
    if (start_python(folder, L"", args)) return 0;
    if (!LAUNCHER_REPO[0]) { say(T(PYTHON_MISSING), MB_OK | MB_ICONERROR); return 1; }

    /* the launcher */
    DWORD b = GetEnvironmentVariableW(L"DKB_LAUNCHER_BASE", base, PATH_CHARS);
    if (!b || b >= PATH_CHARS) swprintf(base, PATH_CHARS, L"%ls\\DKB-Recomp", folder);
    for (size_t len = wcslen(base); len > 3 && base[len - 1] == L'\\'; len--) base[len - 1] = 0;   /* "x\" would end the quote */
    swprintf(old, PATH_CHARS, L"%ls.old", exe);
    DeleteFileW(old);   /* still running (an update moments ago): it goes next time */
    swprintf(head, CMD_CHARS, L"--launcher \"%ls\" --base \"%ls\"", exe, base);
    if (start_installed(base, L"current", head, args) || start_installed(base, L"previous", head, args)) return 0;
    if (!install_latest(inst, base)) return 1;   /* cancelled, or it said why */
    if (start_installed(base, L"current", head, args)) return 0;
    wchar_t versions[PATH_CHARS];
    swprintf(versions, PATH_CHARS, L"%ls\\versions", base);
    say_with(T(START_FAILED), L"path", versions);
    return 1;
}
