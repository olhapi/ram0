"""Tests for additive, one-time owner category onboarding."""

from __future__ import annotations

import threading

from ram0_client import Ram0ClientError
from setup_coding_categories import CODING_CATEGORIES, main, onboard_categories
from ram0_config import write_config


class CatalogClient:
    def __init__(self, response):
        self.response = response
        self.creates: list[dict[str, str]] = []
        self.gets = 0

    def get_categories(self):
        self.gets += 1
        return self.response

    def create_category(self, definition):
        copied = dict(definition)
        self.creates.append(copied)
        return {"saved": [copied]}


def test_category_entrypoint_uses_persistent_config_without_environment(monkeypatch, tmp_path):
    """Breaks if category onboarding does not share the persistent lifecycle configuration."""
    write_config("https://persistent.example", "persistent-key", home=tmp_path)
    constructed: list[tuple[str, str]] = []

    class ConfiguredClient(CatalogClient):
        def __init__(self, api_url, api_key):
            super().__init__({"saved": list(CODING_CATEGORIES)})
            constructed.append((api_url, api_key))

    monkeypatch.setattr("setup_coding_categories.Ram0Client", ConfiguredClient)

    assert main(environment={}, home=tmp_path, data_dir=tmp_path / "data") == 0
    assert constructed == [("https://persistent.example", "persistent-key")]


def test_first_onboarding_preserves_existing_verbatim_and_adds_only_missing(tmp_path):
    """Breaks if onboarding replaces or rewrites an owner's existing catalog."""
    existing = [
        {"name": "architecture_decisions", "description": "My edited definition"},
        {"name": "legacy_personal", "description": "Copied from the legacy template"},
    ]
    client = CatalogClient({"saved": existing, "active": existing, "source": "user"})

    changed = onboard_categories(client, data_dir=tmp_path)

    assert changed is True
    assert client.gets == 1
    assert len(client.creates) == len(CODING_CATEGORIES) - 1
    assert all(item["name"] not in {"architecture_decisions", "legacy_personal"} for item in client.creates)
    assert any(item["name"] == "project_meta" for item in client.creates)


def test_fresh_owner_preserves_active_legacy_defaults_before_appending_coding_catalog(tmp_path):
    """Breaks if onboarding treats saved=[] as an empty catalog and overwrites active defaults."""
    defaults = [
        {"name": "personal_details", "description": "Legacy identity details."},
        {"name": "technology", "description": "Legacy technology interests."},
    ]
    client = CatalogClient({"saved": [], "active": defaults, "source": "defaults"})

    assert onboard_categories(client, data_dir=tmp_path) is True
    assert len(client.creates) == len(CODING_CATEGORIES)
    assert not ({item["name"] for item in defaults} & {item["name"] for item in client.creates})


def test_success_marker_prevents_later_user_edits_from_being_overwritten(tmp_path):
    """Breaks if reinstall/on-start calls PUT again after successful onboarding."""
    first = CatalogClient({"saved": []})
    assert onboard_categories(first, data_dir=tmp_path) is True

    edited = CatalogClient({"saved": [{"name": "custom", "description": "User edit"}]})
    assert onboard_categories(edited, data_dir=tmp_path) is False
    assert edited.gets == 0
    assert edited.creates == []


def test_already_complete_catalog_marks_success_without_put(tmp_path):
    """Breaks if a complete catalog is needlessly replaced on first inspection."""
    client = CatalogClient({"saved": list(CODING_CATEGORIES)})

    assert onboard_categories(client, data_dir=tmp_path) is False
    assert client.gets == 1
    assert client.creates == []
    assert (tmp_path / "coding-categories-onboarded").is_file()


def test_onboarding_posts_each_missing_definition_without_overwriting_a_concurrent_dashboard_edit(tmp_path):
    """Breaks if onboarding still performs a stale whole-catalog replacement after its initial read."""

    class DashboardRaceClient:
        def __init__(self) -> None:
            self.saved: dict[str, dict[str, str]] = {}
            self.creates: list[dict[str, str]] = []
            self.initial_read = False
            self.dashboard_edit_applied = False

        def get_categories(self):
            if not self.initial_read:
                self.initial_read = True
                return {"saved": [], "active": []}
            return {"saved": list(self.saved.values()), "active": list(self.saved.values())}

        def create_category(self, definition):
            if not self.dashboard_edit_applied:
                self.dashboard_edit_applied = True
                self.saved["custom"] = {"name": "custom", "description": "Concurrent dashboard edit"}
                self.saved["architecture_decisions"] = {
                    "name": "architecture_decisions",
                    "description": "Owner-customized during onboarding",
                }
            copied = dict(definition)
            if copied["name"] in self.saved:
                raise Ram0ClientError(400, "request_rejected", "Check the request.")
            self.saved[copied["name"]] = copied
            self.creates.append(copied)
            return {"saved": list(self.saved.values())}

    client = DashboardRaceClient()

    assert onboard_categories(client, data_dir=tmp_path) is True

    assert client.creates
    assert client.saved["custom"]["description"] == "Concurrent dashboard edit"
    assert client.saved["architecture_decisions"]["description"] == "Owner-customized during onboarding"
    assert all(item["name"] not in {"custom", "architecture_decisions"} for item in client.creates)


def test_two_plugin_onboarders_tolerate_duplicate_create_races(tmp_path):
    """Breaks if concurrent plugin starts fail or replace the owner's catalog as a batch."""

    class ConcurrentCatalog:
        def __init__(self) -> None:
            self.lock = threading.Lock()
            self.saved: dict[str, dict[str, str]] = {
                "custom": {"name": "custom", "description": "Dashboard-owned wording"}
            }
            self.create_counts: dict[str, int] = {}

        def get_categories(self):
            with self.lock:
                return {"saved": list(self.saved.values()), "active": list(self.saved.values())}

        def create_category(self, definition):
            with self.lock:
                name = definition["name"]
                if name in self.saved:
                    raise Ram0ClientError(400, "request_rejected", "Check the request.")
                self.saved[name] = dict(definition)
                self.create_counts[name] = self.create_counts.get(name, 0) + 1
                return {"saved": list(self.saved.values())}

    client = ConcurrentCatalog()
    start = threading.Event()
    errors: list[BaseException] = []

    def worker(name: str) -> None:
        try:
            start.wait()
            onboard_categories(client, data_dir=tmp_path / name)
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    workers = [threading.Thread(target=worker, args=(name,)) for name in ("one", "two")]
    for worker_thread in workers:
        worker_thread.start()
    start.set()
    for worker_thread in workers:
        worker_thread.join(timeout=3)

    assert errors == []
    assert client.saved["custom"]["description"] == "Dashboard-owned wording"
    assert set(client.create_counts) == {item["name"] for item in CODING_CATEGORIES}
    assert set(client.create_counts.values()) == {1}
