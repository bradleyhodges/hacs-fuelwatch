"""Run the pure API adapter tests without bootstrapping Home Assistant.

Useful on Windows, where Home Assistant's pytest plugin imports Linux-only
modules. This deliberately does not replace the full Linux integration suite.
Run from any directory with the project's development dependencies installed:
    python tools/test_api_adapter.py
"""

import os
import sys
from pathlib import Path
from types import ModuleType


def main() -> int:
    """Load real adapter modules while bypassing only HA's package entrypoint."""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"

    import pytest

    import custom_components

    package = ModuleType("custom_components.fuelwatch_wa")
    package.__path__ = [str(root / "custom_components/fuelwatch_wa")]
    sys.modules[package.__name__] = package
    custom_components.fuelwatch_wa = package
    return pytest.main(
        [
            "--noconftest",
            "-p",
            "pytest_asyncio.plugin",
            "-q",
            str(root / "tests/test_api.py"),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
