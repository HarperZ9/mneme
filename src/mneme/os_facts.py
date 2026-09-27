"""os_facts.py: two facts mneme asks the operating system for.

`known_local_appdata` reads the per-user LocalAppData folder on Windows
through the Known Folder API rather than an environment variable, which any
parent process can set. `pid_alive` tells whether a process id still runs,
so a replay snapshot left by a process that is gone can be swept. An id out
of range answers True (keep the file), because the OS would refuse it or
truncate it to another process's id.
"""
from __future__ import annotations

import os
from pathlib import Path


def known_local_appdata() -> Path:
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    local_appdata = GUID(0xF1B32785, 0x6FBA, 0x4FCF, (ctypes.c_ubyte * 8)(
        0x9D, 0x55, 0x7B, 0x8E, 0x7F, 0x15, 0x70, 0x91))
    get_path = ctypes.windll.shell32.SHGetKnownFolderPath
    get_path.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                         ctypes.POINTER(ctypes.c_wchar_p)]
    get_path.restype = ctypes.c_long
    out = ctypes.c_wchar_p()
    if get_path(ctypes.byref(local_appdata), 0, None, ctypes.byref(out)) != 0:
        raise OSError("SHGetKnownFolderPath(LocalAppData) failed")
    try:
        return Path(out.value)
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))


MAX_PID = 2**31 - 1


def pid_alive(pid: int) -> bool:
    if not 0 < pid <= MAX_PID:
        return True
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5                # access denied: it exists
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == 259                             # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def short_path(path: str) -> str | None:
    """The Windows 8.3 short form of an existing path, or None."""
    if os.name != "nt":
        return None
    import ctypes

    buffer = ctypes.create_unicode_buffer(1024)
    size = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    return buffer.value if 0 < size < len(buffer) else None
