from configparser import ConfigParser
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _source_tree_version() -> str | None:
    current_file = Path(__file__).resolve()
    source_root = current_file.parents[2]
    setup_cfg = source_root / "setup.cfg"
    source_init = source_root / "src" / "cocodex" / "__init__.py"
    if not setup_cfg.exists() or not source_init.exists():
        return None
    if source_init.resolve() != current_file:
        return None
    parser = ConfigParser()
    parser.read(setup_cfg)
    try:
        return parser["metadata"]["version"]
    except KeyError:
        return None


try:
    __version__ = _source_tree_version() or version("cocodex")
except PackageNotFoundError:
    __version__ = "0.0.0"
