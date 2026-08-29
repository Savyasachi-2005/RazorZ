from __future__ import annotations

from sqlalchemy import text

from app.db import PUBLIC_TABLES, get_engine, lock_public_tables, ping_database


def main() -> None:
    print(ping_database())
    print("locked", lock_public_tables())
    engine = get_engine()
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT relname, relrowsecurity
                FROM pg_class
                WHERE relnamespace = 'public'::regnamespace
                  AND relname = ANY(:names)
                ORDER BY relname
                """
            ),
            {"names": list(PUBLIC_TABLES)},
        ).fetchall()
        print([(row[0], row[1]) for row in rows])
        print("audit_events_count", connection.execute(text("SELECT count(*) FROM audit_events")).scalar())


if __name__ == "__main__":
    main()
