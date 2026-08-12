"""Contracts for the guarded Unraid deployment command."""

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "server" / "scripts" / "deploy_unraid.sh"


def _script() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8")


def test_unraid_deploy_script_exposes_guarded_phases():
    script = _script()

    assert os.access(SCRIPT_PATH, os.X_OK)
    for name in (
        "acquire_lock",
        "preflight",
        "backup_database",
        "resolve_images",
        "verify_image",
        "render_candidate",
        "migrate_database",
        "recreate_services",
        "verify_deployment",
        "rollback_deployment",
    ):
        assert f"{name}()" in script


def test_unraid_deploy_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT_PATH)], check=True)


def test_unraid_deploy_self_test_covers_safety_contract_without_secrets():
    result = subprocess.run(
        [str(SCRIPT_PATH), "--self-test"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "RAM0_TEST_SECRET": "RAM0_SECRET_SENTINEL"},
    )

    assert "invalid SHA rejected" in result.stdout
    assert "concurrent lock rejected" in result.stdout
    assert "digest mismatch rejected" in result.stdout
    assert "revision mismatch rejected" in result.stdout
    assert "state promoted atomically" in result.stdout
    assert "rollback ordered before previous restart" in result.stdout
    assert "rendered ports validated" in result.stdout
    assert "rendered Ram0 resource names validated" in result.stdout
    assert "RAM0_SECRET_SENTINEL" not in result.stdout + result.stderr


def test_unraid_deploy_script_uses_exact_state_and_backup_roots():
    script = _script()

    assert 'APP_ROOT="/mnt/user/appdata/mem0"' in script
    assert 'STATE_DIR="$APP_ROOT/deploy"' in script
    assert 'BACKUP_ROOT="$APP_ROOT/backups"' in script
    assert 'LOCK_DIR="/tmp/ram0-unraid-deploy.lock"' in script
    assert "umask 077" in script
    assert "@sha256:" in script


def test_unraid_deploy_script_cleans_candidate_state_on_every_exit():
    script = _script()

    assert 'rm -f "$CANDIDATE_STATE"' in script
    assert "trap cleanup EXIT" in script


def test_unraid_deploy_compose_reads_secrets_before_deployment_state():
    script = _script()

    secret_env = '--env-file "$SERVER_DIR/.env"'
    state_env = '--env-file "$state"'
    assert secret_env in script
    assert state_env in script
    assert script.index(secret_env) < script.index(state_env)


def test_unraid_deploy_verifies_the_current_multi_user_schema_revision():
    """A successful 008 migration must not be mistaken for a failed deployment."""
    script = _script()

    assert 'expected_revision=${2:-008}' in script
    assert 'verify_deployment "$CANDIDATE_STATE" 008' in script
    assert '[[ $expected_revision == 007 || $expected_revision == 008 ]]' in script


def test_unraid_deploy_uses_the_configured_host_ip_for_ram0_ports():
    """A stale LAN address must not prevent the scoped Ram0 services from starting."""
    compose = (ROOT / "server" / "docker-compose.unraid.yaml").read_text(encoding="utf-8")
    script = _script()

    assert "${RAM0_HOST_IP:?Set RAM0_HOST_IP in server/.env}:18888:8000" in compose
    assert "${RAM0_HOST_IP:?Set RAM0_HOST_IP in server/.env}:13000:3000" in compose
    assert "192.168.1.2" not in compose
    assert "deployment_host_ip()" in script
    assert 'service_has_port "$BACKUP_DIR/candidate-compose.yaml" mem0 "$DEPLOY_HOST_IP" 18888' in script
    assert 'service_has_port "$BACKUP_DIR/candidate-compose.yaml" mem0-dashboard "$DEPLOY_HOST_IP" 13000' in script
    assert 'wait_for_url "http://${DEPLOY_HOST_IP}:18888/docs"' in script
    assert 'wait_for_url "http://${DEPLOY_HOST_IP}:13000/api/health"' in script


def test_unraid_deploy_uses_instance_configured_public_urls():
    """The tracked production overlay must not choose one operator's origin."""
    compose = (ROOT / "server" / "docker-compose.unraid.yaml").read_text(encoding="utf-8")
    script = _script()
    env_example = (ROOT / "server" / ".env.example").read_text(encoding="utf-8")

    assert "RAM0_PUBLIC_API_URL" in compose
    assert "RAM0_DASHBOARD_URL" in compose
    assert "RAM0_PUBLIC_API_URL" in env_example
    assert "RAM0_DASHBOARD_URL" in env_example
    assert 'wait_for_url "${PUBLIC_API_URL}/docs"' in script
    assert 'wait_for_url "${PUBLIC_DASHBOARD_URL}"' in script


def test_unraid_deploy_uses_exact_ram0_resource_names_and_migrates_legacy_project():
    compose = (ROOT / "server" / "docker-compose.unraid.yaml").read_text(encoding="utf-8")
    script = _script()

    assert "name: ram0" in compose
    assert "container_name: ram0_api" in compose
    assert "container_name: ram0_dashboard" in compose
    assert "container_name: ram0_postgres" in compose
    assert "name: ram0_network" in compose
    assert 'TARGET_PROJECT="ram0"' in script
    assert 'LEGACY_PROJECT="mem0"' in script
    assert "migrate_legacy_namespace()" in script
    assert "verify_exact_resource_names()" in script


def test_unraid_deploy_treats_an_already_migrated_namespace_as_a_successful_noop():
    """Normal upgrades must not trip the ERR trap before application recreation."""
    script = _script()

    assert '[[ $MIGRATING_LEGACY == true ]] || return 0' in script
