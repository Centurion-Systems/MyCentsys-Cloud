"""Make the ``cloud`` protocol package importable without Home Assistant.

The package has no Home Assistant imports, so it is loaded as a plain package
rooted at ``custom_components/mycentsys_cloud/cloud``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_PACKAGE_DIR = Path(__file__).resolve().parents[2] / "custom_components" / "mycentsys_cloud" / "cloud"
_PACKAGE_NAME = "mycentsys_cloud_protocol"

if _PACKAGE_NAME not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        _PACKAGE_NAME, _PACKAGE_DIR / "__init__.py", submodule_search_locations=[str(_PACKAGE_DIR)]
    )
    assert spec and spec.loader
    package = importlib.util.module_from_spec(spec)
    sys.modules[_PACKAGE_NAME] = package
    spec.loader.exec_module(package)
