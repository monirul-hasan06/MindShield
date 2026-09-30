"""SQLCipher-encrypted persistence for MindShield sessions and settings."""

from __future__ import annotations

import os
import secrets
import sys
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pywintypes
import sqlcipher3.dbapi2 as sqlite3
import win32crypt


class DatabaseHandler:
    """Create and access MindShield's per-user encrypted SQLCipher database."""

    def __init__(
        self,
        db_path: str | Path | None = None,
        key_path: str | Path | None = None,
    ) -> None:
        self.db_path = (
            Path(db_path)
            if db_path is not None
            else self._default_db_path()
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.key_path = (
            Path(key_path)
            if key_path is not None
            else (
                self._default_key_path()
                if db_path is None
                else self.db_path.with_suffix(".key.dpapi")
            )
        )
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        self._encryption_key = self._load_encryption_key()
        self._verify_sqlcipher()
        self._encrypt_existing_database()
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

    def _load_encryption_key(self) -> bytes:
        if self.key_path.exists():
            try:
                key = win32crypt.CryptUnprotectData(
                    self.key_path.read_bytes(),
                    None,
                    None,
                    None,
                    0,
                )[1]
            except pywintypes.error as error:
                raise RuntimeError(
                    f"Cannot decrypt the MindShield database key at {self.key_path}"
                ) from error
            if len(key) != 32:
                raise ValueError("The protected MindShield database key is invalid")
            return key

        key = secrets.token_bytes(32)
        protected_key = win32crypt.CryptProtectData(
            key,
            "MindShield database key",
            None,
            None,
            None,
            0,
        )
        try:
            with self.key_path.open("xb") as key_file:
                key_file.write(protected_key)
        except FileExistsError:
            try:
                existing_key = win32crypt.CryptUnprotectData(
                    self.key_path.read_bytes(),
                    None,
                    None,
                    None,
                    0,
                )[1]
            except pywintypes.error as error:
                raise RuntimeError(
                    f"Cannot decrypt the MindShield database key at {self.key_path}"
                ) from error
            if len(existing_key) != 32:
                raise ValueError("The protected MindShield database key is invalid")
            return existing_key
        return key

    @staticmethod
    def _verify_sqlcipher() -> None:
        with closing(sqlite3.connect(":memory:")) as connection:
            row = connection.execute("PRAGMA cipher_version").fetchone()
            if row is None or not row[0]:
                raise RuntimeError("MindShield requires a SQLCipher-enabled SQLite runtime")

    def _encrypt_existing_database(self) -> None:
        if not self.db_path.exists() or self.db_path.stat().st_size == 0:
            return
        with self.db_path.open("rb") as database_file:
            header = database_file.read(16)
        if header != b"SQLite format 3\x00":
            return

        temporary_path = self.db_path.with_name(
            f"{self.db_path.name}.encrypted.tmp"
        )
        if temporary_path.exists():
            temporary_path.unlink()

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "ATTACH DATABASE ? AS encrypted KEY ?",
                (str(temporary_path), f"x'{self._encryption_key.hex()}'"),
            )
            connection.execute("SELECT sqlcipher_export('encrypted')")
            connection.execute("DETACH DATABASE encrypted")
        except sqlite3.Error as error:
            connection.close()
            if temporary_path.exists():
                temporary_path.unlink()
            raise RuntimeError(
                f"Could not encrypt the existing database at {self.db_path}"
            ) from error
        finally:
            connection.close()

        try:
            with closing(sqlite3.connect(temporary_path)) as encrypted:
                encrypted.execute(
                    f"PRAGMA key = \"x'{self._encryption_key.hex()}'\""
                )
                encrypted.execute("SELECT count(*) FROM sqlite_master").fetchone()
            os.replace(temporary_path, self.db_path)
        except (OSError, sqlite3.Error) as error:
            if temporary_path.exists():
                temporary_path.unlink()
            raise RuntimeError(
                f"Could not finalize database encryption at {self.db_path}"
            ) from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute(f"PRAGMA key = \"x'{self._encryption_key.hex()}'\"")
        connection.row_factory = sqlite3.Row
        return connection

    def init_db(self) -> None:
        """Create the database schema, migrating the former work-zone column."""
        with closing(self._connect()) as connection, connection:
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
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(work_logs)")
            }
            if "work_zone_title" in columns and "work_zone" not in columns:
                connection.execute(
                    "ALTER TABLE work_logs RENAME COLUMN work_zone_title TO work_zone"
                )

    def log_scheduled_break_start(
        self,
        duration_seconds: int,
        work_zone: str,
    ) -> int:
        """Record an automatic eye-care break and return its database ID."""
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int)
            or duration_seconds <= 0
        ):
            raise ValueError("duration_seconds must be a positive integer")
        if not isinstance(work_zone, str) or not work_zone.strip():
            raise ValueError("work_zone must be a non-empty string")

        now = datetime.now()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO break_logs
                    (date, started_at, duration_seconds, work_zone)
                VALUES (?, ?, ?, ?)
                """,
                (
                    now.date().isoformat(),
                    now.isoformat(timespec="seconds"),
                    duration_seconds,
                    work_zone.strip(),
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
        if (
            isinstance(duration_mins, bool)
            or not isinstance(duration_mins, int)
            or duration_mins <= 0
        ):
            raise ValueError("duration_mins must be a positive integer number of minutes")
        if not isinstance(work_zone, str) or not work_zone.strip():
            raise ValueError("work_zone must be a non-empty string")

        now = datetime.now()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO work_logs (date, start_time, duration_minutes, work_zone)
                VALUES (?, ?, ?, ?)
                """,
                (
                    now.date().isoformat(),
                    now.time().isoformat(timespec="seconds"),
                    duration_mins,
                    work_zone.strip(),
                ),
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
            if count:
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
