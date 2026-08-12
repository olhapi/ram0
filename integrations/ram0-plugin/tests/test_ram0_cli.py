"""Persistent, secret-safe configuration and CLI contracts."""

from __future__ import annotations

import io
import json
import os
import stat
import time
from pathlib import Path

import pytest

from install_cli import install
from bootstrap_cli import bootstrap
from ram0_cli import main
from ram0_config import Ram0Config, Ram0ConfigError, load_config, normalize_api_url, update_config, write_config


CONTRACT = json.loads((Path(__file__).with_name("config_contract.json")).read_text())


def test_write_config_is_private_atomic_and_loads_after_environment_is_cleared(tmp_path):
    """Breaks if setup is session-only, public, misplaced, or leaves a temporary file."""
    path = write_config("https://brain-api.olhapi.com/", "one-time-key", home=tmp_path)

    assert path == tmp_path / ".config/ram0/config.json"
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_config({}, home=tmp_path) == Ram0Config("https://brain-api.olhapi.com", "one-time-key")
    assert list(path.parent.glob(".config.json.*")) == []


def test_environment_overrides_individual_file_fields(tmp_path):
    """Breaks if a CI override erases the other persistent field or file values beat explicit overrides."""
    write_config("https://file.example", "file-key", home=tmp_path)

    assert load_config({"RAM0_API_URL": "https://env.example"}, home=tmp_path) == Ram0Config(
        "https://env.example", "file-key"
    )
    assert load_config({"RAM0_API_KEY": "env-key"}, home=tmp_path) == Ram0Config(
        "https://file.example", "env-key"
    )


@pytest.mark.parametrize(("raw", "expected"), CONTRACT["valid_urls"])
def test_url_contract_normalizes_supported_urls(raw, expected):
    """Breaks if Python and OpenCode would derive different MCP endpoints."""
    assert normalize_api_url(raw) == expected


@pytest.mark.parametrize("raw", CONTRACT["invalid_urls"])
def test_url_contract_rejects_unsafe_or_ambiguous_urls(raw):
    """Breaks if setup can persist credentials or ambiguous routing inside the endpoint."""
    with pytest.raises(Ram0ConfigError, match="absolute HTTP"):
        normalize_api_url(raw)


def test_loader_rejects_insecure_permissions_and_symlinks(tmp_path):
    """Breaks if another local user could read the key or redirect config reads."""
    path = write_config("https://brain-api.olhapi.com", "secret-value", home=tmp_path)
    path.chmod(0o644)
    with pytest.raises(Ram0ConfigError, match="chmod 600"):
        load_config({}, home=tmp_path)

    path.unlink()
    target = tmp_path / "target.json"
    target.write_text('{"api_url":"https://attacker.example","api_key":"stolen"}')
    path.symlink_to(target)
    with pytest.raises(Ram0ConfigError, match="regular file"):
        load_config({}, home=tmp_path)


def test_writer_rejects_existing_symlink_without_changing_target(tmp_path):
    """Breaks if atomic setup can be redirected into an attacker-chosen file."""
    directory = tmp_path / ".config/ram0"
    directory.mkdir(parents=True, mode=0o700)
    target = tmp_path / "target.json"
    target.write_text("unchanged")
    (directory / "config.json").symlink_to(target)

    with pytest.raises(Ram0ConfigError, match="regular file"):
        write_config("https://brain-api.olhapi.com", "secret-value", home=tmp_path)
    assert target.read_text() == "unchanged"


def test_loader_and_writer_reject_an_insecure_or_symlinked_config_directory(tmp_path):
    """Breaks if directory traversal or broad directory access bypasses the private-file boundary."""
    path = write_config("https://brain-api.olhapi.com", "secret-value", home=tmp_path)
    path.parent.chmod(0o755)
    with pytest.raises(Ram0ConfigError, match="chmod 700"):
        load_config({}, home=tmp_path)

    path.unlink()
    path.parent.rmdir()
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    path.parent.symlink_to(redirected)
    with pytest.raises(Ram0ConfigError, match="directory"):
        write_config("https://brain-api.olhapi.com", "secret-value", home=tmp_path)
    assert list(redirected.iterdir()) == []


def test_setup_and_display_never_echo_the_key(monkeypatch, tmp_path):
    """Breaks if setup or status discloses the one-time key."""
    monkeypatch.setattr("ram0_cli.getpass", lambda _prompt: "secret-value")
    stdout, stderr = io.StringIO(), io.StringIO()

    assert main(["setup", "--url", "https://brain-api.olhapi.com"], home=tmp_path, stdout=stdout, stderr=stderr) == 0
    assert main(["config", "show"], home=tmp_path, stdout=stdout, stderr=stderr) == 0

    displayed = stdout.getvalue() + stderr.getvalue()
    assert "secret-value" not in displayed
    assert "API key: configured (redacted)" in displayed
    assert json.loads((tmp_path / ".config/ram0/config.json").read_text())["api_key"] == "secret-value"


def test_mcp_command_runs_stdio_adapter_with_persistent_configuration(monkeypatch, tmp_path):
    """Breaks if Codex needs plugin paths or exported credentials to launch Ram0 MCP."""
    stdin, stdout, stderr = io.StringIO(), io.StringIO(), io.StringIO()
    calls = []

    monkeypatch.setattr(
        "ram0_cli.run_stdio",
        lambda *args, **kwargs: calls.append((args, kwargs)) or 17,
    )

    assert main(["mcp"], home=tmp_path, stdin=stdin, stdout=stdout, stderr=stderr) == 17
    assert calls == [
        (
            (stdin, stdout, stderr),
            {"environment": None, "home": tmp_path},
        )
    ]


def test_field_updates_preserve_the_other_value(tmp_path):
    """Breaks if rotation or endpoint updates accidentally destroy the other credential field."""
    write_config("https://one.example", "one-key", home=tmp_path)
    update_config(api_url="https://two.example", home=tmp_path)
    assert load_config({}, home=tmp_path) == Ram0Config("https://two.example", "one-key")
    update_config(api_key="two-key", home=tmp_path)
    assert load_config({}, home=tmp_path) == Ram0Config("https://two.example", "two-key")


def test_config_test_sends_bearer_to_non_mutating_categories_endpoint(monkeypatch, tmp_path, ram0_server):
    """Breaks if verification mutates memory, omits authentication, or leaks the key."""
    write_config(ram0_server.url, "verification-key", home=tmp_path)
    stdout, stderr = io.StringIO(), io.StringIO()

    assert main(["config", "test"], home=tmp_path, stdout=stdout, stderr=stderr) == 0

    assert [(request["method"], request["path"]) for request in ram0_server.requests] == [("GET", "/categories")]
    assert ram0_server.requests[0]["headers"]["authorization"] == "Bearer verification-key"
    assert ram0_server.requests[0]["headers"]["user-agent"].startswith("ram0-plugin/")
    assert "verification-key" not in stdout.getvalue() + stderr.getvalue()


def test_installer_copies_only_bounded_runtime_and_warns_when_bin_is_not_on_path(tmp_path):
    """Breaks if installation edits shell state, omits the command, or copies the repository."""
    stdout = io.StringIO()
    installed = install(home=tmp_path, environment={"PATH": "/usr/bin"}, stdout=stdout)

    assert installed == tmp_path / ".local/bin/ram0"
    assert stat.S_IMODE(installed.stat().st_mode) == 0o755
    assert sorted(path.name for path in (tmp_path / ".local/share/ram0").iterdir()) == [
        "mcp_stdio_adapter.py",
        "ram0_cli.py",
        "ram0_config.py",
    ]
    assert str(installed) + " setup" in stdout.getvalue()
    assert not (tmp_path / ".zshrc").exists()


def test_bootstrap_is_atomic_idempotent_and_never_touches_configuration(tmp_path):
    config = tmp_path / ".config/ram0/config.json"
    config.parent.mkdir(parents=True, mode=0o700)
    config.write_text('{"api_url":"https://example.test","api_key":"secret"}\n')
    config.chmod(0o600)
    before = (config.read_bytes(), config.stat().st_mtime_ns)
    stdout, stderr = io.StringIO(), io.StringIO()

    assert bootstrap(home=tmp_path, stdout=stdout, stderr=stderr) is True
    installed = tmp_path / ".local/share/ram0/ram0_cli.py"
    first_mtime = installed.stat().st_mtime_ns
    time.sleep(0.002)
    assert bootstrap(home=tmp_path, stdout=stdout, stderr=stderr) is False

    assert installed.stat().st_mtime_ns == first_mtime
    assert (config.read_bytes(), config.stat().st_mtime_ns) == before
    assert stat.S_IMODE(installed.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / ".local/bin/ram0").stat().st_mode) == 0o755
    assert "secret" not in stdout.getvalue() + stderr.getvalue()


def test_bootstrap_rejects_symlinked_runtime_destination(tmp_path):
    share = tmp_path / ".local/share/ram0"
    share.mkdir(parents=True)
    target = tmp_path / "target"
    target.write_text("unchanged")
    (share / "ram0_cli.py").symlink_to(target)

    with pytest.raises(OSError, match="regular file"):
        bootstrap(home=tmp_path)
    assert target.read_text() == "unchanged"


def test_missing_key_is_actionable_without_repr_disclosure(tmp_path):
    """Breaks if network callers get an obscure error or config repr gains a secret field."""
    with pytest.raises(Ram0ConfigError, match="ram0 setup") as raised:
        load_config({}, home=tmp_path, require_key=True)
    assert "api_key" not in repr(raised.value)
