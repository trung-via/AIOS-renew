"""Enter the existing AIOS Operator from one explicitly selected source tree.

This bootstrap deliberately changes import resolution only in this process.  It
does not set PYTHONPATH, install a package, interpret lifecycle state, or choose
an operation or Executor.
"""

from __future__ import annotations

import importlib
from importlib.machinery import ModuleSpec, SourceFileLoader
import importlib.util
from pathlib import Path
import sys
from types import FunctionType, ModuleType


def _source_spec(specification, name: str, source_package_root: Path) -> None:
    """Accept only the selected package's ordinary Python source loader."""
    relative = Path(*name.split("."))
    package = isinstance(specification, ModuleSpec) and specification.submodule_search_locations is not None
    expected = source_package_root / (relative / "__init__.py" if package else relative.with_suffix(".py"))
    if (not isinstance(specification, ModuleSpec) or specification.name != name
            or type(specification.origin) is not str
            or not isinstance(specification.loader, SourceFileLoader)
            or Path(specification.origin).absolute() != expected
            or not expected.is_file()
            or expected.resolve(strict=True) != expected
            or specification.loader.name != name
            or Path(specification.loader.path).absolute() != expected
            or package and specification.submodule_search_locations != [str(expected.parent)]):
        raise SystemExit("AIOS module spec did not resolve from the control source: " + name)


def _source_modules(source_package_root: Path) -> None:
    """Reject cached foreign modules before imports can reuse their objects."""
    for name, module in tuple(sys.modules.items()):
        if name != "aios_renew" and not name.startswith("aios_renew."):
            continue
        if not isinstance(module, ModuleType):
            raise SystemExit("AIOS control module unavailable: " + name)
        specification = getattr(module, "__spec__", None)
        _source_spec(specification, name, source_package_root)
        package = specification.submodule_search_locations is not None
        if (module.__name__ != name or type(getattr(module, "__file__", None)) is not str
                or Path(module.__file__).absolute() != Path(specification.origin).absolute()
                or getattr(module, "__loader__", None) is not specification.loader
                or getattr(module, "__package__", None) != (name if package else name.rpartition(".")[0])
                or package and getattr(module, "__path__", None) != specification.submodule_search_locations):
            raise SystemExit("AIOS control module identity mismatch: " + name)
        if "." in name:
            parent, _, child = name.rpartition(".")
            if getattr(sys.modules.get(parent), child, None) is not module:
                raise SystemExit("AIOS control package module substituted: " + name)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        raise SystemExit("usage: aios_control_entry.py CONTROL_SOURCE [AIOS_ARGS ...]")

    source_root = Path(arguments[0]).resolve(strict=True)
    if Path(__file__).resolve(strict=True) != source_root / "scripts/aios_control_entry.py":
        raise SystemExit("control source does not own this entry")
    source_package_root = source_root / "src"
    if not source_package_root.is_dir() or source_package_root.resolve(strict=True) != source_package_root:
        raise SystemExit("control source has no src package root")

    original_path = sys.path[:]
    try:
        _source_modules(source_package_root)
        sys.path.insert(0, str(source_package_root))
        # Authenticate the parent before find_spec may execute its __init__.
        _source_spec(importlib.util.find_spec("aios_renew"), "aios_renew", source_package_root)
        importlib.import_module("aios_renew")
        _source_modules(source_package_root)
        _source_spec(importlib.util.find_spec("aios_renew.operator"), "aios_renew.operator", source_package_root)
        from aios_renew import operator, ka01_admission
        # These existing original proof helpers are bootstrap dependencies,
        # independent of the requested operation or editable TASK subject.
        _source_spec(importlib.util.find_spec("aios_renew.proof_coverage_contract"),
            "aios_renew.proof_coverage_contract", source_package_root)
        import aios_renew.proof_coverage_contract
        _source_spec(importlib.util.find_spec("aios_renew.parallel_verification"),
            "aios_renew.parallel_verification", source_package_root)
        import aios_renew.parallel_verification
        _source_modules(source_package_root)
        for path in ka01_admission.KERNEL_IMPLEMENTATION_PATHS:
            name = "aios_renew." + Path(path).stem
            if sys.modules.get(name) is None:
                raise SystemExit("AIOS original control module unavailable: " + name)
        operator_main = operator.main
        if (not isinstance(operator_main, FunctionType) or operator_main.__module__ != operator.__name__
                or operator_main.__globals__ is not vars(operator)
                or Path(operator_main.__code__.co_filename).resolve(strict=True) != source_package_root / "aios_renew/operator.py"):
            raise SystemExit("AIOS Operator entry identity mismatch")
    finally:
        # The loaded package retains its own package path, while subprocesses
        # receive no control-source import override through their environment.
        sys.path[:] = original_path

    return operator_main(arguments[1:])


if __name__ == "__main__":
    raise SystemExit(main())
