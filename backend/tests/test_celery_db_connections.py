from unittest.mock import MagicMock


def test_worker_process_init_disposes_inherited_database_connections(monkeypatch) -> None:
    from app.worker.celery_app import dispose_inherited_database_connections
    import app.db.session as db_session

    fake_engine = MagicMock()
    monkeypatch.setattr(db_session, "engine", fake_engine)

    dispose_inherited_database_connections()

    fake_engine.dispose.assert_called_once_with(close=False)
