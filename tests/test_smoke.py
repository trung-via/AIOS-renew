from importlib.metadata import version

import aios_renew


def test_package_imports() -> None:
    assert aios_renew.__version__ == version("aios-renew")
