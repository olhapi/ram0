"""Static contracts for publishing Ram0 container images to public GHCR."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ghcr-images.yml"


def _workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_ghcr_workflow_is_sha_pinned_and_minimally_privileged():
    workflow = _workflow()

    assert "contents: read" in workflow
    assert "packages: write" in workflow
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow
    assert "docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9" in workflow
    assert "docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f" in workflow
    assert "docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8" in workflow
    assert "@v" not in workflow


def test_ghcr_workflow_publishes_both_amd64_sha_images():
    workflow = _workflow()

    assert "ghcr.io/olhapi/ram0-api:sha-${{ github.sha }}" in workflow
    assert "ghcr.io/olhapi/ram0-dashboard:sha-${{ github.sha }}" in workflow
    assert workflow.count("platforms: linux/amd64") == 2
    assert workflow.count("org.opencontainers.image.revision=${{ github.sha }}") == 2


def test_ghcr_workflow_runs_for_main_and_manual_dispatch():
    workflow = _workflow()

    assert "workflow_dispatch:" in workflow
    assert "branches: [main]" in workflow
    assert ":latest" not in workflow
