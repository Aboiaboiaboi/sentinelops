"""Helpers shared between test modules.

Plain functions rather than fixtures, because conftest fixtures cannot be
imported and these are used inside other helpers as often as inside tests.
"""

import subprocess
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Scan


def git(*args: str, cwd: Path) -> None:
    """Run git, failing loudly. Output is captured so a passing suite stays quiet."""
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def init_repo(path: Path) -> Path:
    """An empty git repository with committer identity set.

    The identity matters: git refuses to commit without one, and CI containers
    have no global config to fall back on.
    """
    path.mkdir(parents=True, exist_ok=True)
    git("init", "--quiet", "--initial-branch=main", cwd=path)
    git("config", "user.email", "test@example.com", cwd=path)
    git("config", "user.name", "Test", cwd=path)
    return path


def commit_all(repo: Path, message: str = "initial") -> None:
    git("add", "-A", cwd=repo)
    git("commit", "--quiet", "-m", message, cwd=repo)


async def reload_scan(session: AsyncSession, scan_id: uuid.UUID) -> Scan:
    """Re-read a scan from the database rather than trusting the ORM's copy.

    The lifecycle transitions run raw UPDATE statements, so any instance held
    from before one is stale by definition.
    """
    session.expire_all()
    return await session.scalar(select(Scan).where(Scan.id == scan_id))


class CloneSettings:
    """Stand-in for Settings exposing only what workers/repo.py reads.

    Lets a test point cloning at tmp_path without touching the real
    configuration or the repos/ directory.
    """

    clone_timeout_seconds = 60
    clone_max_bytes = 10_000_000
    clone_max_files = 5_000

    def __init__(self, root: Path) -> None:
        self.clone_root = root
