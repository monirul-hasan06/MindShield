"""SQLCipher-encrypted persistence for MindShield sessions and settings."""

from __future__ import annotations

import csv
import os
import secrets
import shutil
import sys
import tempfile
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from collections.abc import Sequence
from zipfile import ZIP_STORED, BadZipFile, ZipFile

import pywintypes
import sqlcipher3.dbapi2 as sqlite3
import win32crypt


@dataclass(frozen=True)
class WorkZoneRule:
    """A persisted rule used to match an active window."""

    id: int
    match_type: str
    pattern: str
    enabled: bool
    match_mode: str = "contains"


@dataclass(frozen=True)
class WorkZoneProfile:
    """A named set of work-zone matching rules."""

    id: int
    name: str
    rules: tuple[WorkZoneRule, ...]


@dataclass(frozen=True)
class SessionRecord:
    """A focus session row safe to show in history and export to CSV."""

    date: str
    start_time: str
    duration_seconds: int
    work_zone: str

    @property
    def duration_minutes(self) -> int:
        """Whole minutes rounded from the exact recorded duration."""
        return int(round(self.duration_seconds / 60))


@dataclass(frozen=True)
class FocusGuardRule:
    """A local rule identifying an application or active-window distraction."""

    id: int
    match_type: str
    pattern: str
    enabled: bool
    match_mode: str = "contains"


@dataclass(frozen=True)
class ProductivitySummary:
    """Recorded work and completed-break time for a selected date range."""

    work_seconds: int
    break_seconds: int


class DatabaseHandler:
    """Create and access MindShield's per-user encrypted SQLCipher database."""

    WORK_ZONE_MATCH_TYPES = frozenset(
        {"any", "title", "process", "path", "accessible"}
    )
    FOCUS_GUARD_MATCH_TYPES = frozenset(
        {"title", "process", "path", "accessible"}
    )
    MATCH_MODES = frozenset({"contains", "token"})
    SCHEMA_VERSION = 3
    MAX_BACKUP_DATABASE_BYTES = 512 * 1024 * 1024
    EFFECTIVE_WORK_SECONDS_SQL = (
        "CASE WHEN COALESCE(duration_seconds, 0) > 0 "
        "THEN duration_seconds ELSE COALESCE(duration_minutes, 0) * 60 END"
    )

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
            return self._unprotect_key(self.key_path.read_bytes())

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
            return self._unprotect_key(self.key_path.read_bytes())
        return key

    def _unprotect_key(self, protected_key: bytes) -> bytes:
        try:
            key = win32crypt.CryptUnprotectData(
                protected_key,
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
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        return connection

    def backup_to(self, backup_path: str | Path) -> None:
        """Write an encrypted database and its user-protected key to a backup."""
        target = Path(backup_path).expanduser()
        resolved_target = target.resolve()
        if resolved_target in (self.db_path.resolve(), self.key_path.resolve()):
            raise ValueError("The backup path must not overwrite MindShield data")
        target.parent.mkdir(parents=True, exist_ok=True)

        staged_archive: Path | None = None
        try:
            with tempfile.TemporaryDirectory(dir=target.parent) as temp_dir:
                exported_database = Path(temp_dir) / "mindshield.db"
                with closing(self._connect()) as connection:
                    try:
                        connection.execute(
                            "ATTACH DATABASE ? AS backup KEY ?",
                            (
                                str(exported_database),
                                f"x'{self._encryption_key.hex()}'",
                            ),
                        )
                        connection.execute("SELECT sqlcipher_export('backup')")
                        connection.execute("DETACH DATABASE backup")
                    except sqlite3.Error as error:
                        raise RuntimeError("Could not export the encrypted database") from error

                with tempfile.NamedTemporaryFile(
                    prefix=f".{target.name}.",
                    suffix=".tmp",
                    dir=target.parent,
                    delete=False,
                ) as temporary_archive:
                    staged_archive = Path(temporary_archive.name)
                with ZipFile(staged_archive, "w") as archive:
                    archive.write(exported_database, "mindshield.db")
                    archive.write(self.key_path, "database.key.dpapi")
            os.replace(staged_archive, target)
        except (OSError, BadZipFile) as error:
            raise RuntimeError(f"Could not create MindShield backup at {target}") from error
        finally:
            if staged_archive is not None and staged_archive.exists():
                staged_archive.unlink()

    def restore_from(self, backup_path: str | Path) -> None:
        """Restore a backup created by :meth:`backup_to` for this Windows user.

        Callers must ensure no work or break phase is active. The current database
        and key are replaced only after the archive and restored database have been
        verified.
        """
        source = Path(backup_path).expanduser()
        if source.resolve() in (self.db_path.resolve(), self.key_path.resolve()):
            raise ValueError("The backup path must not be a MindShield data file")

        staged_database: Path | None = None
        staged_key: Path | None = None
        previous_database: Path | None = None
        previous_key: Path | None = None
        old_encryption_key = self._encryption_key
        installed_database = False
        installed_key = False

        try:
            with ZipFile(source, "r") as archive:
                entries = archive.infolist()
                if (
                    len(entries) != 2
                    or {entry.filename for entry in entries}
                    != {"mindshield.db", "database.key.dpapi"}
                    or any(entry.flag_bits & 0x1 for entry in entries)
                    or any(entry.compress_type != ZIP_STORED for entry in entries)
                ):
                    raise ValueError("The selected file is not a valid MindShield backup")
                key_info = archive.getinfo("database.key.dpapi")
                database_info = archive.getinfo("mindshield.db")
                if key_info.file_size > 4096:
                    raise ValueError("The protected database key in the backup is invalid")
                if (
                    database_info.file_size <= 0
                    or database_info.file_size > self.MAX_BACKUP_DATABASE_BYTES
                ):
                    raise ValueError("The backup database is too large to restore")
                protected_key = archive.read(key_info)
                restored_key = self._unprotect_key(protected_key)

                staged_database = self._create_staging_file(self.db_path.parent, ".db")
                staged_key = self._create_staging_file(self.key_path.parent, ".key.dpapi")
                with archive.open("mindshield.db") as archive_database:
                    with staged_database.open("wb") as database_file:
                        shutil.copyfileobj(archive_database, database_file)
                staged_key.write_bytes(protected_key)

            self._verify_database_file(staged_database, restored_key)
            previous_database = self._reserve_staging_path(self.db_path.parent, ".previous.db")
            previous_key = self._reserve_staging_path(self.key_path.parent, ".previous.key")
            if self.db_path.exists():
                os.replace(self.db_path, previous_database)
            else:
                previous_database = None
            if self.key_path.exists():
                os.replace(self.key_path, previous_key)
            else:
                previous_key = None

            os.replace(staged_key, self.key_path)
            installed_key = True
            os.replace(staged_database, self.db_path)
            installed_database = True
            self._encryption_key = restored_key
            self.init_db()
        except Exception:
            if installed_database and self.db_path.exists():
                self.db_path.unlink()
            if installed_key and self.key_path.exists():
                self.key_path.unlink()
            if previous_database is not None and previous_database.exists():
                os.replace(previous_database, self.db_path)
            if previous_key is not None and previous_key.exists():
                os.replace(previous_key, self.key_path)
            self._encryption_key = old_encryption_key
            raise
        finally:
            for temporary_path in (
                staged_database,
                staged_key,
                previous_database,
                previous_key,
            ):
                if temporary_path is not None and temporary_path.exists():
                    temporary_path.unlink()

    @staticmethod
    def _create_staging_file(directory: Path, suffix: str) -> Path:
        descriptor, name = tempfile.mkstemp(
            prefix=".mindshield-restore-",
            suffix=suffix,
            dir=directory,
        )
        os.close(descriptor)
        return Path(name)

    @staticmethod
    def _reserve_staging_path(directory: Path, suffix: str) -> Path:
        path = DatabaseHandler._create_staging_file(directory, suffix)
        path.unlink()
        return path

    @staticmethod
    def _verify_database_file(database_path: Path, key: bytes) -> None:
        try:
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
                connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlite3.Error as error:
            raise ValueError(
                "The backup database is corrupt or its key does not match"
            ) from error

    def init_db(self) -> None:
        """Create or migrate the encrypted schema up to :attr:`SCHEMA_VERSION`."""
        with closing(self._connect()) as connection, connection:
            version = self._current_schema_version(connection)
            if version < 1:
                self._migrate_to_v1(connection)
                self._store_schema_version(connection, 1)
            if version < 2:
                self._migrate_to_v2(connection)
                self._store_schema_version(connection, 2)
            if version < 3:
                self._migrate_to_v3(connection)
                self._store_schema_version(connection, 3)

    @staticmethod
    def _current_schema_version(connection: sqlite3.Connection) -> int:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY
            )
            """
        )
        row = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
        if row is None or row[0] is None:
            return 0
        return int(row[0])

    @staticmethod
    def _store_schema_version(connection: sqlite3.Connection, version: int) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
            (version,),
        )

    def _migrate_to_v1(self, connection: sqlite3.Connection) -> None:
        """Create the original tables and one-time column renames."""
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
                completed_at TEXT,
                break_type TEXT NOT NULL DEFAULT 'scheduled'
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS focus_guard_rules (
                id INTEGER PRIMARY KEY,
                match_type TEXT NOT NULL,
                pattern TEXT NOT NULL,
                enabled INTEGER NOT NULL CHECK (enabled IN (0, 1))
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS work_zone_profiles (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS work_zone_rules (
                id INTEGER PRIMARY KEY,
                profile_id INTEGER NOT NULL
                    REFERENCES work_zone_profiles(id) ON DELETE CASCADE,
                match_type TEXT NOT NULL,
                pattern TEXT NOT NULL,
                enabled INTEGER NOT NULL CHECK (enabled IN (0, 1))
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
            columns.remove("work_zone_title")
            columns.add("work_zone")
        if "duration_seconds" not in columns:
            connection.execute(
                "ALTER TABLE work_logs "
                "ADD COLUMN duration_seconds INTEGER NOT NULL DEFAULT 0"
            )
            connection.execute(
                "UPDATE work_logs SET duration_seconds = "
                "COALESCE(duration_minutes, 0) * 60"
            )
        break_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(break_logs)")
        }
        if "break_type" not in break_columns:
            connection.execute(
                "ALTER TABLE break_logs "
                "ADD COLUMN break_type TEXT NOT NULL DEFAULT 'scheduled'"
            )
        self._migrate_work_zone_profiles(connection)

    @staticmethod
    def _migrate_to_v2(connection: sqlite3.Connection) -> None:
        """Index activity dates and backfill exact durations for older rows."""
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_work_logs_date ON work_logs(date)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_break_logs_date ON break_logs(date)"
        )
        connection.execute(
            """
            UPDATE work_logs
            SET duration_seconds = COALESCE(duration_minutes, 0) * 60
            WHERE COALESCE(duration_seconds, 0) = 0
                AND COALESCE(duration_minutes, 0) > 0
            """
        )

    @staticmethod
    def _migrate_to_v3(connection: sqlite3.Connection) -> None:
        """Record whether each rule is a substring or a whole-token match."""
        for table in ("work_zone_rules", "focus_guard_rules"):
            columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if "match_mode" not in columns:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN match_mode "
                    "TEXT NOT NULL DEFAULT 'contains'"
                )

    def _migrate_work_zone_profiles(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        profiles = connection.execute(
            "SELECT id, name FROM work_zone_profiles ORDER BY id"
        ).fetchall()
        legacy_zone = connection.execute(
            "SELECT value FROM settings WHERE key = 'active_work_zone'"
        ).fetchone()
        legacy_name = legacy_zone["value"] if legacy_zone is not None else None
        if legacy_name is not None and not legacy_name.strip():
            raise ValueError("Saved active work zone must not be empty")

        if not profiles:
            profile_name = legacy_name or "Focus"
            cursor = connection.execute(
                "INSERT INTO work_zone_profiles (name) VALUES (?)",
                (profile_name,),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("Could not create the default work-zone profile")
            profile_id = cursor.lastrowid
            connection.execute(
                """
                INSERT INTO work_zone_rules (profile_id, match_type, pattern, enabled)
                VALUES (?, 'any', ?, 1)
                """,
                (profile_id, legacy_name or profile_name),
            )
            self._store_active_profile(connection, profile_id, profile_name)
            return

        active_id_setting = connection.execute(
            "SELECT value FROM settings WHERE key = 'active_work_zone_profile_id'"
        ).fetchone()
        active_profile = None
        if active_id_setting is not None:
            try:
                active_id = int(active_id_setting["value"])
            except (TypeError, ValueError):
                active_id = -1
            active_profile = connection.execute(
                "SELECT id, name FROM work_zone_profiles WHERE id = ?",
                (active_id,),
            ).fetchone()

        if active_profile is None:
            active_profile = profiles[0]
            self._store_active_profile(
                connection,
                int(active_profile["id"]),
                str(active_profile["name"]),
            )

        rule_count = connection.execute(
            "SELECT COUNT(*) FROM work_zone_rules WHERE profile_id = ?",
            (active_profile["id"],),
        ).fetchone()[0]
        if not rule_count:
            pattern = legacy_name or str(active_profile["name"])
            connection.execute(
                """
                INSERT INTO work_zone_rules (profile_id, match_type, pattern, enabled)
                VALUES (?, 'any', ?, 1)
                """,
                (active_profile["id"], pattern),
            )

    @staticmethod
    def _store_active_profile(
        connection: sqlite3.Connection,
        profile_id: int,
        name: str,
    ) -> None:
        connection.executemany(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            [
                ("active_work_zone_profile_id", str(profile_id)),
                ("active_work_zone", name),
            ],
        )

    def log_scheduled_break_start(
        self,
        duration_seconds: int,
        work_zone: str,
        break_type: str = "scheduled",
    ) -> int:
        """Record a timed break and return its database ID."""
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int)
            or duration_seconds <= 0
        ):
            raise ValueError("duration_seconds must be a positive integer")
        if not isinstance(work_zone, str) or not work_zone.strip():
            raise ValueError("work_zone must be a non-empty string")
        if break_type not in ("scheduled", "manual"):
            raise ValueError("break_type must be 'scheduled' or 'manual'")

        now = datetime.now()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO break_logs
                    (date, started_at, duration_seconds, work_zone, break_type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    now.date().isoformat(),
                    now.isoformat(timespec="seconds"),
                    duration_seconds,
                    work_zone.strip(),
                    break_type,
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
                    AND break_type = 'scheduled'
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
                f"""
                SELECT COALESCE(SUM({self.EFFECTIVE_WORK_SECONDS_SQL}), 0)
                FROM work_logs
                WHERE date BETWEEN ? AND ?
                """,
                (month_start, today),
            ).fetchone()
            return int(round(int(row[0]) / 60))

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

    def list_work_zone_profiles(self) -> list[WorkZoneProfile]:
        """Return all named work-zone profiles and their rules."""
        with closing(self._connect()) as connection, connection:
            profile_rows = connection.execute(
                "SELECT id, name FROM work_zone_profiles ORDER BY id"
            ).fetchall()
            rule_rows = connection.execute(
                """
                SELECT id, profile_id, match_type, pattern, enabled, match_mode
                FROM work_zone_rules
                ORDER BY profile_id, id
                """
            ).fetchall()

        rules_by_profile: dict[int, list[WorkZoneRule]] = {}
        for row in rule_rows:
            profile_id = int(row["profile_id"])
            rules_by_profile.setdefault(profile_id, []).append(
                WorkZoneRule(
                    id=int(row["id"]),
                    match_type=str(row["match_type"]),
                    pattern=str(row["pattern"]),
                    enabled=bool(row["enabled"]),
                    match_mode=str(row["match_mode"] or "contains"),
                )
            )
        return [
            WorkZoneProfile(
                id=int(row["id"]),
                name=str(row["name"]),
                rules=tuple(rules_by_profile.get(int(row["id"]), ())),
            )
            for row in profile_rows
        ]

    @staticmethod
    def _normalize_rules(
        rules: Sequence[tuple[object, ...]],
        allowed_types: frozenset[str],
        label: str,
    ) -> list[tuple[str, str, int, str]]:
        """Validate rule tuples, defaulting omitted modes to substring contains."""
        normalized: list[tuple[str, str, int, str]] = []
        for rule in rules:
            if not isinstance(rule, tuple) or len(rule) not in (3, 4):
                raise ValueError(
                    "Each rule must contain a type, pattern, and enabled flag"
                )
            if len(rule) == 3:
                match_type, pattern, enabled = rule
                match_mode = "contains"
            else:
                match_type, pattern, enabled, match_mode = rule
            if not isinstance(match_type, str) or match_type not in allowed_types:
                raise ValueError(f"Unsupported {label} match type: {match_type!r}")
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError(f"{label.capitalize()} patterns must not be empty")
            if not isinstance(enabled, bool):
                raise TypeError("Rule enabled flags must be bool values")
            if (
                not isinstance(match_mode, str)
                or match_mode not in DatabaseHandler.MATCH_MODES
            ):
                raise ValueError(f"Unsupported {label} match mode: {match_mode!r}")
            normalized.append((match_type, pattern.strip(), int(enabled), match_mode))
        return normalized

    def get_active_work_zone_profile(self) -> WorkZoneProfile:
        """Return the active work-zone profile, falling back to the first one."""
        profiles = self.list_work_zone_profiles()
        if not profiles:
            raise RuntimeError("MindShield has no work-zone profiles")

        setting = self.get_setting("active_work_zone_profile_id")
        try:
            active_id = int(setting) if setting is not None else -1
        except ValueError:
            active_id = -1
        return next(
            (profile for profile in profiles if profile.id == active_id),
            profiles[0],
        )

    def save_work_zone_profile(
        self,
        name: str,
        rules: Sequence[tuple[str, str, bool]],
        profile_id: int | None = None,
    ) -> int:
        """Create or update a profile with typed, enabled substring rules."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Profile name must be a non-empty string")
        if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
            raise TypeError("rules must be a sequence of rule tuples")
        normalized_rules = self._normalize_rules(
            rules,
            self.WORK_ZONE_MATCH_TYPES,
            "work-zone",
        )
        if not any(enabled for _, _, enabled, _mode in normalized_rules):
            raise ValueError("Enable at least one work-zone rule")
        if profile_id is not None and (
            isinstance(profile_id, bool)
            or not isinstance(profile_id, int)
            or profile_id <= 0
        ):
            raise ValueError("profile_id must be a positive integer")

        with closing(self._connect()) as connection, connection:
            if profile_id is None:
                cursor = connection.execute(
                    "INSERT INTO work_zone_profiles (name) VALUES (?)",
                    (name.strip(),),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("Could not create work-zone profile")
                profile_id = cursor.lastrowid
            else:
                cursor = connection.execute(
                    "UPDATE work_zone_profiles SET name = ? WHERE id = ?",
                    (name.strip(), profile_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"Work-zone profile {profile_id} does not exist")
                connection.execute(
                    "DELETE FROM work_zone_rules WHERE profile_id = ?",
                    (profile_id,),
                )

            connection.executemany(
                """
                INSERT INTO work_zone_rules
                    (profile_id, match_type, pattern, enabled, match_mode)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (profile_id, match_type, pattern, enabled, match_mode)
                    for match_type, pattern, enabled, match_mode in normalized_rules
                ],
            )
            active_profile_id = connection.execute(
                "SELECT value FROM settings WHERE key = 'active_work_zone_profile_id'"
            ).fetchone()
            if (
                active_profile_id is not None
                and active_profile_id["value"] == str(profile_id)
            ):
                self._store_active_profile(connection, profile_id, name.strip())
            return profile_id

    def set_active_work_zone_profile(self, profile_id: int) -> WorkZoneProfile:
        """Select a profile and keep the legacy work-zone setting in sync."""
        if isinstance(profile_id, bool) or not isinstance(profile_id, int) or profile_id <= 0:
            raise ValueError("profile_id must be a positive integer")

        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT id, name FROM work_zone_profiles WHERE id = ?",
                (profile_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Work-zone profile {profile_id} does not exist")
            self._store_active_profile(connection, profile_id, str(row["name"]))

        return next(
            profile
            for profile in self.list_work_zone_profiles()
            if profile.id == profile_id
        )

    def delete_work_zone_profile(self, profile_id: int) -> None:
        """Delete a profile unless it is the last available profile."""
        if isinstance(profile_id, bool) or not isinstance(profile_id, int) or profile_id <= 0:
            raise ValueError("profile_id must be a positive integer")

        with closing(self._connect()) as connection, connection:
            profile_count = connection.execute(
                "SELECT COUNT(*) FROM work_zone_profiles"
            ).fetchone()[0]
            if profile_count <= 1:
                raise ValueError("MindShield must keep at least one work-zone profile")
            cursor = connection.execute(
                "DELETE FROM work_zone_profiles WHERE id = ?",
                (profile_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"Work-zone profile {profile_id} does not exist")

            active_id = connection.execute(
                "SELECT value FROM settings WHERE key = 'active_work_zone_profile_id'"
            ).fetchone()
            if active_id is not None and active_id["value"] == str(profile_id):
                replacement = connection.execute(
                    "SELECT id, name FROM work_zone_profiles ORDER BY id LIMIT 1"
                ).fetchone()
                self._store_active_profile(
                    connection,
                    int(replacement["id"]),
                    str(replacement["name"]),
                )

    def get_session_history(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        search: str = "",
    ) -> list[SessionRecord]:
        """Return filtered work-log rows without transient window metadata."""
        if not isinstance(search, str):
            raise TypeError("search must be text")
        for value in (start_date, end_date):
            if value is not None and (
                not isinstance(value, date) or isinstance(value, datetime)
            ):
                raise TypeError("session history dates must be date values")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("start_date must not be after end_date")

        conditions: list[str] = []
        parameters: list[str] = []
        if start_date is not None:
            conditions.append("date >= ?")
            parameters.append(start_date.isoformat())
        if end_date is not None:
            conditions.append("date <= ?")
            parameters.append(end_date.isoformat())
        if search.strip():
            conditions.append("instr(lower(work_zone), lower(?)) > 0")
            parameters.append(search.strip())
        where_clause = (
            f"WHERE {' AND '.join(conditions)}" if conditions else ""
        )

        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                f"""
                SELECT date, start_time,
                    {self.EFFECTIVE_WORK_SECONDS_SQL} AS duration_seconds,
                    work_zone
                FROM work_logs
                {where_clause}
                ORDER BY date DESC, start_time DESC, id DESC
                """,
                parameters,
            ).fetchall()
        return [
            SessionRecord(
                date=str(row["date"] or ""),
                start_time=str(row["start_time"] or ""),
                duration_seconds=int(row["duration_seconds"] or 0),
                work_zone=str(row["work_zone"] or ""),
            )
            for row in rows
        ]

    def export_sessions_csv(
        self,
        file_path: str | Path,
        start_date: date | None = None,
        end_date: date | None = None,
        search: str = "",
    ) -> int:
        """Export the matching date/time/duration/work-zone history to CSV."""
        records = self.get_session_history(start_date, end_date, search)
        with Path(file_path).open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(("Date", "Start time", "Duration (seconds)", "Work zone"))
            writer.writerows(
                (
                    record.date,
                    record.start_time,
                    record.duration_seconds,
                    record.work_zone,
                )
                for record in records
            )
        return len(records)

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

        self.log_work_segment(duration_mins * 60, work_zone)

    def log_work_segment(
        self,
        duration_seconds: int,
        work_zone: str,
        started_at: datetime | None = None,
    ) -> None:
        """Store an active work segment with second-level duration."""
        if (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, int)
            or duration_seconds <= 0
        ):
            raise ValueError("duration_seconds must be a positive integer")
        if not isinstance(work_zone, str) or not work_zone.strip():
            raise ValueError("work_zone must be a non-empty string")

        if started_at is None:
            started_at = datetime.now() - timedelta(seconds=duration_seconds)
        elif not isinstance(started_at, datetime):
            raise TypeError("started_at must be a datetime")
        duration_minutes = int(round(duration_seconds / 60))
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO work_logs
                    (date, start_time, duration_minutes, work_zone, duration_seconds)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    started_at.date().isoformat(),
                    started_at.time().isoformat(timespec="seconds"),
                    duration_minutes,
                    work_zone.strip(),
                    duration_seconds,
                ),
            )

    def get_focus_guard_rules(self) -> list[FocusGuardRule]:
        """Return persisted application and active-window block rules."""
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT id, match_type, pattern, enabled, match_mode
                FROM focus_guard_rules
                ORDER BY id
                """
            ).fetchall()
        return [
            FocusGuardRule(
                id=int(row["id"]),
                match_type=str(row["match_type"]),
                pattern=str(row["pattern"]),
                enabled=bool(row["enabled"]),
                match_mode=str(row["match_mode"] or "contains"),
            )
            for row in rows
        ]

    def save_focus_guard_rules(
        self,
        rules: Sequence[tuple[str, str, bool]],
    ) -> None:
        """Replace blocked-app/site rules after validating their types and values."""
        if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
            raise TypeError("rules must be a sequence of rule tuples")
        normalized_rules = self._normalize_rules(
            rules,
            self.FOCUS_GUARD_MATCH_TYPES,
            "focus-guard",
        )

        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM focus_guard_rules")
            connection.executemany(
                """
                INSERT INTO focus_guard_rules
                    (match_type, pattern, enabled, match_mode)
                VALUES (?, ?, ?, ?)
                """,
                normalized_rules,
            )

    def get_productivity_summary(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> ProductivitySummary:
        """Sum recorded work segments and completed timed breaks in a date range."""
        for value in (start_date, end_date):
            if value is not None and (
                not isinstance(value, date) or isinstance(value, datetime)
            ):
                raise TypeError("productivity summary dates must be date values")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("start_date must not be after end_date")

        work_conditions: list[str] = []
        break_conditions = ["completed_at IS NOT NULL"]
        work_parameters: list[str] = []
        break_parameters: list[str] = []
        if start_date is not None:
            work_conditions.append("date >= ?")
            break_conditions.append("date >= ?")
            work_parameters.append(start_date.isoformat())
            break_parameters.append(start_date.isoformat())
        if end_date is not None:
            work_conditions.append("date <= ?")
            break_conditions.append("date <= ?")
            work_parameters.append(end_date.isoformat())
            break_parameters.append(end_date.isoformat())
        work_where = (
            f"WHERE {' AND '.join(work_conditions)}" if work_conditions else ""
        )
        break_where = f"WHERE {' AND '.join(break_conditions)}"

        with closing(self._connect()) as connection, connection:
            work_row = connection.execute(
                f"""
                SELECT COALESCE(SUM({self.EFFECTIVE_WORK_SECONDS_SQL}), 0)
                FROM work_logs
                {work_where}
                """,
                work_parameters,
            ).fetchone()
            break_row = connection.execute(
                f"""
                SELECT COALESCE(SUM(duration_seconds), 0)
                FROM break_logs
                {break_where}
                """,
                break_parameters,
            ).fetchone()
        return ProductivitySummary(
            work_seconds=int(work_row[0]),
            break_seconds=int(break_row[0]),
        )

    def reset_analytics(self, preserve_break_id: int | None = None) -> None:
        """Clear saved activity, optionally retaining the active timed break."""
        if preserve_break_id is not None and (
            isinstance(preserve_break_id, bool)
            or not isinstance(preserve_break_id, int)
            or preserve_break_id <= 0
        ):
            raise ValueError("preserve_break_id must be a positive integer or None")

        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM work_logs")
            if preserve_break_id is None:
                connection.execute("DELETE FROM break_logs")
            else:
                connection.execute(
                    "DELETE FROM break_logs WHERE id != ?",
                    (preserve_break_id,),
                )

    def get_focus_seconds(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> int:
        """Return exact recorded work seconds for an inclusive date range."""
        for value in (start_date, end_date):
            if value is not None and (
                not isinstance(value, date) or isinstance(value, datetime)
            ):
                raise TypeError("focus dates must be date values")
        if start_date is not None and end_date is not None and start_date > end_date:
            raise ValueError("start_date must not be after end_date")

        conditions: list[str] = []
        parameters: list[str] = []
        if start_date is not None:
            conditions.append("date >= ?")
            parameters.append(start_date.isoformat())
        if end_date is not None:
            conditions.append("date <= ?")
            parameters.append(end_date.isoformat())
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                f"""
                SELECT COALESCE(SUM({self.EFFECTIVE_WORK_SECONDS_SQL}), 0)
                FROM work_logs
                {where_clause}
                """,
                parameters,
            ).fetchone()
            return int(row[0])

    def get_todays_total_minutes(self) -> int:
        """Return the total logged work minutes for the current local date."""
        return int(round(self.get_focus_seconds(date.today(), date.today()) / 60))

    def get_weekly_logs(self) -> list[tuple[str, int]]:
        """Return daily focus totals in seconds for each of the past seven days."""
        today = date.today()
        first_day = today - timedelta(days=6)
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                f"""
                SELECT date, SUM({self.EFFECTIVE_WORK_SECONDS_SQL}) AS total_seconds
                FROM work_logs
                WHERE date BETWEEN ? AND ?
                GROUP BY date
                ORDER BY date ASC
                """,
                (first_day.isoformat(), today.isoformat()),
            ).fetchall()
            totals_by_date = {
                row["date"]: int(row["total_seconds"])
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
                INSERT INTO work_logs (
                    date, start_time, duration_minutes, work_zone, duration_seconds
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (log_date, start_time, duration, zone, duration * 60)
                    for log_date, start_time, duration, zone in demo_rows
                ],
            )
