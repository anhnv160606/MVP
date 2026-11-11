"""Give each test its own PostgreSQL schema and temporary IFC directory."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql

from database.postgres import project_transaction


_MIGRATION = Path(__file__).resolve().parents[1] / "supabase/migrations/20261111000002_placement_state.sql"


def setup_postgres(test):
    dsn = os.environ.get("MVP_TEST_DATABASE_URL")
    if not dsn:
        test.skipTest("Set MVP_TEST_DATABASE_URL to a disposable PostgreSQL test database")
    schema = f"mvp_test_{uuid4().hex}"
    migration = _MIGRATION.read_text(encoding="utf-8").replace("mvp_placement", schema)
    with psycopg.connect(dsn) as connection:
        connection.execute(migration)

    def drop_schema():
        with psycopg.connect(dsn) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

    test.addCleanup(drop_schema)
    root = Path(test.enterContext(TemporaryDirectory()))
    test.enterContext(patch.dict(os.environ, {
        "MVP_DATABASE_URL": dsn,
        "MVP_POSTGRES_SCHEMA": schema,
        "MVP_IFC_SNAPSHOT_DIR": str(root / "snapshots"),
    }))
    return root


def fail_after_insert(table, message, *, condition=None):
    """Force rollback after a row has already been inserted."""
    with project_transaction() as connection:
        connection.execute(
            sql.SQL("""
                CREATE FUNCTION test_fail_insert() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN RAISE EXCEPTION USING MESSAGE = {}; END;
                $$
            """).format(sql.Literal(message)),
        )
        predicate = sql.SQL(" WHEN ({})").format(condition) if condition is not None else sql.SQL("")
        connection.execute(
            sql.SQL("""
                CREATE TRIGGER fail_insert AFTER INSERT ON {}
                FOR EACH ROW{} EXECUTE FUNCTION test_fail_insert()
            """).format(sql.Identifier(table), predicate),
        )


def stop_failing_insert(table):
    with project_transaction() as connection:
        connection.execute(sql.SQL("DROP TRIGGER fail_insert ON {}").format(sql.Identifier(table)))
