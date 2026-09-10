import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import runtime_guard
from runtime_guard import (
    AlreadyRunningError,
    RuntimeConfigurationError,
    SingleInstanceLock,
    build_info,
    config_environment_path,
    load_config_environment,
    load_runtime_config,
    lock_path_for_config,
    resolve_config_path,
    version_string,
)


def test_explicit_relative_config_is_resolved_from_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert resolve_config_path("settings/config.ini") == tmp_path / "settings" / "config.ini"


def test_default_config_is_beside_source_module(monkeypatch):
    monkeypatch.delattr(runtime_guard.sys, "frozen", raising=False)

    assert resolve_config_path() == Path(runtime_guard.__file__).resolve().parent / "config.ini"


def test_frozen_default_config_is_beside_executable(tmp_path, monkeypatch):
    executable = tmp_path / "video_sorter.exe"
    monkeypatch.setattr(runtime_guard.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime_guard.sys, "executable", str(executable))

    assert resolve_config_path() == tmp_path / "config.ini"


def test_load_runtime_config_handles_inline_comments_and_utf8_bom(tmp_path):
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "\ufeff[Settings]\nmode = Upload # selected mode\nstart_time_tolerance = 30\n",
        encoding="utf-8",
    )

    config = load_runtime_config(config_path)

    assert config.get("Settings", "mode") == "Upload"
    assert config.getint("Settings", "start_time_tolerance") == 30


def test_missing_runtime_config_names_the_selected_path(tmp_path):
    config_path = tmp_path / "missing.ini"

    with pytest.raises(RuntimeConfigurationError) as error:
        load_runtime_config(config_path)
    assert str(config_path) in str(error.value)


def test_config_environment_only_loads_dotenv_beside_selected_config(tmp_path, monkeypatch):
    selected = tmp_path / "selected"
    unrelated = tmp_path / "unrelated"
    selected.mkdir()
    unrelated.mkdir()
    config_path = selected / "config.ini"
    config_path.write_text("[Settings]\nmode=Upload\n", encoding="utf-8")
    (selected / ".env").write_text("RUNTIME_GUARD_TEST_VALUE=selected\n", encoding="utf-8")
    (unrelated / ".env").write_text("RUNTIME_GUARD_TEST_VALUE=unrelated\n", encoding="utf-8")
    monkeypatch.chdir(unrelated)
    monkeypatch.delenv("RUNTIME_GUARD_TEST_VALUE", raising=False)

    assert config_environment_path(config_path) == selected / ".env"
    assert load_config_environment(config_path) is True
    assert os.environ["RUNTIME_GUARD_TEST_VALUE"] == "selected"


def test_config_environment_preserves_managed_environment_value(tmp_path, monkeypatch):
    config_path = tmp_path / "config.ini"
    config_path.write_text("[Settings]\nmode=Upload\n", encoding="utf-8")
    (tmp_path / ".env").write_text("RUNTIME_GUARD_TEST_VALUE=file\n", encoding="utf-8")
    monkeypatch.setenv("RUNTIME_GUARD_TEST_VALUE", "managed")

    load_config_environment(config_path)

    assert os.environ["RUNTIME_GUARD_TEST_VALUE"] == "managed"


def test_lock_path_is_scoped_to_selected_config_folder(tmp_path):
    assert lock_path_for_config(tmp_path / "config.ini") == tmp_path / ".video_sorter.lock"


def test_single_instance_lock_refuses_a_second_holder_and_can_be_reacquired(tmp_path):
    path = tmp_path / ".video_sorter.lock"

    with SingleInstanceLock(path):
        with pytest.raises(AlreadyRunningError, match="Another Video Sorter instance"):
            SingleInstanceLock(path).acquire()

    with SingleInstanceLock(path) as reacquired:
        assert reacquired.acquired is True


def test_os_releases_lock_when_holder_process_exits(tmp_path):
    path = tmp_path / ".video_sorter.lock"
    child_code = (
        "import sys; "
        "from runtime_guard import SingleInstanceLock; "
        "lock = SingleInstanceLock(sys.argv[1]).acquire(); "
        "print('locked', flush=True); "
        "sys.stdin.read(1)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code, str(path)],
        cwd=Path(runtime_guard.__file__).resolve().parent,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "locked"
        with pytest.raises(AlreadyRunningError):
            SingleInstanceLock(path).acquire()
    finally:
        child.terminate()
        child.wait(timeout=10)

    with SingleInstanceLock(path) as reacquired:
        assert reacquired.acquired is True


def test_version_string_uses_sanitized_build_metadata(tmp_path, monkeypatch):
    metadata = {
        "version": "2026.09.10",
        "commit": "ae3b3240123456789abcdef0123456789abcdef0",
        "built_at": "2026-09-10T16:30:00+00:00",
        "workflow_run": "123456789",
    }
    (tmp_path / "build-info.json").write_text(json.dumps(metadata), encoding="utf-8")
    monkeypatch.setattr(runtime_guard, "_build_info_directories", lambda: [tmp_path])

    assert build_info().commit == metadata["commit"]
    assert version_string() == (
        "Video Sorter 2026.09.10 "
        "(commit ae3b32401234, built 2026-09-10T16:30:00+00:00, workflow 123456789)"
    )


def test_invalid_build_metadata_cannot_inject_output(tmp_path, monkeypatch):
    metadata = {
        "version": "unsafe\nvalue",
        "commit": "secret text",
        "built_at": "also unsafe",
        "workflow_run": "1; Write-Host secret",
    }
    (tmp_path / "build-info.json").write_text(json.dumps(metadata), encoding="utf-8")
    monkeypatch.setattr(runtime_guard, "_build_info_directories", lambda: [tmp_path])

    assert version_string() == (
        "Video Sorter 2026.09.10 (commit unknown, built unknown)"
    )


def test_main_version_does_not_require_a_config(monkeypatch, capsys):
    import video_sorter

    def unexpected_config_load(*args, **kwargs):
        raise AssertionError("--version must not load a runtime config")

    monkeypatch.setattr(video_sorter, "load_runtime_config", unexpected_config_load)

    assert video_sorter.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("Video Sorter 2026.09.10 (")


def test_main_lock_collision_exits_before_processing(tmp_path, monkeypatch, capsys):
    import video_sorter

    config_path = tmp_path / "config.ini"
    config_path.write_text("[Paths]\n", encoding="utf-8")

    def unexpected_processing(*args, **kwargs):
        raise AssertionError("a duplicate process must not enter operational startup")

    monkeypatch.setattr(video_sorter, "_run_operational", unexpected_processing)

    with SingleInstanceLock(lock_path_for_config(config_path)):
        result = video_sorter.main(["--config", str(config_path), "--run-once"])

    assert result == 1
    assert "Another Video Sorter instance is already using" in capsys.readouterr().err
