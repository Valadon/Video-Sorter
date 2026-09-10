"""Startup helpers for configuration, build identity, and process locking."""

from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import BinaryIO

from dotenv import load_dotenv


APPLICATION_NAME = "Video Sorter"
APPLICATION_VERSION = "2026.09.10"
BUILD_INFO_FILENAME = "build-info.json"
LOCK_FILENAME = ".video_sorter.lock"


class RuntimeConfigurationError(RuntimeError):
    """Raised when the selected runtime configuration cannot be loaded."""


class AlreadyRunningError(RuntimeError):
    """Raised when another sorter holds the configuration's process lock."""


@dataclass(frozen=True)
class BuildInfo:
    """Non-secret identity embedded into release bundles."""

    version: str = APPLICATION_VERSION
    commit: str = "unknown"
    built_at: str = "unknown"
    workflow_run: str = "unknown"


def _application_directory() -> Path:
    """Return the executable folder when frozen, otherwise the source folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resolve_config_path(config_path: str | os.PathLike[str] | None = None) -> Path:
    """Resolve an explicit config path or use the application-folder default.

    Explicit relative paths are resolved from the caller's current directory.
    The default is stable even when a shortcut or scheduler uses another working
    directory: source runs use the repository folder and packaged runs use the
    folder containing ``video_sorter.exe``.
    """
    if config_path is None:
        return _application_directory() / "config.ini"

    candidate = Path(config_path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve()


def load_runtime_config(config_path: str | os.PathLike[str]) -> ConfigParser:
    """Load one config file with the parser behavior used by the sorter."""
    path = Path(config_path)
    parser = ConfigParser(inline_comment_prefixes=("#", ";"))
    try:
        with path.open("r", encoding="utf-8-sig") as config_file:
            parser.read_file(config_file)
    except FileNotFoundError as exc:
        raise RuntimeConfigurationError(f"Config file does not exist: {path}") from exc
    except OSError as exc:
        raise RuntimeConfigurationError(f"Could not read config file {path}: {exc}") from exc
    return parser


def config_environment_path(config_path: str | os.PathLike[str]) -> Path:
    """Return the only .env path associated with the selected config."""
    return Path(config_path).resolve().parent / ".env"


def load_config_environment(config_path: str | os.PathLike[str]) -> bool:
    """Load credentials from the selected config folder, if present.

    Existing process environment values take precedence. This supports managed
    environment injection while preventing an unrelated current directory from
    silently selecting a different ``.env`` file.
    """
    return bool(load_dotenv(config_environment_path(config_path), override=False))


def lock_path_for_config(config_path: str | os.PathLike[str]) -> Path:
    """Use one process lock per absolute configuration folder."""
    return Path(config_path).resolve().parent / LOCK_FILENAME


class SingleInstanceLock:
    """A non-blocking process lock released automatically by the OS on exit."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._file: BinaryIO | None = None

    @property
    def acquired(self) -> bool:
        return self._file is not None

    def acquire(self) -> "SingleInstanceLock":
        if self._file is not None:
            return self

        # Keep a byte in the file because Windows locks a byte range. The file is
        # only a stable lock target; lock ownership is held by the open handle.
        lock_file = self.path.open("a+b")
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)

        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock_file.close()
            raise AlreadyRunningError(
                f"Another Video Sorter instance is already using {self.path.parent}. "
                f"Lock: {self.path}"
            ) from exc

        self._file = lock_file
        return self

    def release(self) -> None:
        lock_file = self._file
        if lock_file is None:
            return

        self._file = None
        try:
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()

    def __enter__(self) -> "SingleInstanceLock":
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


def _build_info_directories() -> list[Path]:
    directories = []
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        directories.append(Path(bundled_root))
    directories.append(_application_directory())
    return list(dict.fromkeys(directories))


def _safe_value(value: object, *, default: str, pattern: str, limit: int = 80) -> str:
    text = str(value).strip()[:limit]
    return text if re.fullmatch(pattern, text) else default


def build_info() -> BuildInfo:
    """Read bundled build metadata, falling back safely for source runs."""
    for directory in _build_info_directories():
        path = directory / BUILD_INFO_FILENAME
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue

        return BuildInfo(
            version=_safe_value(
                payload.get("version"),
                default=APPLICATION_VERSION,
                pattern=r"[A-Za-z0-9][A-Za-z0-9._+-]*",
            ),
            commit=_safe_value(
                payload.get("commit"), default="unknown", pattern=r"[0-9a-fA-F]{7,64}"
            ),
            built_at=_safe_value(
                payload.get("built_at"),
                default="unknown",
                pattern=r"[0-9TZ:+.-]+",
            ),
            workflow_run=_safe_value(
                payload.get("workflow_run"), default="unknown", pattern=r"[0-9]+"
            ),
        )
    return BuildInfo()


def version_string() -> str:
    """Return an operator-readable version and build identity."""
    info = build_info()
    commit = info.commit[:12] if info.commit != "unknown" else "unknown"
    details = [f"commit {commit}", f"built {info.built_at}"]
    if info.workflow_run != "unknown":
        details.append(f"workflow {info.workflow_run}")
    return f"{APPLICATION_NAME} {info.version} ({', '.join(details)})"


def build_metadata_payload(*, commit: str, workflow_run: str = "unknown") -> dict[str, str]:
    """Create the small, non-secret metadata payload used by build automation."""
    return {
        "application": APPLICATION_NAME,
        "version": APPLICATION_VERSION,
        "commit": commit,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "workflow_run": workflow_run,
    }
