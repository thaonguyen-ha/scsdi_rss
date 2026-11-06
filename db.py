"""Postgres (Neon) backing store for the article archive.

This replaces the old append-only ``archive/articles.jsonl`` as the single
source of truth. The static site in ``public/`` is now a disposable build
artifact regenerated from the DB on every run.

Design notes
------------
* Records are plain dicts (the same shape the scripts already pass around:
  ``key``, ``title``, ``pubDate``, ``source_name``, ...). The full record is
  stored losslessly in a ``JSONB data`` column, so no field is ever dropped
  when a new one is added by ``archive_text.py`` / ``resolve_links.py``.
* A surrogate ``id`` is the primary key, NOT ``key`` -- with dedup off the
  archive legitimately holds several rows sharing one normalized-title ``key``.
* A handful of columns are *promoted* out of ``data`` (title, country, dates,
  relevance flags) purely so Postgres can index/query them -- handy for the
  planned Hugging Face relevance/dedup work and for ad-hoc SQL. On read we
  return ``data`` verbatim, so promoted columns never change record shape.
* The scripts all follow a "load everything -> mutate -> write everything back"
  pattern, so writes are modeled as :func:`replace_all` (full rewrite, atomic)
  and :func:`insert_many` (append). No per-row identity is exposed to callers,
  which keeps surrogate ids out of the dicts (and therefore out of feed.json).

Connection: reads ``DATABASE_URL`` (Neon's pooled connection string). A local
``.env`` is loaded if python-dotenv is installed.
"""

from __future__ import annotations

import os
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Json

try:  # optional: load DATABASE_URL from a local .env during development
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL is not set. Point it at your Neon Postgres connection "
            "string (use the pooled '-pooler' host). Locally: put it in a .env "
            "file or export it; in CI: add it as the DATABASE_URL Actions secret."
        )
    return dsn


def connect() -> psycopg.Connection:
    """Open a new connection. Caller is responsible for closing (or use a
    ``with`` block). Short-lived by design -- fine for Neon's pooler."""
    return psycopg.connect(_dsn())


SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key           TEXT NOT NULL,
    data          JSONB NOT NULL,
    title         TEXT,
    outlet        TEXT,
    country       TEXT,
    domain        TEXT,
    relevant      BOOLEAN,
    tier          TEXT,
    pub_date      TIMESTAMPTZ,
    first_seen    TIMESTAMPTZ,
    text_archived BOOLEAN,
    text_relevant BOOLEAN,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_articles_key      ON articles (key);
CREATE INDEX IF NOT EXISTS idx_articles_pub_date ON articles (pub_date DESC);
CREATE INDEX IF NOT EXISTS idx_articles_relevant ON articles (relevant);
"""

# Columns written on every insert, in order. ``id`` is identity (auto) and
# ``updated_at`` defaults to now(), so neither is listed here.
_COLS = (
    "key", "data", "title", "outlet", "country", "domain",
    "relevant", "tier", "pub_date", "first_seen", "text_archived", "text_relevant",
)
_INSERT = (
    f"INSERT INTO articles ({', '.join(_COLS)}) "
    f"VALUES ({', '.join(['%s'] * len(_COLS))})"
)


def init_schema(conn: psycopg.Connection | None = None) -> None:
    """Create the table and indexes if they don't exist. Idempotent."""
    own = conn is None
    conn = conn or connect()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA)
        conn.commit()
    finally:
        if own:
            conn.close()


def _ts(val, rfc822: bool = False):
    """Parse a date string to an aware datetime, or None. ``rfc822`` for
    pubDate strings ('Wed, 21 Oct 2026 13:23:27 GMT'); otherwise ISO-8601."""
    if not val:
        return None
    try:
        dt = parsedate_to_datetime(val) if rfc822 else datetime.fromisoformat(val)
    except (TypeError, ValueError):
        return None
    if dt is not None and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _row(rec: dict) -> tuple:
    """Map a record dict to an INSERT row (promoted columns + JSONB data).
    Internal keys (leading underscore) are stripped from the stored data."""
    data = {k: v for k, v in rec.items() if not k.startswith("_")}
    return (
        rec.get("key"),
        Json(data),
        rec.get("title"),
        rec.get("outlet"),
        rec.get("country"),
        rec.get("domain"),
        rec.get("relevant"),
        rec.get("tier"),
        _ts(rec.get("pubDate"), rfc822=True),
        _ts(rec.get("first_seen")),
        rec.get("text_archived"),
        rec.get("text_relevant"),
    )


def load_ordered() -> list[dict]:
    """Return every record as a list of dicts, in insertion order."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT data FROM articles ORDER BY id")
        return [r[0] for r in cur.fetchall()]


def load_archive() -> tuple[dict, list]:
    """Drop-in replacement for build_feed.load_archive(): returns
    ``(records_by_key, ordered_list)``. ``by_key`` collapses duplicates
    (last row wins), matching the old JSONL behavior."""
    ordered = load_ordered()
    by_key = {rec["key"]: rec for rec in ordered}
    return by_key, ordered


def insert_many(records: list[dict]) -> int:
    """Append records (used for newly fetched items). Returns count inserted."""
    records = list(records)
    if not records:
        return 0
    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(_INSERT, [_row(r) for r in records])
        conn.commit()
    return len(records)


def replace_all(records: list[dict]) -> int:
    """Atomically replace the entire table with ``records``, preserving order.
    Mirrors the old "rewrite the whole archive" writes in archive_text.py,
    resolve_links.py, and build_feed.py --no-crawl."""
    records = list(records)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE articles RESTART IDENTITY")
            if records:
                cur.executemany(_INSERT, [_row(r) for r in records])
        conn.commit()
    return len(records)


def count() -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM articles")
        return cur.fetchone()[0]
