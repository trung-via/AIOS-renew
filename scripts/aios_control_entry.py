"""Enter the existing AIOS Operator from one explicitly selected source tree.

This bootstrap deliberately changes import resolution only in this process.  It
does not set PYTHONPATH, install a package, interpret lifecycle state, or choose
an operation or Executor.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        raise SystemExit("usage: aios_control_entry.py CONTROL_SOURCE [AIOS_ARGS ...]")

    source_root = Path(arguments[0]).resolve(strict=True)
    source_package_root = source_root / "src"
    if not source_package_root.is_dir():
        raise SystemExit("control source has no src package root")

    import_path = str(source_package_root)
    sys.path.insert(0, import_path)
    try:
        specification = importlib.util.find_spec("aios_renew.operator")
        if specification is None or specification.origin is None:
            raise SystemExit("control source has no AIOS Operator")
        operator_path = Path(specification.origin).resolve(strict=True)
        if not operator_path.is_relative_to(source_root):
            raise SystemExit("AIOS Operator did not resolve from the control source")
        from aios_renew.operator import main as operator_main
    finally:
        # The loaded package retains its own package path, while subprocesses
        # receive no control-source import override through their environment.
        sys.path.remove(import_path)

    return operator_main(arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
