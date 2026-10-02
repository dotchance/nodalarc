"""Lifecycle contract tests for Make-as-facade behavior."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


DRY_RUN_TARGETS = (
    "help",
    "all",
    "deps",
    "check-deps",
    "check-registry",
    "build",
    "build-frontends",
    "build-images",
    "_clear-build-cache",
    "ensure-base-images",
    "build-base-images",
    "build-base",
    "build-frr",
    "build-probe",
    "build-ome",
    "build-scheduler",
    "build-node-agent",
    "build-vs-api",
    "build-operator",
    "build-vf",
    "build-measurement",
    "load",
    "install",
    "reinstall",
    "session",
    "restart",
    "upgrade",
    "deploy-all",
    "deploy-ome",
    "deploy-scheduler",
    "deploy-node-agent",
    "deploy-vs-api",
    "deploy-operator",
    "deploy-vf",
    "deploy-measurement",
    "status",
    "lint",
    "lint-policy",
    "typecheck",
    "format-diff",
    "generate-contracts",
    "check-contracts",
    "dead-code",
    "test",
    "test-backend",
    "test-frontend",
    "test-integration",
    "test-runtime-matrix",
    "test-builder-e2e",
    "teardown",
    "force-teardown",
    "reset-platform",
    "clean",
    "clean-deps",
    "clean-images",
    "clean-registry",
    "purge-containerd",
    "nuke",
)


def _makefile() -> str:
    return (ROOT / "Makefile").read_text()


def _make_env(**overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "KUBECONFIG": str(ROOT / ".does-not-exist"),
            "MODE": "single-node",
            "REGISTRY_HOST": "",
        }
    )
    env.update(overrides)
    return env


def _dry_run_make(target: str, **env_overrides: str) -> str:
    result = subprocess.run(
        ["make", "-n", "--no-print-directory", target],
        cwd=ROOT,
        env=_make_env(**env_overrides),
        text=True,
        capture_output=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, f"{target} dry-run failed:\n{output}"
    return output


def test_make_targets_dry_run_cleanly() -> None:
    stale_tool_script = re.compile(r"tools/(?:na-|clean-|detect-|check_lint_policy)|tools/.*\.sh")
    for target in DRY_RUN_TARGETS:
        output = _dry_run_make(target)
        assert not stale_tool_script.search(output), (
            f"{target} uses stale tools/ script path:\n{output}"
        )


def test_image_tags_are_content_addressed() -> None:
    """Image identity must equal content identity: a dirty tree must never
    share a tag with its HEAD commit, or the registry serves stale code under
    a current name and concurrent builds race (this happened; it cost hours)."""
    import subprocess
    import tempfile
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "na-tag.sh"

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        env = {"PATH": "/usr/bin:/bin", "HOME": tmp, "GIT_CONFIG_GLOBAL": "/dev/null"}

        def git(*args: str) -> None:
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                cwd=repo,
                env=env,
                check=True,
                capture_output=True,
            )

        def tag() -> str:
            return subprocess.run(
                ["bash", str(script)],
                cwd=repo,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        git("init", "-q")
        (repo / "a.txt").write_text("one\n")
        git("add", "a.txt")
        git("commit", "-qm", "first")

        clean = tag()
        assert "-" not in clean, f"clean tree must tag as bare sha, got {clean}"

        (repo / "a.txt").write_text("two\n")
        dirty_one = tag()
        assert dirty_one.startswith(clean + "-"), dirty_one
        assert dirty_one != clean, "dirty tree must not share the clean tag"

        (repo / "a.txt").write_text("three\n")
        dirty_two = tag()
        assert dirty_two != dirty_one, "different dirty trees must not share a tag"

        # Untracked files change builds too.
        (repo / "a.txt").write_text("one\n")
        assert tag() == clean
        (repo / "new.txt").write_text("x\n")
        with_new = tag()
        assert with_new != clean, "untracked files must dirty the tag"
        (repo / "new.txt").write_text("y\n")
        assert tag() != with_new, "an edit to an untracked file must change the tag"


@pytest.mark.parametrize("carrier", ["environment", "command-line"])
def test_helm_release_is_refused_at_parse_time(carrier: str) -> None:
    """The release name is fixed; a HELM_RELEASE from config.mk (a make variable, as the
    command-line assignment sets one) or the environment fails make before any recipe."""
    args = ["make", "-n", "--no-print-directory", "help"]
    env = _make_env()
    if carrier == "environment":
        env["HELM_RELEASE"] = "other"
    else:
        args.append("HELM_RELEASE=other")
    result = subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True, check=False)

    assert result.returncode != 0
    assert "HELM_RELEASE is not a setting" in result.stderr
    assert result.stdout == ""


def test_registry_prefix_is_refused_at_parse_time() -> None:
    """A prefix from config.mk or the environment reaches no script; make refuses it."""
    result = subprocess.run(
        ["make", "-n", "--no-print-directory", "help"],
        cwd=ROOT,
        env=_make_env(REGISTRY_HOST="registry.local:5000", REGISTRY_PREFIX="registry.local:5000/"),
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "REGISTRY_PREFIX is not a setting" in result.stderr
    assert "REGISTRY_PREFIX ?=" not in _makefile()
