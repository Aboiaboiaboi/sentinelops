"""Deployment checks.

How does this thing get built and shipped, and does the artefact it produces
behave itself once it is running.

The Dockerfile and Compose parsing this scanner drives lives in parsing.py,
split out once this file passed the 600-line threshold architecture.file_size
applies to everyone else — the checks below are what to do with a parse, not
how to produce one.
"""

from pathlib import Path

from app.scanners.base import (
    CheckResult,
    CheckSpec,
    RepositoryIndex,
    ScanFinding,
    Severity,
    failed,
    passed,
    skipped,
)
from app.scanners.deployment.parsing import (
    COMPOSE_NAMES,
    compose_unpinned_images,
    copies_full_context,
    host_access,
    is_dockerfile,
    is_orchestration,
    is_pinned,
    parse_stages,
    runs_as_root,
    shell_form_entry,
)

CATEGORY = "deployment"

_CONFIG = CheckSpec("deployment.config", "Deployment configuration")
_PINNING = CheckSpec("deployment.image_pinning", "Pinned base images")
_NON_ROOT = CheckSpec("deployment.non_root", "Container drops privileges")
_HEALTHCHECK = CheckSpec("deployment.healthcheck", "Container healthcheck")
_DOCKERIGNORE = CheckSpec("deployment.dockerignore", "Build context excluded")
_SIGNALS = CheckSpec("deployment.signal_handling", "Container receives stop signals")
_PRIVILEGED = CheckSpec("deployment.privileged", "Host isolation preserved")
_CI = CheckSpec("deployment.ci", "CI pipeline")

# The image checks read Dockerfile stages, so without one there is nothing to
# read — a repository whose image is built elsewhere is not failing them.
_NO_DOCKERFILE = "no Dockerfile was found to inspect"

# Impacts. Both paths through the checks below total the category weight of 15:
# a repository with no deployment config at all loses 12 + 3, and one with a
# Dockerfile and orchestration can lose 3 + 4 + 1 + 3 + 1 + 2 + 1.
#
# Rebalanced when the signal-handling and privileged checks were added. The
# weight has to come from somewhere, and it came from the two findings about
# what ends up in the image rather than from the two about reproducibility and
# privilege — a wedged container and a fat layer are both recoverable, and
# neither is somebody owning the host.
_NO_DEPLOYMENT_CONFIG = 12
_UNPINNED_BASE_IMAGE = 3
_RUNS_AS_ROOT = 4
_NO_HEALTHCHECK = 1
_NO_CI_PIPELINE = 3
_NO_DOCKERIGNORE = 1
_PRIVILEGED_CONTAINER = 2
_SHELL_FORM_ENTRYPOINT = 1

# Only these get read for the privilege check. A directory can be recognised as
# orchestration because of a README sitting in it; a manifest is YAML.
_MANIFEST_SUFFIXES = frozenset({".yml", ".yaml"})

# A ceiling on how many manifests are read. A Helm chart or a Kustomize tree can
# hold hundreds of templates, and the question here is answered by the first
# offending line — reading the whole tree would cost more than the answer is
# worth. The same reasoning as MAX_READ_BYTES one level up.
_MAX_MANIFESTS = 50

# CI providers that keep their config in a directory. Matched on the path
# prefix so that an *empty* .github/workflows/ does not count — a directory with
# no workflow in it runs nothing.
_CI_DIRECTORY_PREFIXES = (".github/workflows/", ".circleci/")

# Providers that use a single file at the repository root.
_CI_ROOT_FILES = (
    ".gitlab-ci.yml",
    ".gitlab-ci.yaml",
    "jenkinsfile",
    "azure-pipelines.yml",
    ".drone.yml",
    "bitbucket-pipelines.yml",
    ".travis.yml",
    ".woodpecker.yml",
)


class DeploymentScanner:
    category = CATEGORY
    CHECKS = (_CONFIG, _PINNING, _NON_ROOT, _HEALTHCHECK, _DOCKERIGNORE, _SIGNALS, _PRIVILEGED, _CI)

    def scan(self, repo: RepositoryIndex) -> list[CheckResult]:
        # Both questions answered in one pass over the already-built index.
        # This used to be two separate walks of the tree, which on top of the
        # other scanners' walks meant traversing the same repository roughly a
        # dozen times per scan.
        dockerfiles: list[Path] = []
        compose_files: list[Path] = []
        manifest_files: list[Path] = []
        has_orchestration = False
        for path in repo.files:
            if is_dockerfile(path):
                dockerfiles.append(path)
                continue
            if path.name.lower() in COMPOSE_NAMES:
                compose_files.append(path)
                has_orchestration = True
                continue
            # No longer short-circuited once orchestration is found: the
            # privilege check needs the manifests themselves, not just the fact
            # that some exist. The test is string work on an already-walked
            # path, so it costs no additional I/O.
            if is_orchestration(path, repo.path):
                has_orchestration = True
                if (
                    path.suffix.lower() in _MANIFEST_SUFFIXES
                    and len(manifest_files) < _MAX_MANIFESTS
                ):
                    manifest_files.append(path)

        results: list[CheckResult] = []
        if not dockerfiles and not has_orchestration:
            results.append(failed(_CONFIG, self._no_deployment_config()))
            # Nothing describes the deployment, so there is nothing to inspect
            # for pinning, privileges or build context. Reporting those as
            # passed would credit a repository for a file it does not have.
            no_config = "no deployment configuration was found to inspect"
            results.extend(
                skipped(check, no_config)
                for check in (_PINNING, _NON_ROOT, _HEALTHCHECK, _DOCKERIGNORE, _SIGNALS)
            )
        else:
            results.append(passed(_CONFIG))
            results.extend(self._check_images(dockerfiles, compose_files, repo))

        # Outside the branch: it reads orchestration rather than the image, and
        # it answers for itself when there is none.
        results.append(self._check_privileged(compose_files, manifest_files, repo))
        results.append(self._check_ci(repo))
        return results

    def _no_deployment_config(self) -> ScanFinding:
        return ScanFinding(
            category=CATEGORY,
            severity=Severity.HIGH,
            title="No deployment configuration",
            description=(
                "No Dockerfile, Compose file, or Kubernetes manifest was found. Nothing in the "
                "repository describes how the service is packaged or run, so how it reaches an "
                "environment lives only in somebody's shell history."
            ),
            recommendation=(
                "Add a Dockerfile describing how the service is built and started, so the same "
                "artefact runs locally and in every environment."
            ),
            score_impact=_NO_DEPLOYMENT_CONFIG,
        )

    def _check_images(
        self, dockerfiles: list[Path], compose_files: list[Path], repo: RepositoryIndex
    ) -> list[CheckResult]:
        unpinned: list[str] = []
        root_stages: list[str] = []
        healthchecked = False
        runnable_seen = False
        broad_copy: str | None = None
        shell_entry: tuple[str, str] | None = None

        for path in dockerfiles:
            relative = repo.relative(path)
            stages = parse_stages(repo.read(path))
            names = {s.name.lower() for s in stages if s.name}

            for stage in stages:
                if not is_pinned(stage.base, names):
                    unpinned.append(f"{relative} ({stage.base})")
                if broad_copy is None and copies_full_context(stage):
                    broad_copy = relative
                if not stage.is_runnable:
                    continue
                runnable_seen = True
                if runs_as_root(stage):
                    root_stages.append(f"{relative} ({stage.name or stage.base})")
                if "healthcheck" in stage.instructions:
                    healthchecked = True
                if shell_entry is None:
                    instruction = shell_form_entry(stage)
                    if instruction is not None:
                        shell_entry = (relative, instruction)

        # The same pinning question asked of Compose: `image: postgres` pulls
        # a different database on different days, exactly like a floating FROM.
        for path in compose_files:
            relative = repo.relative(path)
            for image in compose_unpinned_images(repo.read(path)):
                unpinned.append(f"{relative} ({image})")

        results: list[CheckResult] = []

        if not dockerfiles:
            results.append(skipped(_DOCKERIGNORE, _NO_DOCKERFILE))
        elif repo.has_root_entry(".dockerignore"):
            results.append(passed(_DOCKERIGNORE))
        elif broad_copy is None:
            results.append(
                skipped(_DOCKERIGNORE, "nothing copies the whole build context into the image")
            )
        else:
            results.append(
                failed(
                    _DOCKERIGNORE,
                    ScanFinding(
                        category=CATEGORY,
                        severity=Severity.MEDIUM,
                        title="Full build context copied with no .dockerignore",
                        description=(
                            f"{broad_copy} copies the entire directory into the image and there "
                            "is no .dockerignore. Whatever is lying around at build time ships in "
                            "the layers — local env files, the .git history, dependency caches — "
                            "and stays retrievable from the image even if a later layer deletes it."
                        ),
                        recommendation=(
                            "Add a .dockerignore excluding at least .git, .env* and dependency "
                            "directories — or copy only the paths the image actually needs."
                        ),
                        score_impact=_NO_DOCKERIGNORE,
                    ),
                )
            )

        if not unpinned:
            results.append(passed(_PINNING))
        else:
            results.append(
                failed(
                    _PINNING,
                    ScanFinding(
                        category=CATEGORY,
                        severity=Severity.MEDIUM,
                        title="Base image is not pinned",
                        description=(
                            f"{unpinned[0]} uses a floating tag. The same Dockerfile will produce "
                            "different images on different days, so a build that passed CI is not "
                            "necessarily the one that ships."
                        ),
                        recommendation=(
                            "Pin to a specific version, or to a digest with @sha256: for a build "
                            "that is reproducible byte for byte."
                        ),
                        score_impact=_UNPINNED_BASE_IMAGE,
                    ),
                )
            )

        # Privileges and health are properties of the stage that actually ships.
        # A Dockerfile with no runnable stage — a base image others build on —
        # has no process to run unprivileged or to health-check.
        if not dockerfiles:
            results.append(skipped(_NON_ROOT, _NO_DOCKERFILE))
            results.append(skipped(_HEALTHCHECK, _NO_DOCKERFILE))
            results.append(skipped(_SIGNALS, _NO_DOCKERFILE))
            return results

        if not runnable_seen:
            no_process = "no stage in the Dockerfile starts a process"
            results.append(skipped(_NON_ROOT, no_process))
            results.append(skipped(_HEALTHCHECK, no_process))
            results.append(skipped(_SIGNALS, no_process))
            return results

        if not root_stages:
            results.append(passed(_NON_ROOT))
        else:
            results.append(
                failed(
                    _NON_ROOT,
                    ScanFinding(
                        category=CATEGORY,
                        severity=Severity.HIGH,
                        title="Container runs as root",
                        description=(
                            f"{root_stages[0]} defines a process but never drops privileges. If "
                            "the application is compromised, the attacker starts as root inside "
                            "the container — and with some host configurations that maps to root "
                            "outside it."
                        ),
                        recommendation=(
                            "Create an unprivileged user in the image and add a USER instruction "
                            "before CMD. Keep application code owned by root so the running "
                            "process cannot rewrite it."
                        ),
                        score_impact=_RUNS_AS_ROOT,
                    ),
                )
            )

        if healthchecked:
            results.append(passed(_HEALTHCHECK))
        else:
            results.append(
                failed(
                    _HEALTHCHECK,
                    ScanFinding(
                        category=CATEGORY,
                        severity=Severity.LOW,
                        title="No container healthcheck",
                        description=(
                            "No HEALTHCHECK instruction was found. An orchestrator can only tell "
                            "that the process started, not that it is serving — so a container "
                            "that is up but wedged keeps receiving traffic."
                        ),
                        recommendation=(
                            "Add a HEALTHCHECK that exercises the path that matters, or configure "
                            "a readiness probe if you deploy to Kubernetes."
                        ),
                        score_impact=_NO_HEALTHCHECK,
                    ),
                )
            )

        if shell_entry is None:
            results.append(passed(_SIGNALS))
        else:
            where, instruction = shell_entry
            results.append(
                failed(
                    _SIGNALS,
                    ScanFinding(
                        category=CATEGORY,
                        severity=Severity.MEDIUM,
                        title="Container does not receive stop signals",
                        description=(
                            f"{where} starts its process with `{instruction}` — shell form, so the "
                            "application runs as a child of /bin/sh, which does not forward "
                            "SIGTERM. On every deploy and every scale-down the orchestrator asks "
                            "the container to stop, nothing hears it, and the container is killed "
                            "once the grace period expires — dropping whatever was in flight."
                        ),
                        recommendation=(
                            "Use the JSON array form, so the process is PID 1 and receives the "
                            'signal directly: CMD ["python", "-m", "app"]. If a shell really is '
                            "needed, prefix the command with exec, or use an init such as tini."
                        ),
                        score_impact=_SHELL_FORM_ENTRYPOINT,
                    ),
                )
            )
        return results

    def _check_privileged(
        self, compose_files: list[Path], manifest_files: list[Path], repo: RepositoryIndex
    ) -> CheckResult:
        """Whether any committed deployment file removes the container boundary.

        Asked of Compose and Kubernetes together. The grants are spelled the
        same way in both, and a repository that deploys to Kubernetes is
        precisely the one this check exists for.
        """
        candidates = compose_files + manifest_files
        if not candidates:
            return skipped(
                _PRIVILEGED, "no Compose file or orchestration manifest was found to inspect"
            )

        for path in candidates:
            description = host_access(repo.read(path))
            if description is None:
                continue
            relative = repo.relative(path)
            return failed(
                _PRIVILEGED,
                ScanFinding(
                    category=CATEGORY,
                    severity=Severity.HIGH,
                    title="Container granted host-level access",
                    description=(
                        f"{relative} defines a container that {description}. The isolation "
                        "between the container and the machine it runs on is removed, so a "
                        "compromise of the application is a compromise of the host rather than "
                        "of a sandbox. A development-only Compose file is the common and "
                        "legitimate case for this — the thing worth checking is that the line "
                        "has not been copied into whatever actually gets deployed."
                    ),
                    recommendation=(
                        "Remove the grant from anything that reaches a deployed environment. "
                        "Where the capability is genuinely needed, add only the specific one "
                        "(cap_add: NET_ADMIN) rather than privileged, and reach a container "
                        "runtime through its platform API instead of by mounting its socket."
                    ),
                    score_impact=_PRIVILEGED_CONTAINER,
                ),
            )

        return passed(_PRIVILEGED)

    def _check_ci(self, repo: RepositoryIndex) -> CheckResult:
        # A CI directory only counts if it has something in it — an empty
        # .github/workflows/ runs nothing.
        if any(
            repo.relative(path).lower().startswith(_CI_DIRECTORY_PREFIXES) for path in repo.files
        ):
            return passed(_CI)
        if repo.has_root_entry(*_CI_ROOT_FILES):
            return passed(_CI)

        return failed(
            _CI,
            ScanFinding(
                category=CATEGORY,
                severity=Severity.MEDIUM,
                title="No CI pipeline",
                description=(
                    "No continuous integration configuration was found. Nothing builds or tests "
                    "the project automatically, so whether the main branch works depends on "
                    "somebody having checked by hand."
                ),
                recommendation=(
                    "Add a pipeline that installs dependencies, runs the test suite, and builds "
                    "the image on every push."
                ),
                score_impact=_NO_CI_PIPELINE,
            ),
        )
