import os

os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("ZOHO_CLIENT_ID", "cid")
os.environ.setdefault("ZOHO_CLIENT_SECRET", "csecret")
os.environ.setdefault("ZOHO_REFRESH_TOKEN", "rtoken")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402


@pytest.fixture
def settings(tmp_path):
    s = get_settings().model_copy(update={"storage_local_dir": str(tmp_path / "files"),
                                          "max_file_bytes": 1024 * 1024})
    return s


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk_on(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
