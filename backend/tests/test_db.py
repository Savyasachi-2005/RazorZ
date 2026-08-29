from app.db import create_db_and_tables


def test_create_db_and_tables_succeeds_for_sqlite_memory():
    tables = create_db_and_tables("sqlite://")
    assert "sources" in tables
    assert "orders" in tables
