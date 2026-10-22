from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.is_development,
    pool_size=20,
    max_overflow=10,
    pool_recycle=300,
    pool_pre_ping=True,
    pool_timeout=30,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def sync_sequences() -> int:
    """Point every Postgres id sequence at MAX(id).

    Rows imported with explicit ids (migrate_mysql.py) leave the sequences at 1, so the
    next INSERT collides with an existing row ("duplicate key ... _pkey") and the page
    returns a 500. Idempotent and cheap; a no-op on non-Postgres databases.
    """
    from sqlalchemy import text

    if engine.dialect.name != "postgresql":
        return 0
    fixed = 0
    async with engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT c.table_name, c.column_name, pg_get_serial_sequence(quote_ident(c.table_name), c.column_name) "
            "FROM information_schema.columns c WHERE c.table_schema = current_schema() AND c.column_default LIKE 'nextval%'"
        ))).fetchall()
        for table, col, seq in rows:
            if not seq:
                continue
            mx = (await conn.execute(text(f'SELECT MAX("{col}") FROM "{table}"'))).scalar()
            last, called = (await conn.execute(text(f"SELECT last_value, is_called FROM {seq}"))).fetchone()
            current = last if called else last - 1
            if mx is not None and mx > current:
                await conn.execute(text("SELECT setval(:seq, :val, true)"), {"seq": seq, "val": mx})
                fixed += 1
    return fixed


# Columns added after the first release. ``create_all`` only creates missing *tables*, so existing
# databases get these via ALTER TABLE at startup (idempotent).
_ADDED_COLUMNS = [
    ("branches", "is_hq", "BOOLEAN DEFAULT FALSE"),
    ("givings", "branch_id", "INTEGER"),
    ("givings", "gateway_owner", "VARCHAR(10) DEFAULT 'hq'"),
    ("givings", "method", "VARCHAR(20) DEFAULT 'online'"),
    ("givings", "note", "TEXT"),
    ("prayer_requests", "branch_id", "INTEGER"),
    ("unit_reports", "resident_reviewed_by", "INTEGER"),
    ("unit_reports", "resident_reviewed_at", "TIMESTAMP"),
    ("admins", "session_version", "INTEGER DEFAULT 0"),
    ("admins", "must_change_password", "BOOLEAN DEFAULT FALSE"),
    ("sermons", "series", "VARCHAR(150)"),
    ("sermons", "scripture", "VARCHAR(150)"),
    ("sermons", "transcript_public", "BOOLEAN DEFAULT FALSE"),
    ("sermons", "transcription_error", "TEXT"),
    # Global churches: ISO codes are the source of truth (app/core/geo.py); backfilled by geo_migration.
    ("branches", "country_code", "VARCHAR(2)"),
    ("branches", "region_code", "VARCHAR(10)"),
    ("branches", "currency", "VARCHAR(3)"),
    ("branches", "timezone", "VARCHAR(64)"),
    ("members", "country_code", "VARCHAR(2)"),
    ("members", "region_code", "VARCHAR(10)"),
    ("members", "city", "VARCHAR(120)"),
    ("ministers", "origin_country_code", "VARCHAR(2)"),
    ("ministers", "origin_region_code", "VARCHAR(10)"),
    ("ministers", "education_level", "VARCHAR(40)"),
    ("givings", "currency", "VARCHAR(3) DEFAULT 'NGN'"),
    ("givings", "checked_at", "TIMESTAMP"),
    ("remittances", "currency", "VARCHAR(3) DEFAULT 'NGN'"),
    ("contact_inquiries", "topic", "VARCHAR(40)"),
    ("newsletter_subscribers", "token", "VARCHAR(64)"),
    ("newsletter_subscribers", "source", "VARCHAR(30)"),
    ("prayer_requests", "notified_at", "TIMESTAMP"),
    ("contact_inquiries", "kind", "VARCHAR(10) DEFAULT 'contact'"),
]


async def ensure_schema() -> None:
    """Add new columns to old databases and normalise data the new HQ/branch model depends on."""
    from sqlalchemy import inspect, text

    async with engine.begin() as conn:
        def _missing(sync_conn):
            insp = inspect(sync_conn)
            have = {t: {c["name"] for c in insp.get_columns(t)} for t in {t for t, _, _ in _ADDED_COLUMNS} if insp.has_table(t)}
            return [(t, c, ddl) for t, c, ddl in _ADDED_COLUMNS if t in have and c not in have[t]]

        for table, col, ddl in await conn.run_sync(_missing):
            await conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{col}" {ddl}'))

        if conn.dialect.name == "postgresql":
            # Older databases created unit_reports.department_id with ON DELETE CASCADE, so deleting a
            # department erased every church's reports for it. Re-point it at RESTRICT (data untouched).
            cascading = (await conn.execute(text(
                "SELECT conname FROM pg_constraint WHERE contype = 'f' AND confdeltype = 'c' "
                "AND conrelid = 'unit_reports'::regclass AND confrelid = 'departments'::regclass"))).scalars().all() \
                if (await conn.execute(text("SELECT to_regclass('unit_reports') IS NOT NULL"))).scalar() else []
            for name in cascading:
                await conn.execute(text(f'ALTER TABLE unit_reports DROP CONSTRAINT "{name}"'))
                await conn.execute(text(
                    f'ALTER TABLE unit_reports ADD CONSTRAINT "{name}" FOREIGN KEY (department_id) '
                    "REFERENCES departments (id) ON DELETE RESTRICT"))

        # Exactly one HQ branch. Prefer an explicit slug/name; otherwise the oldest branch.
        flagged = (await conn.execute(text("SELECT COUNT(*) FROM branches WHERE is_hq = :t"), {"t": True})).scalar() or 0
        if flagged == 0:
            row = (await conn.execute(text(
                "SELECT id FROM branches WHERE slug IN ('hq', 'scent-of-water-hq') OR LOWER(name) LIKE '%headquarters%' "
                "OR LOWER(name) LIKE '% hq' OR LOWER(name) = 'hq' ORDER BY id LIMIT 1"))).first()
            if row is None:
                row = (await conn.execute(text("SELECT id FROM branches ORDER BY id LIMIT 1"))).first()
            if row is not None:
                await conn.execute(text("UPDATE branches SET is_hq = :t WHERE id = :i"), {"t": True, "i": row[0]})
        await conn.execute(text("UPDATE branches SET is_hq = :f WHERE is_hq IS NULL"), {"f": False})
        await conn.execute(text("UPDATE admins SET role = 'branch_admin' WHERE role IN ('admin', 'branch_super_admin')"))
        await conn.execute(text("UPDATE givings SET gateway_owner = 'hq' WHERE gateway_owner IS NULL"))
        await conn.execute(text("UPDATE givings SET method = 'online' WHERE method IS NULL"))
        tables = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
        if "admins" in tables:
            await conn.execute(text("UPDATE admins SET session_version = 0 WHERE session_version IS NULL"))
            await conn.execute(text("UPDATE admins SET must_change_password = :f WHERE must_change_password IS NULL"), {"f": False})
        if "sermons" in tables:
            await conn.execute(text("UPDATE sermons SET transcript_public = :f WHERE transcript_public IS NULL"), {"f": False})
        if "contact_inquiries" in tables:
            await conn.execute(text("UPDATE contact_inquiries SET kind = 'contact' WHERE kind IS NULL"))
        if "newsletter_subscribers" in tables:
            # Every subscriber needs an unsubscribe token (rows created before the column existed have none).
            import secrets

            for (sub_id,) in (await conn.execute(text("SELECT id FROM newsletter_subscribers WHERE token IS NULL"))).fetchall():
                await conn.execute(text("UPDATE newsletter_subscribers SET token = :t WHERE id = :i"), {"t": secrets.token_urlsafe(24), "i": sub_id})


async def init_db() -> None:
    from app.models import __all__  # noqa: F401 — ensure all models are registered on Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_schema()
    await flag_default_passwords()


# Passwords that ship with the software or are printed in the docs; an account still using one must change it.
_KNOWN_DEFAULT_PASSWORDS = ("admin123",)          # each check is a slow bcrypt verify at every start-up, so only the shipped default


async def flag_default_passwords() -> int:
    """Force a password change on any console account that still uses a well-known default. Idempotent."""
    from sqlalchemy import text

    from app.core.security import verify_password

    flagged = 0
    try:
        async with engine.begin() as conn:
            rows = (await conn.execute(text("SELECT id, password_hash FROM admins WHERE must_change_password IS NOT TRUE"))).fetchall()
            for admin_id, pw_hash in rows:
                if any(verify_password(p, pw_hash) for p in _KNOWN_DEFAULT_PASSWORDS):
                    await conn.execute(text("UPDATE admins SET must_change_password = :t WHERE id = :i"), {"t": True, "i": admin_id})
                    flagged += 1
    except Exception:                       # never block start-up on this
        import logging

        logging.getLogger(__name__).exception("default-password check failed")
    return flagged
