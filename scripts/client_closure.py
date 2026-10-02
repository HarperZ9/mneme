"""The modules of a package that a client entry point can import.

The client plugin ships only these, so code the server never loads (the CLI,
the optional model extractor) stays out of the plugin folder. The walk is
static: it follows every import statement in every reachable module, at module
level or inside a function, absolute (`package.x`) or relative (`.x`)."""
import ast
from pathlib import Path


def _imported(tree, package, home):
    """Module names under `package` imported by a module whose own package is `home`."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = home.split('.')
                base = '.'.join(parts[:len(parts) - (node.level - 1)])
                module = f'{base}.{node.module}' if node.module else base
            else:
                module = node.module or ''
            found.add(module)
            # `from package import name` may name a submodule.
            found.update(f'{module}.{alias.name}' for alias in node.names)
    return {name for name in found if name == package or name.startswith(package + '.')}


def _path(source, module):
    candidate = Path(source, *module.split('.')[1:])
    if (candidate / '__init__.py').is_file():
        return candidate / '__init__.py'
    candidate = candidate.with_suffix('.py')
    return candidate if candidate.is_file() else None


def closure(source, package, entry):
    """Relative file names in `source` (the package folder) reachable from `entry`."""
    pending = _imported(ast.parse(Path(entry).read_text(encoding='utf-8-sig')), package, package)
    seen, files = set(), set()
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _path(source, module)
        if path is None:
            continue  # an imported name that is not a module, such as a function
        files.add(path.relative_to(source).as_posix())
        parent = module.rpartition('.')[0]
        if parent:
            pending.add(parent)  # importing package.x runs package/__init__.py first
        home = module if path.name == '__init__.py' else parent
        pending |= _imported(ast.parse(path.read_text(encoding='utf-8-sig')), package, home)
    return files
