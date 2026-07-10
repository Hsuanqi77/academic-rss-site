import sqlite3
from collections.abc import Iterator
from pathlib import Path


SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _schema_statements(script: str) -> Iterator[str]:
    start = 0
    for position, character in enumerate(script):
        if character != ";":
            continue

        candidate = script[start : position + 1]
        if sqlite3.complete_statement(candidate):
            statement = candidate.strip()
            if statement:
                yield statement
            start = position + 1

    if script[start:].strip():
        raise RuntimeError("schema SQL contains an incomplete statement")


def connect_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise RuntimeError("cannot initialize database with a pending transaction")

    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise RuntimeError(f"unsupported database schema version: {version}")

        script = SCHEMA_PATH.read_text(encoding="utf-8")
        for statement in _schema_statements(script):
            connection.execute(statement)
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
