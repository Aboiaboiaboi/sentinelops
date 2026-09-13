"""The broker's allowlist and wire format — the part of app/broker/ that is
pure data and testable on any platform, including Windows, where
socket.AF_UNIX does not exist and server.py's actual listener cannot run.
"""

import pytest

from app.broker.protocol import (
    ALLOWED_IMAGES,
    ImageNotAllowed,
    request_from_spec,
    response_from_result,
    result_from_response,
    spec_from_request,
)
from app.utils.sandbox import SandboxResult, SandboxSpec

GITLEAKS_IMAGE = next(iter(ALLOWED_IMAGES))


def _body(**overrides: object) -> dict:
    body = {
        "image": GITLEAKS_IMAGE,
        "command": ["detect", "--source", "{repo}"],
        "timeout_seconds": 60,
        "repo_path": "/data/repos/scan-1",
    }
    body.update(overrides)
    return body


class TestAllowlist:
    def test_the_five_real_tool_images_are_present(self) -> None:
        """Imported from the tool modules, not retyped — a sixth copy of these
        strings is exactly the drift this project already complains about
        needing to avoid for the pre-pull list in deploy.sh."""
        assert len(ALLOWED_IMAGES) == 5

    def test_an_allowed_image_is_accepted(self) -> None:
        spec, repo_path = spec_from_request(_body())

        assert spec.image == GITLEAKS_IMAGE
        assert repo_path == "/data/repos/scan-1"

    def test_a_non_allowlisted_image_is_refused_before_building_a_spec(self) -> None:
        """The allowlist is the first gate, not a property checked afterward —
        a SandboxSpec is never even constructed for a disallowed image."""
        with pytest.raises(ImageNotAllowed):
            spec_from_request(_body(image="evil/whatever:1.0"))

    def test_a_close_but_wrong_tag_on_an_allowed_image_is_still_refused(self) -> None:
        """Matching is exact-string, not by repository name — swapping the tag
        of an otherwise-allowed image is exactly the kind of thing an
        allowlist exists to catch, not let through on a technicality."""
        base_image, _, _ = GITLEAKS_IMAGE.rpartition(":")
        with pytest.raises(ImageNotAllowed):
            spec_from_request(_body(image=f"{base_image}:latest"))

    def test_an_unpinned_image_is_rejected_even_if_it_were_allowed(self) -> None:
        """Defense in depth: SandboxSpec.__post_init__ enforces this
        independently, so even a bug in ALLOWED_IMAGES could not smuggle
        through a floating tag."""
        with pytest.raises(ValueError, match="latest"):
            SandboxSpec(
                image=f"{GITLEAKS_IMAGE.rpartition(':')[0]}:latest", command=(), timeout_seconds=1
            )


class TestRequestShape:
    def test_a_malformed_body_raises_rather_than_crashing_oddly(self) -> None:
        with pytest.raises(KeyError):
            spec_from_request({"image": GITLEAKS_IMAGE})  # no command, no timeout, no repo_path

    def test_optional_fields_default_the_same_way_sandboxspec_does(self) -> None:
        spec, _ = spec_from_request(_body())

        assert spec.memory_mb == 512
        assert spec.needs_cache is False
        assert spec.environment == ()

    def test_environment_pairs_round_trip_as_tuples(self) -> None:
        spec, _ = spec_from_request(_body(environment=[["HOME", "/tmp"]]))

        assert spec.environment == (("HOME", "/tmp"),)

    def test_repo_path_is_a_plain_string_not_touched_as_a_filesystem_path(self) -> None:
        """It is never resolved or checked for existence here — in the
        named-volume case it is only ever substituted into a tool's argv for
        the path *inside* the tool container. The volume actually mounted is
        fixed by the broker's own startup configuration, not by this value."""
        _, repo_path = spec_from_request(_body(repo_path="/data/repos/../../etc"))

        assert repo_path == "/data/repos/../../etc"
        assert isinstance(repo_path, str)


class TestWireRoundTrip:
    def test_spec_to_request_to_spec(self) -> None:
        original = SandboxSpec(
            image=GITLEAKS_IMAGE,
            command=("detect", "--source", "{repo}"),
            timeout_seconds=60,
            memory_mb=256,
            needs_cache=True,
            environment=(("HOME", "/tmp"),),
        )

        body = request_from_spec(original, "/data/repos/scan-2")
        rebuilt, repo_path = spec_from_request(body)

        assert rebuilt == original
        assert repo_path == "/data/repos/scan-2"

    def test_result_to_response_to_result(self) -> None:
        original = SandboxResult(
            exit_code=1, stdout="findings", stderr="warn", timed_out=False, repo_mount="/data"
        )

        rebuilt = result_from_response(response_from_result(original))

        assert rebuilt == original
        assert rebuilt.truncated is False
