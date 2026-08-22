from __future__ import annotations

import sqlite3

from gabay.launcher import _backup_sqlite_before_migration


def test_pre_migration_backup_is_consistent_and_retained(tmp_path):
    database = tmp_path / "gabay.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE evidence (value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES ('kept')")
        connection.commit()

    backup = _backup_sqlite_before_migration(tmp_path)

    assert backup is not None and backup.exists()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM evidence").fetchone()[0] == "kept"


def test_pre_migration_backup_skips_a_missing_database(tmp_path):
    assert _backup_sqlite_before_migration(tmp_path) is None
