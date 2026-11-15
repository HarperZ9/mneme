"""wipe.py: overwrite a file's bytes before it is unlinked.

A plain unlink frees a file's disk blocks without changing them, so the bytes
stay readable with an undelete tool until the file system reuses the blocks.
`overwrite` writes zeros over the whole file in place and syncs it, and
`overwrite_and_unlink` then removes it. Mneme does this for the files it
deletes that can hold erased text: replay snapshots, export files an erase
removes, and the rollback journal of an erase (erase_journal.py).

The limit: this replaces the bytes a conventional file system keeps for the
file. A solid-state drive that remaps blocks, a copy-on-write or snapshotting
file system (APFS, Btrfs, ZFS, a volume shadow copy) or a journaling mode that
logs data can keep an older copy that no write to the file reaches. Full-disk
encryption with a destroyed key, or destroying the medium, covers those.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

_CHUNK = 1 << 20


def _is_link(path: Path) -> bool:
    return os.path.islink(path)


def overwrite(path: Path) -> int:
    """Write zeros over a regular file in place and sync. Returns the byte count.

    Raises OSError when the file cannot be opened or written. It never writes
    through a link (checked first, and again by O_NOFOLLOW where the platform
    has it) and never writes to anything but a regular file."""
    if _is_link(path):
        raise OSError(f"{Path(path).name!r} is a link")
    flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError(f"{Path(path).name!r} is not a regular file")
        remaining, zeros = info.st_size, bytes(_CHUNK)
        os.lseek(fd, 0, os.SEEK_SET)
        while remaining > 0:
            written = os.write(fd, zeros[:min(_CHUNK, remaining)])
            if written <= 0:
                raise OSError("overwrite made no forward progress")
            remaining -= written
        os.fsync(fd)
        return info.st_size
    finally:
        os.close(fd)


def overwrite_and_unlink(path: Path) -> bool:
    """Overwrite, then unlink. True when the overwrite ran before the unlink.

    A failed overwrite does not stop the unlink (the file still goes, as it did
    before mneme overwrote anything). The unlink's own error propagates."""
    path = Path(path)
    try:
        overwrite(path)
        done = True
    except FileNotFoundError:
        raise
    except OSError:
        done = False
    path.unlink()
    return done
