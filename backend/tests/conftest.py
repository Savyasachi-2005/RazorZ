from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite://"

from app.db import create_db_and_tables, drop_db_and_tables, reset_engine_cache


def pytest_configure() -> None:
    reset_engine_cache()
    create_db_and_tables()


def pytest_unconfigure() -> None:
    drop_db_and_tables()
    reset_engine_cache()
