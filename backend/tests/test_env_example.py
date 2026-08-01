"""The documented environment must match the real one.

`.env.example` was silently deleted in one commit and nobody noticed for
several more, because nothing depended on it existing — the app starts fine
without it, since every setting has a default. What breaks is the documented
setup: `cp .env.example .env` on a fresh clone.

These tests make the file load-bearing again.
"""

import re
from pathlib import Path

import pytest

from app.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"

# Settings nobody should be setting per-deployment, so their absence from the
# example is deliberate rather than an oversight. `jwt_algorithm` in particular
# would invalidate every issued token if it changed.
UNDOCUMENTED_ON_PURPOSE = {"APP_NAME", "JWT_ALGORITHM"}


def _documented_keys() -> set[str]:
    return {
        match.group(1)
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if (match := re.match(r"^([A-Z][A-Z0-9_]*)=", line.strip()))
    }


def test_the_example_file_exists() -> None:
    """The quick start says to copy it. It has to be there to copy."""
    assert ENV_EXAMPLE.is_file()


def test_every_setting_is_documented() -> None:
    fields = {name.upper() for name in Settings.model_fields}

    undocumented = fields - _documented_keys() - UNDOCUMENTED_ON_PURPOSE

    assert not undocumented, f"add these to .env.example: {sorted(undocumented)}"


def test_nothing_documented_has_been_removed_from_settings() -> None:
    """The other direction: a variable in the example that no longer exists
    tells somebody to configure something with no effect."""
    fields = {name.upper() for name in Settings.model_fields}

    stale = _documented_keys() - fields

    assert not stale, f"remove these from .env.example: {sorted(stale)}"


def test_the_example_holds_no_real_credentials() -> None:
    """It is committed, so anything that looks like a secret in it is public.

    The GitHub App key especially: it can mint access to every installed user's
    repositories.
    """
    content = ENV_EXAMPLE.read_text(encoding="utf-8")

    for line in content.splitlines():
        if match := re.match(r"^(GITHUB_APP_[A-Z_]*|SECRET_KEY)=(.*)$", line.strip()):
            name, value = match.groups()
            if name == "SECRET_KEY":
                # Deliberately the public development default, which the app
                # refuses to start with in production.
                assert value == "dev-only-insecure-secret-change-me"
            else:
                assert value == "", f"{name} must be blank in a committed file"


@pytest.mark.parametrize(
    "setting",
    ["DATABASE_URL", "REDIS_URL", "SECRET_KEY", "GITHUB_APP_CLIENT_ID"],
)
def test_the_settings_someone_actually_has_to_find_are_present(setting: str) -> None:
    """A spot check on the ones a deployment cannot work without."""
    assert setting in _documented_keys()
