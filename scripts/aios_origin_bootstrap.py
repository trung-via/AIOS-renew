"""Bounded local page-gesture development entry; never launches AIOS work.

Default stdout remains PAGE_SCOPED_AIOS_SEND_ORIGIN_BOOTSTRAP_V1. Explicit
--diagnostic wraps that same attempt's result with fixed-enum READY attribution.
"""

from aios_renew.origin_bootstrap import main


if __name__ == "__main__":
    raise SystemExit(main())
