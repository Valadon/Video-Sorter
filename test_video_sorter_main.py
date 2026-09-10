from types import SimpleNamespace

from runtime_guard import SingleInstanceLock, lock_path_for_config
from upload_journal import (
    STATE_ATTACHED,
    STATE_BYTES_SUBMITTING,
    STATE_BYTES_UPLOADED,
    STATE_ENTRY_CREATING,
    UploadJournal,
)
import video_sorter


def test_version_exits_without_loading_config(monkeypatch, capsys):
    monkeypatch.setattr(video_sorter, 'version_string', lambda: 'Video Sorter test build')

    def unexpected_config_resolution(*_args, **_kwargs):
        raise AssertionError('--version must not resolve or load config')

    monkeypatch.setattr(video_sorter, 'resolve_config_path', unexpected_config_resolution)

    assert video_sorter.main(['--version']) == 0
    assert capsys.readouterr().out.strip() == 'Video Sorter test build'


def test_duplicate_instance_exits_before_environment_or_processing(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / 'config.ini'
    runtime_config = SimpleNamespace()
    monkeypatch.setattr(video_sorter, 'load_runtime_config', lambda _: runtime_config)

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError('duplicate startup must stop before environment or processing')

    monkeypatch.setattr(video_sorter, 'load_config_environment', unexpected_call)
    monkeypatch.setattr(video_sorter, '_run_operational', unexpected_call)

    with SingleInstanceLock(lock_path_for_config(config_path)):
        result = video_sorter.main(['--config', str(config_path), '--run-once'])

    assert result == 1
    assert 'Another Video Sorter instance is already using' in capsys.readouterr().err


def test_upload_status_does_not_load_credentials_or_process_files(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / 'config.ini'
    monkeypatch.setattr(video_sorter, 'load_runtime_config', lambda _: SimpleNamespace())

    def unexpected_call(*_args, **_kwargs):
        raise AssertionError('upload status must not load credentials or process files')

    monkeypatch.setattr(video_sorter, 'load_config_environment', unexpected_call)
    monkeypatch.setattr(video_sorter, '_run_operational', unexpected_call)

    secret_token_id = 'do-not-print-this-upload-token'
    with UploadJournal(str(tmp_path / 'upload_journal.sqlite3')) as journal:
        journal.get_or_create('a' * 64, 'u00100001', 'recording.mp4')
        journal.update(
            'a' * 64,
            'u00100001',
            STATE_ATTACHED,
            upload_token_id=secret_token_id,
            entry_id='entry-123',
        )

    result = video_sorter.main([
        '--config', str(config_path), '--upload-status'
    ])

    output = capsys.readouterr().out
    assert result == 0
    assert 'file=recording.mp4' in output
    assert 'sha256=aaaaaaaaaaaa...' in output
    assert 'owner=u00100001' in output
    assert 'state=attached' in output
    assert 'token_recorded=yes' in output
    assert 'entry=entry-123' in output
    assert secret_token_id not in output


def test_find_media_loads_selected_environment_and_uses_exact_lookup(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / 'config.ini'
    monkeypatch.setattr(video_sorter, 'load_runtime_config', lambda _: SimpleNamespace())
    environment_calls = []
    monkeypatch.setattr(
        video_sorter, 'load_config_environment', lambda path: environment_calls.append(path)
    )
    lookup_calls = []
    entry = SimpleNamespace(
        id='entry-123', name='Exact Media Name', userId='u1234567', status=2, duration=3600
    )
    media = SimpleNamespace(
        listByNameAndOwner=lambda name, owner: lookup_calls.append((name, owner)) or [entry]
    )
    monkeypatch.setattr(
        video_sorter, 'get_kaltura_client', lambda: SimpleNamespace(media=media)
    )

    result = video_sorter.main([
        '--config', str(config_path), '--find-media', 'Exact Media Name',
        '--owner', 'u1234567',
    ])

    output = capsys.readouterr().out
    assert result == 0
    assert environment_calls == [config_path]
    assert lookup_calls == [('Exact Media Name', 'u1234567')]
    assert 'id=entry-123 name=Exact Media Name owner=u1234567 status=2 duration=3600' in output


def test_verify_uploads_reads_each_journaled_entry_from_kaltura(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / 'config.ini'
    monkeypatch.setattr(video_sorter, 'load_runtime_config', lambda _: SimpleNamespace())
    monkeypatch.setattr(video_sorter, 'load_config_environment', lambda _: True)
    with UploadJournal(str(tmp_path / 'upload_journal.sqlite3')) as journal:
        journal.get_or_create('a' * 64, 'u00100001', 'recording.mp4')
        journal.update(
            'a' * 64, 'u00100001', STATE_ATTACHED,
            upload_token_id='hidden-token', entry_id='entry-123',
        )
    get_calls = []
    entry = SimpleNamespace(
        id='entry-123', name='Recorded Class', userId='u00100001', status=2, duration=3600
    )
    media = SimpleNamespace(get=lambda entry_id: get_calls.append(entry_id) or entry)
    monkeypatch.setattr(
        video_sorter, 'get_kaltura_client', lambda: SimpleNamespace(media=media)
    )

    result = video_sorter.main([
        '--config', str(config_path), '--verify-uploads'
    ])

    output = capsys.readouterr().out
    assert result == 0
    assert get_calls == ['entry-123']
    assert 'id=entry-123 name=Recorded Class owner=u00100001 status=2 duration=3600' in output
    assert 'hidden-token' not in output


def test_verify_upload_tokens_prints_safe_status_without_token_ids(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / 'config.ini'
    monkeypatch.setattr(video_sorter, 'load_runtime_config', lambda _: SimpleNamespace())
    monkeypatch.setattr(video_sorter, 'load_config_environment', lambda _: True)
    with UploadJournal(str(tmp_path / 'upload_journal.sqlite3')) as journal:
        journal.get_or_create(
            'b' * 64, 'u00100001', 'recording.mp4', source_size=123456
        )
        journal.update(
            'b' * 64, 'u00100001', 'bytes_submitting',
            upload_token_id='hidden-upload-token', confirmed_bytes=65536,
        )
    token_get_calls = []
    token = SimpleNamespace(
        status=0,
        fileSize=123456,
        uploadedFileSize=65536,
        uploadUrl='https://upload.example.edu/path?ks=hidden-query-value',
        updatedAt=1789052400,
    )
    upload_token_service = SimpleNamespace(
        get=lambda token_id: token_get_calls.append(token_id) or token
    )
    monkeypatch.setattr(
        video_sorter,
        'get_kaltura_client',
        lambda: SimpleNamespace(uploadToken=upload_token_service),
    )

    result = video_sorter.main([
        '--config', str(config_path), '--verify-upload-tokens'
    ])

    output = capsys.readouterr().out
    assert result == 0
    assert token_get_calls == ['hidden-upload-token']
    assert 'fingerprint=bbbbbbbbbbbb...' in output
    assert 'entry=- source=recording.mp4 owner=u00100001' in output
    assert 'status=0 fileSize=123456 uploadedFileSize=65536' in output
    assert 'uploadHost=upload.example.edu updatedAt=1789052400' in output
    assert 'hidden-upload-token' not in output
    assert 'hidden-query-value' not in output


def test_resume_upload_bytes_selects_one_receipt_and_reports_progress(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / 'config.ini'
    watch_folder = tmp_path / 'watch'
    watch_folder.mkdir()
    source = watch_folder / 'recording.mp4'
    source.write_bytes(b'bytes to resume')
    runtime_config = SimpleNamespace(
        get=lambda section, key: str(watch_folder)
        if (section, key) == ('Paths', 'watch_folder')
        else None
    )
    monkeypatch.setattr(video_sorter, 'load_runtime_config', lambda _: runtime_config)
    monkeypatch.setattr(video_sorter, 'load_config_environment', lambda _: True)
    source_hash = 'c' * 64
    with UploadJournal(str(tmp_path / 'upload_journal.sqlite3')) as journal:
        journal.get_or_create(
            source_hash, 'u00100001', source.name, source_size=source.stat().st_size
        )
        journal.update(
            source_hash, 'u00100001', STATE_BYTES_SUBMITTING,
            upload_token_id='hidden-upload-token', confirmed_bytes=3,
        )

    client = object()
    monkeypatch.setattr(video_sorter, 'get_kaltura_client', lambda: client)
    resume_calls = []

    def fake_resume(path, owner, passed_client, journal, sha, *, progress):
        resume_calls.append((path, owner, passed_client, sha))
        progress(3, source.stat().st_size)
        progress(source.stat().st_size, source.stat().st_size)
        return journal.update(
            sha,
            owner,
            STATE_BYTES_UPLOADED,
            confirmed_bytes=source.stat().st_size,
        )

    monkeypatch.setattr(video_sorter, 'resume_upload_bytes_only', fake_resume)

    result = video_sorter.main([
        '--config', str(config_path), '--resume-upload-bytes', source_hash[:12],
        '--owner', 'u00100001',
    ])

    output = capsys.readouterr().out
    assert result == 0
    assert resume_calls == [(str(source), 'u00100001', client, source_hash)]
    assert 'progress fingerprint=cccccccccccc...' in output
    assert f'confirmed_bytes={source.stat().st_size}' in output
    assert 'state=bytes_uploaded' in output
    assert 'hidden-upload-token' not in output


def test_resume_upload_bytes_refuses_entry_stage_receipt(
    tmp_path, monkeypatch, capsys
):
    config_path = tmp_path / 'config.ini'
    watch_folder = tmp_path / 'watch'
    watch_folder.mkdir()
    source = watch_folder / 'recording.mp4'
    source.write_bytes(b'already past byte stage')
    runtime_config = SimpleNamespace(
        get=lambda section, key: str(watch_folder)
    )
    monkeypatch.setattr(video_sorter, 'load_runtime_config', lambda _: runtime_config)
    monkeypatch.setattr(video_sorter, 'load_config_environment', lambda _: True)
    source_hash = 'd' * 64
    with UploadJournal(str(tmp_path / 'upload_journal.sqlite3')) as journal:
        journal.get_or_create(
            source_hash, 'u00100001', source.name, source_size=source.stat().st_size
        )
        journal.update(
            source_hash, 'u00100001', STATE_ENTRY_CREATING,
            upload_token_id='hidden-upload-token', confirmed_bytes=source.stat().st_size,
        )

    monkeypatch.setattr(
        video_sorter,
        'get_kaltura_client',
        lambda: (_ for _ in ()).throw(AssertionError('must not contact Kaltura')),
    )

    result = video_sorter.main([
        '--config', str(config_path), '--resume-upload-bytes', source_hash[:12],
        '--owner', 'u00100001',
    ])

    assert result == 1
    assert 'not eligible for bytes-only resume' in capsys.readouterr().err
