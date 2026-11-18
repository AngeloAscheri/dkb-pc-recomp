/* Stand-in for a release's python\pythonw.exe in the launcher end-to-end test: appends its command line and working
   folder to the file named by STUB_LOG, then exits 0. */
#include <windows.h>
int WINAPI wWinMain(HINSTANCE a, HINSTANCE b, LPWSTR c, int d)
{
    wchar_t log[1024], cwd[1024], line[8192];
    (void)a, (void)b, (void)c, (void)d;
    if (!GetEnvironmentVariableW(L"STUB_LOG", log, 1024)) return 1;
    GetCurrentDirectoryW(1024, cwd);
    wsprintfW(line, L"%s\t%s\n", cwd, GetCommandLineW());
    char u[16384];
    int n = WideCharToMultiByte(CP_UTF8, 0, line, -1, u, sizeof u, NULL, NULL);
    HANDLE f = CreateFileW(log, FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_ALWAYS, 0, NULL);
    DWORD put;
    if (f != INVALID_HANDLE_VALUE) WriteFile(f, u, n - 1, &put, NULL), CloseHandle(f);
    return 0;
}
