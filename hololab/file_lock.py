"""Cross-platform exclusive locks held until the owning stream closes."""

import os


def lock_exclusive(stream):
    if os.name != "nt":
        import fcntl

        fcntl.flock(stream, fcntl.LOCK_EX)
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    lock = kernel.LockFileEx
    lock.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(OVERLAPPED),
    ]
    lock.restype = wintypes.BOOL
    overlap = OVERLAPPED()
    if not lock(msvcrt.get_osfhandle(stream.fileno()), 2, 0, 1, 0, ctypes.byref(overlap)):
        raise ctypes.WinError(ctypes.get_last_error())
