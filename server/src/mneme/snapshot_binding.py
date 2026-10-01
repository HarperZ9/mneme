"""Process-local snapshot authority for a client bound to one state file."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import os
from pathlib import Path
import stat

_BOUND_ROOT = ContextVar('mneme_snapshot_root', default=None)


def bound_root():
    """Recheck the selected path before any caller traverses its namespace."""
    root = _BOUND_ROOT.get()
    if root is not None:
        for part in (root, *root.parents):
            if part.is_symlink() or (part.exists() and getattr(part.lstat(),
                    'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
                raise ValueError('linked snapshot authority is not accepted')
    return root


def check_bound_path(path):
    """Refuse linked descendants before listing, creating or removing snapshots."""
    root = bound_root()
    if root is None:
        return
    path = Path(path)
    if path != root and root not in path.parents:
        raise ValueError('path is outside snapshot authority')
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or (part.exists() and getattr(part.lstat(),
                'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            raise ValueError('linked snapshot authority is not accepted')


@contextmanager
def for_state(state):
    """Use only this state's sibling namespace; restore legacy CLI defaults on exit."""
    path = Path(state)
    if not path.is_absolute() or not path.parent.is_dir():
        raise ValueError('snapshot authority requires an absolute state path')
    digest = hashlib.sha256(os.path.normcase(str(path.resolve())).encode()).hexdigest()
    token = _BOUND_ROOT.set(path.parent / ('.mneme-snapshots-' + digest))
    try:
        bound_root()
        yield
    finally:
        _BOUND_ROOT.reset(token)
