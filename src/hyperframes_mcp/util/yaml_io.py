"""Reading the YAML config files (``config/``, a profile's bundle).

The config loaders run on request paths (the Studio's options, every reference route, each run's
cost cap), and PyYAML's pure-Python parser takes about ten times as long as libyaml's. This uses
libyaml when PyYAML was built with it and the pure-Python safe loader otherwise; both are safe
loaders (no tag builds a Python object) and both raise ``yaml.YAMLError`` on a syntax error.
"""

from __future__ import annotations

from typing import Any

import yaml

_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load_yaml(text: str) -> Any:
    """The parsed document in ``text`` (None for an empty one), like ``yaml.safe_load``."""
    return yaml.load(text, Loader=_Loader)
