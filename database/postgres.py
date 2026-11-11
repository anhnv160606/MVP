"""Connections and project-scoped transactions for Supabase Postgres."""

import os
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg.rows import dict_row


class ProjectDatabaseError(RuntimeError):
    """Project data could not be read or written."""


@contextmanager
def project_transaction(*, lock_project=None):
    dsn = os.environ.get("MVP_DATABASE_URL")
    if not dsn:
        raise ProjectDatabaseError("Set MVP_DATABASE_URL to the backend PostgreSQL connection string")
    schema = os.environ.get("MVP_POSTGRES_SCHEMA", "mvp_placement")
    try:
        # Disable prepared statements for compatibility with transaction-mode poolers.
        with psycopg.connect(dsn, row_factory=dict_row, prepare_threshold=None, connect_timeout=10) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            connection.execute(
                sql.SQL("SET LOCAL search_path TO {}, pg_catalog, pg_temp").format(sql.Identifier(schema)),
            )
            if lock_project is not None:
                # Every write for one project takes this lock before reading versions.
                # Different projects can proceed independently.
                connection.execute("SET LOCAL lock_timeout TO '30s'")
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{schema}:{lock_project}",),
                )
            yield connection
    except psycopg.Error as error:
        detail = error.diag.message_primary
        raise ProjectDatabaseError(detail or "Could not connect to the project database") from error
