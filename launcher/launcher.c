/* AgentDock.exe — portable launcher. No console window, nothing installed outside the AgentDock folder.
 *
 * Ready (runtime\venv exists and was synced with the current uv.lock):
 *     <interpreter the venv was made from>\pythonw.exe AgentDock.pyw [args]
 * Otherwise (first run, or uv.lock changed): run scripts\setup.ps1 in a console that shows progress;
 * it prepares runtime\ and then starts AgentDock itself.
 *
 * Build (any OS with mingw-w64): see launcher/build.sh
 */
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <stdio.h>
#include <wchar.h>

#define BIG 32768

static wchar_t root[MAX_PATH];

static void join(wchar_t *out, const wchar_t *rel) {
    swprintf(out, MAX_PATH, L"%ls\\%ls", root, rel);
}

static BOOL exists(const wchar_t *path) {
    DWORD a = GetFileAttributesW(path);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

static char *slurp(const wchar_t *path, DWORD *size) {
    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return NULL;
    DWORD n = GetFileSize(h, NULL), got = 0;
    char *buf = (char *)HeapAlloc(GetProcessHeap(), 0, n + 1);
    if (buf && (!ReadFile(h, buf, n, &got, NULL) || got != n)) { HeapFree(GetProcessHeap(), 0, buf); buf = NULL; }
    CloseHandle(h);
    if (buf) { buf[n] = 0; *size = n; }
    return buf;
}

static BOOL same_file(const wchar_t *a, const wchar_t *b) {
    DWORD na = 0, nb = 0;
    char *x = slurp(a, &na), *y = slurp(b, &nb);
    BOOL same = x && y && na == nb && memcmp(x, y, na) == 0;
    if (x) HeapFree(GetProcessHeap(), 0, x);
    if (y) HeapFree(GetProcessHeap(), 0, y);
    return same;
}

/* pyvenv.cfg "home = C:\...\cpython-3.13...". UTF-8. */
static BOOL venv_home(const wchar_t *cfg, wchar_t *home) {
    DWORD n = 0;
    char *text = slurp(cfg, &n);
    if (!text) return FALSE;
    BOOL ok = FALSE;
    for (char *line = text; line && *line; ) {
        char *next = strchr(line, '\n');
        if (next) *next++ = 0;
        while (*line == ' ' || *line == '\t') line++;
        if (_strnicmp(line, "home", 4) == 0) {
            char *v = strchr(line, '=');
            if (v) {
                v++;
                while (*v == ' ' || *v == '\t') v++;
                size_t len = strlen(v);
                while (len && (v[len - 1] == '\r' || v[len - 1] == ' ' || v[len - 1] == '\t')) v[--len] = 0;
                ok = MultiByteToWideChar(CP_UTF8, 0, v, -1, home, MAX_PATH) > 0;
            }
            break;
        }
        line = next;
    }
    HeapFree(GetProcessHeap(), 0, text);
    return ok;
}

/* Everything after argv[0] in the original command line. */
static const wchar_t *tail_args(void) {
    const wchar_t *p = GetCommandLineW();
    if (*p == L'"') { p++; while (*p && *p != L'"') p++; if (*p) p++; }
    else { while (*p && *p != L' ' && *p != L'\t') p++; }
    while (*p == L' ' || *p == L'\t') p++;
    return p;
}

static BOOL run(wchar_t *cmd, DWORD flags) {
    STARTUPINFOW si = {sizeof si};
    PROCESS_INFORMATION pi;
    if (!CreateProcessW(NULL, cmd, NULL, NULL, FALSE, flags, NULL, root, &si, &pi)) return FALSE;
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return TRUE;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR line, int show) {
    (void)inst; (void)prev; (void)line; (void)show;
    GetModuleFileNameW(NULL, root, MAX_PATH);
    wchar_t *slash = wcsrchr(root, L'\\');
    if (slash) *slash = 0;

    wchar_t lock[MAX_PATH], synced[MAX_PATH], cfg[MAX_PATH], pyw[MAX_PATH], home[MAX_PATH], script[MAX_PATH];
    join(lock, L"uv.lock");
    join(synced, L"runtime\\venv\\agentdock-synced.lock");
    join(cfg, L"runtime\\venv\\pyvenv.cfg");
    join(pyw, L"AgentDock.pyw");
    static wchar_t cmd[BIG];

    if (same_file(lock, synced) && venv_home(cfg, home)) {
        wchar_t exe[MAX_PATH];
        swprintf(exe, MAX_PATH, L"%ls\\pythonw.exe", home);
        if (exists(exe)) {
            swprintf(cmd, BIG, L"\"%ls\" \"%ls\" %ls", exe, pyw, tail_args());
            if (run(cmd, DETACHED_PROCESS)) return 0;
        }
    }
    join(script, L"scripts\\setup.ps1");
    swprintf(cmd, BIG, L"powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"%ls\" %ls", script, tail_args());
    if (run(cmd, CREATE_NEW_CONSOLE)) return 0;
    MessageBoxW(NULL, L"無法啟動 AgentDock：找不到 PowerShell 或 scripts\\setup.ps1。", L"AgentDock", MB_ICONERROR);
    return 1;
}
