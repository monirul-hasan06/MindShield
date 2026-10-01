"""SQLite persistence for MindShield sessions, settings, and breaks."""

from __future__ import annotations

import filecmp
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path


class DatabaseHandler:
    """Create and access MindShield's writable SQLite database."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        key_path: str | Path | None = None,
    ) -> None:
        self._custom_db_path = db_path is not None
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else self._default_db_path()
        )
        self.key_path = (
            Path(key_path)
            if key_path is not None
            else (
                self.db_path.with_suffix(".key.dpapi")
                if self._custom_db_path
                else self._default_key_path()
            )
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_standard_sqlite_database()
        self.init_db()

    @staticmethod
    def _default_db_path() -> Path:
        if getattr(sys, "frozen", False):
            local_app_data = Path(
                os.environ.get(
                    "LOCALAPPDATA",
                    Path.home() / "AppData" / "Local",
                )
            )
            return local_app_data / "MindShield" / "mindshield.db"
        return Path(__file__).resolve().parents[2] / "mindshield.db"

    @staticmethod
    def _default_key_path() -> Path:
        local_app_data = Path(
            os.environ.get(
                "LOCALAPPDATA",
                Path.home() / "AppData" / "Local",
            )
        )
        return local_app_data / "MindShield" / "database.key.dpapi"

    def _ensure_standard_sqlite_database(self) -> None:
        if not self.db_path.exists() or self.db_path.stat().st_size == 0:
            return
        with self.db_path.open("rb") as database_file:
            header = database_file.read(16)
        if header != b"SQLite format 3\x00":
            if self.key_path.is_file():
                self._migrate_legacy_sqlcipher_database()
                return
            raise RuntimeError(
                f"{self.db_path} is not a standard SQLite database. "
                "Back up and migrate this database before using standard SQLite."
            )

    def _migrate_legacy_sqlcipher_database(self) -> None:
        try:
            import pywintypes
            import sqlcipher3.dbapi2 as sqlcipher
            import win32crypt
        except ImportError as error:
            raise RuntimeError(
                "This legacy encrypted database requires the Windows SQLCipher "
                "migration dependencies. The original database was not changed."
            ) from error

        try:
            key = win32crypt.CryptUnprotectData(
                self.key_path.read_bytes(),
                None,
                None,
                None,
                0,
            )[1]
        except (OSError, pywintypes.error) as error:
            raise RuntimeError(
                f"Could not decrypt the legacy database key at {self.key_path}. "
                "The original database was not changed."
            ) from error
        if len(key) != 32:
            raise RuntimeError(
                f"The legacy database key at {self.key_path} is invalid. "
                "The original database was not changed."
            )

        try:
            with closing(sqlcipher.connect(str(self.db_path))) as encrypted:
                encrypted.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
                integrity = encrypted.execute("PRAGMA quick_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise RuntimeError("The legacy SQLCipher database failed its integrity check")

                tables = {
                    row[0]
                    for row in encrypted.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                if "work_logs" not in tables:
                    raise RuntimeError("The legacy database has no work_logs table")

                work_log_columns = {
                    row[1] for row in encrypted.execute("PRAGMA table_info(work_logs)")
                }
                work_zone_column = (
                    "work_zone"
                    if "work_zone" in work_log_columns
                    else "work_zone_title"
                    if "work_zone_title" in work_log_columns
                    else None
                )
                if work_zone_column is None:
                    raise RuntimeError("The legacy work_logs table has no work-zone column")
                work_logs = encrypted.execute(
                    "SELECT id, date, start_time, duration_minutes, "
                    f"{work_zone_column} FROM work_logs"
                ).fetchall()
                settings = (
                    encrypted.execute("SELECT key, value FROM settings").fetchall()
                    if "settings" in tables
                    else []
                )
                break_logs = (
                    encrypted.execute(
                        "SELECT id, date, started_at, duration_seconds, "
                        "work_zone, completed_at FROM break_logs"
                    ).fetchall()
                    if "break_logs" in tables
                    else []
                )
        except sqlcipher.Error as error:
            raise RuntimeError(
                "Could not read the legacy SQLCipher database. "
                "The original database was not changed."
            ) from error

        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=f"{self.db_path.name}.",
            suffix=".sqlite.tmp",
            dir=self.db_path.parent,
        )
        os.close(temporary_fd)
        temporary_path = Path(temporary_name)
        try:
            with closing(sqlite3.connect(temporary_path)) as connection, connection:
                self._create_schema(connection)
                connection.executemany(
                    """
                    INSERT INTO work_logs
                        (id, date, start_time, duration_minutes, work_zone)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    work_logs,
                )
                connection.executemany(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    settings,
                )
                connection.executemany(
                    """
                    INSERT INTO break_logs
                        (id, date, started_at, duration_seconds, work_zone, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    break_logs,
                )
                integrity = connection.execute("PRAGMA quick_check").fetchone()
                if integrity is None or integrity[0] != "ok":
                    raise RuntimeError("The migrated SQLite database failed its integrity check")

            backup_path = self._create_legacy_backup()
            os.replace(temporary_path, self.db_path)
            self._migration_backup_path = backup_path
        except (OSError, sqlite3.Error) as error:
            raise RuntimeError(
                "Could not migrate the legacy SQLCipher database. "
                "The original database was not changed."
            ) from error
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _create_legacy_backup(self) -> Path:
        backup_path = self.db_path.with_name(
            f"{self.db_path.name}.sqlcipher.bak"
        )
        if backup_path.is_file() and filecmp.cmp(
            self.db_path,
            backup_path,
            shallow=False,
        ):
            return backup_path
        suffix = 1
        while backup_path.exists():
            backup_path = self.db_path.with_name(
                f"{self.db_path.name}.sqlcipher.{suffix}.bak"
            )
            if backup_path.is_file() and filecmp.cmp(
                self.db_path,
                backup_path,
                shallow=False,
            ):
                return backup_path
            suffix += 1

        created_backup = False
        try:
            with self.db_path.open("rb") as source, backup_path.open("xb") as backup:
                created_backup = True
                shutil.copyfileobj(source, backup)
                backup.flush()
                os.fsync(backup.fileno())
        except OSError:
            if created_backup and backup_path.exists():
                backup_path.unlink()
            raise
        return backup_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def init_db(self) -> None:
        """Create the database schema, migrating the former work-zone column."""
        with closing(self._connect()) as connection, connection:
            self._create_schema(connection)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(work_logs)")
            }
            if "work_zone_title" in columns and "work_zone" not in columns:
                connection.execute(
                    "ALTER TABLE work_logs RENAME COLUMN work_zone_title TO work_zone"
                )

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS work_logs (
                id INTEGER PRIMARY KEY,
                date TEXT,
                start_time TEXT,
                duration_minutes INTEGER,
                work_zone TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT UNIQUE,
                value TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS break_logs (
                id INTEGER PRIMARY KEY,
                date TEXT NOT NULL,
                started_at TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL,
                work_zone TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )

    def log_scheduled_break_start(
        self,
        duration_seconds: int,
        work_zone: str,
    ) -> int:
        """Record an automatic eye-care break and return its database ID."""
        now = datetime.now()
        return self.add_break_log(
            now.date().isoformat(),
            now.strftime("%H:%M"),
            duration_seconds,
            work_zone,
            False,
        )

    def add_break_log(
        self,
        break_date: str,
        start_time: str,
        duration_seconds: int,
        work_zone: str,
        completed: bool,
    ) -> int:
        """Add a scheduled-break record with explicit date, time, and status."""
        try:
            normalized_date = date.fromisoformat(break_date).isoformat()
        except (TypeError, ValueError) as error:
            raise ValueError("date must use YYYY-MM-DD format") from error
        try:
            normalized_time = datetime.strptime(start_time, "%H:%M").time()
        except (TypeError, ValueError) as error:
            raise ValueError("start_time must use HH:MM format") from error
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int)
            or duration_seconds <= 0
        ):
            raise ValueError("duration_seconds must be a positive integer")
        if not isinstance(work_zone, str) or not work_zone.strip():
            raise ValueError("work_zone must be a non-empty string")
        if not isinstance(completed, bool):
            raise TypeError("completed must be a bool")

        started_at = datetime.combine(
            date.fromisoformat(normalized_date),
            normalized_time,
        )
        completed_at = (
            (started_at + timedelta(seconds=duration_seconds)).isoformat(
                timespec="seconds"
            )
            if completed
            else None
        )
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO break_logs (
                    date, started_at, duration_seconds, work_zone, completed_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    normalized_date,
                    started_at.isoformat(timespec="seconds"),
                    duration_seconds,
                    work_zone.strip(),
                    completed_at,
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("Could not record scheduled break")
            return cursor.lastrowid

    def complete_scheduled_break(self, break_id: int) -> None:
        """Mark a scheduled break complete when its countdown reaches zero."""
        if isinstance(break_id, bool) or not isinstance(break_id, int) or break_id <= 0:
            raise ValueError("break_id must be a positive integer")

        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE break_logs
                SET completed_at = ?
                WHERE id = ? AND completed_at IS NULL
                """,
                (datetime.now().isoformat(timespec="seconds"), break_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Scheduled break {break_id} is missing or already complete")

    def get_break_logs(
        self,
    ) -> list[tuple[int, str, str, int, str, bool]]:
        """Return scheduled-break records for profile editing."""
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT id, date, started_at, duration_seconds, work_zone, completed_at
                FROM break_logs
                ORDER BY date DESC, started_at DESC, id DESC
                """
            ).fetchall()
            return [
                (
                    int(row["id"]),
                    str(row["date"]),
                    str(row["started_at"]),
                    int(row["duration_seconds"]),
                    str(row["work_zone"]),
                    row["completed_at"] is not None,
                )
                for row in rows
            ]

    def update_break_log(
        self,
        break_id: int,
        break_date: str,
        start_time: str,
        duration_seconds: int,
        work_zone: str,
        completed: bool,
    ) -> None:
        """Edit a scheduled-break record and its completion status."""
        if (
            isinstance(break_id, bool)
            or not isinstance(break_id, int)
            or break_id <= 0
        ):
            raise ValueError("break_id must be a positive integer")
        try:
            normalized_date = date.fromisoformat(break_date).isoformat()
        except (TypeError, ValueError) as error:
            raise ValueError("date must use YYYY-MM-DD format") from error
        try:
            normalized_time = datetime.strptime(start_time, "%H:%M").time()
        except (TypeError, ValueError) as error:
            raise ValueError("start_time must use HH:MM format") from error
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int)
            or duration_seconds <= 0
        ):
            raise ValueError("duration_seconds must be a positive integer")
        if not isinstance(work_zone, str) or not work_zone.strip():
            raise ValueError("work_zone must be a non-empty string")
        if not isinstance(completed, bool):
            raise TypeError("completed must be a bool")

        started_at = datetime.combine(
            date.fromisoformat(normalized_date),
            normalized_time,
        ).isoformat(timespec="seconds")
        completed_at = (
            (
                datetime.fromisoformat(started_at)
                + timedelta(seconds=duration_seconds)
            ).isoformat(timespec="seconds")
            if completed
            else None
        )
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE break_logs
                SET date = ?, started_at = ?, duration_seconds = ?,
                    work_zone = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    normalized_date,
                    started_at,
                    duration_seconds,
                    work_zone.strip(),
                    completed_at,
                    break_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Scheduled break {break_id} does not exist")

    def delete_break_log(self, break_id: int) -> None:
        """Delete a scheduled-break record by ID."""
        if isinstance(break_id, bool) or not isinstance(break_id, int) or break_id <= 0:
            raise ValueError("break_id must be a positive integer")
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM break_logs WHERE id = ?",
                (break_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Scheduled break {break_id} does not exist")

    def get_monthly_break_compliance(self) -> tuple[int, int]:
        """Return completed and total scheduled breaks for the current month."""
        month_start = date.today().replace(day=1).isoformat()
        today = date.today().isoformat()
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(completed_at),
                    COUNT(*)
                FROM break_logs
                WHERE date BETWEEN ? AND ?
                """,
                (month_start, today),
            ).fetchone()
            return int(row[0]), int(row[1])

    def get_monthly_total_minutes(self) -> int:
        """Return logged focus minutes from the start of this calendar month."""
        month_start = date.today().replace(day=1).isoformat()
        today = date.today().isoformat()
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(duration_minutes), 0)
                FROM work_logs
                WHERE date BETWEEN ? AND ?
                """,
                (month_start, today),
            ).fetchone()
            return int(row[0])

    def get_setting(self, key: str) -> str | None:
        """Return a saved preference, or ``None`` when it has not been set."""
        if not isinstance(key, str) or not key.strip():
            raise ValueError("key must be a non-empty string")

        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key.strip(),),
            ).fetchone()
            if row is None:
                return None
            value = row["value"]
            if not isinstance(value, str):
                raise ValueError(f"Saved setting {key!r} must be text")
            return value

    def set_settings(self, settings: dict[str, str]) -> None:
        """Save multiple preferences together in one transaction."""
        if not isinstance(settings, dict):
            raise TypeError("settings must be a dictionary")
        if any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            for key, value in settings.items()
        ):
            raise ValueError("setting keys must be non-empty strings and values must be text")

        with closing(self._connect()) as connection, connection:
            connection.executemany(
                """
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                [(key.strip(), value) for key, value in settings.items()],
            )

    def log_session(self, duration_mins: int, work_zone: str) -> None:
        """Store a completed session."""
        now = datetime.now()
        self.add_work_log(
            now.date().isoformat(),
            now.time().strftime("%H:%M"),
            duration_mins,
            work_zone,
        )

    def add_work_log(
        self,
        log_date: str,
        start_time: str,
        duration_mins: int,
        work_zone: str,
    ) -> None:
        """Add a focus session with an explicitly selected date and time."""
        try:
            normalized_date = date.fromisoformat(log_date).isoformat()
        except (TypeError, ValueError) as error:
            raise ValueError("date must use YYYY-MM-DD format") from error
        try:
            normalized_time = datetime.strptime(start_time, "%H:%M").time()
        except (TypeError, ValueError) as error:
            raise ValueError("start_time must use HH:MM format") from error
        if (
            isinstance(duration_mins, bool)
            or not isinstance(duration_mins, int)
            or duration_mins <= 0
        ):
            raise ValueError("duration_mins must be a positive integer number of minutes")
        if not isinstance(work_zone, str) or not work_zone.strip():
            raise ValueError("work_zone must be a non-empty string")

        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO work_logs (date, start_time, duration_minutes, work_zone)
                VALUES (?, ?, ?, ?)
                """,
                (
                    normalized_date,
                    normalized_time.isoformat(timespec="seconds"),
                    duration_mins,
                    work_zone.strip(),
                ),
            )

    def get_work_logs(self) -> list[tuple[int, str, str, int, str]]:
        """Return all focus sessions ordered newest first for profile editing."""
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT id, date, start_time, duration_minutes, work_zone
                FROM work_logs
                ORDER BY date DESC, start_time DESC, id DESC
                """
            ).fetchall()
            return [
                (
                    int(row["id"]),
                    str(row["date"]),
                    str(row["start_time"]),
                    int(row["duration_minutes"]),
                    str(row["work_zone"]),
                )
                for row in rows
            ]

    def update_work_log(
        self,
        log_id: int,
        log_date: str,
        start_time: str,
        duration_mins: int,
        work_zone: str,
    ) -> None:
        """Update a focus session and reject invalid dates, times, or values."""
        if isinstance(log_id, bool) or not isinstance(log_id, int) or log_id <= 0:
            raise ValueError("log_id must be a positive integer")
        try:
            normalized_date = date.fromisoformat(log_date).isoformat()
        except (TypeError, ValueError) as error:
            raise ValueError("date must use YYYY-MM-DD format") from error
        try:
            normalized_time = datetime.strptime(start_time, "%H:%M").time()
        except (TypeError, ValueError) as error:
            raise ValueError("start_time must use HH:MM format") from error
        if (
            isinstance(duration_mins, bool)
            or not isinstance(duration_mins, int)
            or duration_mins <= 0
        ):
            raise ValueError("duration_mins must be a positive integer")
        if not isinstance(work_zone, str) or not work_zone.strip():
            raise ValueError("work_zone must be a non-empty string")

        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE work_logs
                SET date = ?, start_time = ?, duration_minutes = ?, work_zone = ?
                WHERE id = ?
                """,
                (
                    normalized_date,
                    normalized_time.isoformat(timespec="seconds"),
                    duration_mins,
                    work_zone.strip(),
                    log_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Focus session {log_id} does not exist")

    def delete_work_log(self, log_id: int) -> None:
        """Delete one focus session by ID."""
        if isinstance(log_id, bool) or not isinstance(log_id, int) or log_id <= 0:
            raise ValueError("log_id must be a positive integer")
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "DELETE FROM work_logs WHERE id = ?",
                (log_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Focus session {log_id} does not exist")

    def reset_analytics(self) -> None:
        """Clear focus and break history while preserving user preferences."""
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM work_logs")
            connection.execute("DELETE FROM break_logs")
            connection.execute(
                """
                INSERT INTO settings (key, value)
                VALUES ('demo_data_seeded', 'true')
                ON CONFLICT(key) DO UPDATE SET value = 'true'
                """
            )

    def get_todays_total_minutes(self) -> int:
        """Return the total logged work minutes for the current local date."""
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(duration_minutes), 0) "
                "FROM work_logs WHERE date = ?",
                (date.today().isoformat(),),
            ).fetchone()
            return int(row[0])

    def get_weekly_logs(self) -> list[tuple[str, int]]:
        """Return daily focus totals for each of the past seven calendar days."""
        today = date.today()
        first_day = today - timedelta(days=6)
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT date, SUM(duration_minutes) AS total_minutes
                FROM work_logs
                WHERE date BETWEEN ? AND ?
                GROUP BY date
                ORDER BY date ASC
                """,
                (first_day.isoformat(), today.isoformat()),
            ).fetchall()
            totals_by_date = {
                row["date"]: int(row["total_minutes"])
                for row in rows
            }

        return [
            (
                (first_day + timedelta(days=offset)).isoformat(),
                totals_by_date.get((first_day + timedelta(days=offset)).isoformat(), 0),
            )
            for offset in range(7)
        ]

    def get_current_streak_days(self) -> int:
        """Return the current consecutive logging streak, allowing today or yesterday."""
        today = date.today()
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT DISTINCT date FROM work_logs WHERE date <= ? ORDER BY date DESC",
                (today.isoformat(),),
            ).fetchall()

        logged_dates = {date.fromisoformat(row["date"]) for row in rows}
        streak_date = today if today in logged_dates else today - timedelta(days=1)
        streak = 0
        while streak_date in logged_dates:
            streak += 1
            streak_date -= timedelta(days=1)
        return streak

    def seed_demo_data(self) -> None:
        """Insert realistic sessions for the past seven days if no logs exist."""
        daily_sessions = (
            ((45, "Deep Work"), (30, "Reading")),
            ((50, "Deep Work"),),
            ((35, "Planning"), (40, "Deep Work")),
            ((60, "Deep Work"), (25, "Reading")),
            ((45, "Study"),),
            ((30, "Planning"), (55, "Deep Work")),
            ((40, "Deep Work"), (30, "Study")),
        )
        today = date.today()

        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            count = connection.execute(
                "SELECT COUNT(*) FROM work_logs"
            ).fetchone()[0]
            seeded = connection.execute(
                "SELECT value FROM settings WHERE key = 'demo_data_seeded'"
            ).fetchone()
            if count or (seeded is not None and seeded["value"] == "true"):
                connection.execute(
                    """
                    INSERT INTO settings (key, value)
                    VALUES ('demo_data_seeded', 'true')
                    ON CONFLICT(key) DO UPDATE SET value = 'true'
                    """
                )
                return

            demo_rows: list[tuple[str, str, int, str]] = []
            for day_offset, sessions in enumerate(daily_sessions):
                log_date = today - timedelta(days=6 - day_offset)
                for session_index, (duration, zone_title) in enumerate(sessions):
                    start = time(hour=9 + session_index * 2, minute=15 + session_index * 10)
                    demo_rows.append(
                        (
                            log_date.isoformat(),
                            start.isoformat(timespec="seconds"),
                            duration,
                            zone_title,
                        )
                    )

            connection.executemany(
                """
                INSERT INTO work_logs (date, start_time, duration_minutes, work_zone)
                VALUES (?, ?, ?, ?)
                """,
                demo_rows,
            )
            connection.execute(
                """
                INSERT INTO settings (key, value)
                VALUES ('demo_data_seeded', 'true')
                ON CONFLICT(key) DO UPDATE SET value = 'true'
                """
            )
