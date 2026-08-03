import os
from uuid import uuid4

from sqlalchemy import select

from app.db.models import User


def test_committed_fixture_data_is_not_visible_outside_test_transaction(db_session, _test_engine):
    user = User(
        id=uuid4(),
        username=f"isolation_{uuid4().hex}",
        password_hash="fixture-only",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    with _test_engine.connect() as independent_connection:
        visible_user = independent_connection.execute(
            select(User.id).where(User.id == user.id),
        ).scalar_one_or_none()

    assert visible_user is None


def test_application_database_url_is_the_validated_test_database():
    from app.db.session import engine

    assert engine.url.render_as_string(hide_password=False) == os.environ["DATABASE_URL_TEST"]
