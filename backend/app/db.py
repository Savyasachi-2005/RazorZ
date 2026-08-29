from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401
from app.config import settings

_engines: dict[str, object] = {}
# URLs whose schema bootstrap already ran in this process. Repositories construct
# freely, so without this the DDL/RLS pass would repeat on every single request.
_bootstrapped: set[str] = set()


def get_engine(database_url: str | None = None):
    url = database_url or settings.database_url
    cached = _engines.get(url)
    if cached is not None:
        return cached

    kwargs: dict = {"echo": False, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url == "sqlite://":
            kwargs["poolclass"] = StaticPool

    engine = create_engine(url, **kwargs)
    _engines[url] = engine
    return engine


def reset_engine_cache() -> None:
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
    _bootstrapped.clear()


PUBLIC_TABLES = (
    "sources",
    "orders",
    "payments",
    "settlements",
    "refunds",
    "fees",
    "reconciliation_records",
    "match_candidates",
    "exceptions",
    "audit_events",
    "webhook_events",
    "users",
    "user_sessions",
)


def lock_public_tables(database_url: str | None = None) -> list[str]:
    """Enable RLS and revoke Data API roles. Postgres owner access is unchanged."""
    engine = get_engine(database_url)
    if engine.dialect.name != "postgresql":
        return []

    locked: list[str] = []
    with engine.begin() as connection:
        roles = {
            row[0]
            for row in connection.execute(
                text("SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
            )
        }
        for table in PUBLIC_TABLES:
            connection.execute(text(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY'))
            for role in roles:
                connection.execute(text(f'REVOKE ALL ON TABLE public."{table}" FROM {role}'))
            locked.append(table)
    return locked


def create_db_and_tables(database_url: str | None = None, *, force: bool = False):
    """Bootstrap schema once per process per URL. Repeat calls are a no-op."""
    url = database_url or settings.database_url
    if not force and url in _bootstrapped:
        return list(SQLModel.metadata.tables.keys())

    engine = get_engine(database_url)
    SQLModel.metadata.create_all(engine)
    _ensure_multi_record_columns(engine)
    lock_public_tables(database_url)
    _bootstrapped.add(url)
    return list(SQLModel.metadata.tables.keys())


def _table_columns(connection, table: str) -> set[str]:
    dialect = connection.engine.dialect.name
    if dialect == "sqlite":
        rows = connection.execute(text(f'PRAGMA table_info("{table}")'))
        return {row[1] for row in rows}
    rows = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table"
        ),
        {"table": table},
    )
    return {row[0] for row in rows}


def _ensure_multi_record_columns(engine) -> None:
    """Best-effort ADD COLUMN for multi-record fields without Alembic."""
    additions = {
        "settlements": [("payment_reference", "VARCHAR")],
        "fees": [("payment_reference", "VARCHAR")],
        "reconciliation_records": [
            ("pair_type", "VARCHAR"),
            ("source_record_type", "VARCHAR"),
            ("related_record_type", "VARCHAR"),
        ],
    }
    with engine.begin() as connection:
        for table, columns in additions.items():
            try:
                existing = _table_columns(connection, table)
            except Exception:
                continue
            if not existing:
                continue
            for name, col_type in columns:
                if name in existing:
                    continue
                connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {col_type}'))


def drop_db_and_tables(database_url: str | None = None) -> None:
    engine = get_engine(database_url)
    SQLModel.metadata.drop_all(engine)
    _bootstrapped.discard(database_url or settings.database_url)


def get_session(database_url: str | None = None):
    engine = get_engine(database_url)
    return Session(engine)


def ping_database(database_url: str | None = None) -> dict[str, str | bool]:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {
        "ok": True,
        "dialect": engine.dialect.name,
        "database": engine.url.database or "",
        "host": engine.url.host or "local",
    }
