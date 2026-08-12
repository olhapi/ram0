"""Reusable database-free doubles for tests that reload ``server.main``."""

from unittest.mock import MagicMock


def fake_session_factory():
    """Return a context-manager session factory that never opens PostgreSQL."""
    session = MagicMock(name="server_test_session")
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.scalar.return_value = None
    session.execute.return_value.scalars.return_value.all.return_value = []
    return MagicMock(name="server_test_session_factory", return_value=session)


def install_server_runtime_doubles(server_main, *, session_factory=None):
    """Fence one reloaded app from category runtime and server-store side effects."""
    factory = session_factory or fake_session_factory()
    category_service = MagicMock(name="category_service")
    category_service.resolve_catalog.return_value = object()
    category_service.after_add.side_effect = lambda response, _catalog, **_kwargs: response
    category_service.run_memory_update.side_effect = (
        lambda _memory_id, operation, **_kwargs: operation()
    )
    worker = MagicMock(name="category_worker")

    server_main.SessionLocal = factory
    server_main.set_session_factory(factory)
    server_main.get_category_service = MagicMock(return_value=category_service)
    server_main.initialize_category_runtime = MagicMock(return_value=category_service)
    server_main.get_category_worker = MagicMock(return_value=worker)
    server_main._should_log_request = lambda _request: False
    server_main.app.dependency_overrides[
        server_main.categories_router.get_category_service
    ] = lambda: category_service
    return category_service, worker
