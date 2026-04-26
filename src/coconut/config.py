from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path


CONFIG_PATH = Path(".coconut/config.json")


@dataclass(frozen=True)
class CoconutConfig:
    main_branch: str
    verify: str | None
    remote: str | None
    socket_path: str
    worktree_root: str
    dirty_interval_s: float


def find_repo_root(start: Path | None = None) -> Path:
    cwd = Path.cwd() if start is None else start
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "not inside a Git repository")
    return Path(result.stdout.strip()).resolve()


def init_config(
    repo: Path,
    *,
    main_branch: str,
    verify: str | None,
    remote: str | None,
    dirty_interval_s: float = 2.0,
) -> CoconutConfig:
    config = CoconutConfig(
        main_branch=main_branch,
        verify=verify,
        remote=remote,
        socket_path=".coconut/coconut.sock",
        worktree_root=".coconut/worktrees",
        dirty_interval_s=dirty_interval_s,
    )
    coconut_dir = repo / ".coconut"
    coconut_dir.mkdir(exist_ok=True)
    (repo / CONFIG_PATH).write_text(
        json.dumps(asdict(config), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (coconut_dir / "tasks").mkdir(exist_ok=True)
    (coconut_dir / "worktrees").mkdir(exist_ok=True)
    return config


def load_config(repo: Path) -> CoconutConfig:
    path = repo / CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist; run coconut init first")
    data = json.loads(path.read_text(encoding="utf-8"))
    return CoconutConfig(**data)
