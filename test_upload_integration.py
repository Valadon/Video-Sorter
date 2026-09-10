from datetime import date, time
import logging

from data_types import Course, EventHost, LectureRecording
from kaltura_uploader import UploadNeedsManualReconciliation, UploadResult
from upload_journal import UploadJournal
import video_sorter


def upload_pair(tmp_path, host_count=2):
    source = tmp_path / '4603_20260910-1_20260910-090000_S1R1.mp4'
    source.write_bytes(b'one recording')
    recording = LectureRecording(
        str(source), date(2026, 9, 10), time(9, 0), '4603', 'extron'
    )
    hosts = [
        EventHost(f'OWNER{i}', 'TESTER', f'0010000{i}')
        for i in range(1, host_count + 1)
    ]
    course = Course(
        'LAW 7000', '1', 'Recovery Test', 'Tester', '4603', {'Thursday'}, time(9, 0), hosts
    )
    return source, recording, course


def test_upload_files_hashes_once_and_uses_fresh_client_per_owner(
    tmp_path, monkeypatch, caplog
):
    source, recording, course = upload_pair(tmp_path)
    journal = UploadJournal(str(tmp_path / 'upload_journal.sqlite3'))
    clients = []
    upload_calls = []
    hash_calls = []
    moved = []

    def fake_client():
        client = object()
        clients.append(client)
        return client

    def fake_hash(path):
        hash_calls.append(path)
        return 'source-hash'

    def fake_upload(rec, matched_course, client, name, index, **kwargs):
        upload_calls.append((rec, matched_course, client, name, index, kwargs))
        return UploadResult(f'entry-{index}')

    monkeypatch.setattr(video_sorter, 'get_kaltura_client', fake_client)
    monkeypatch.setattr(video_sorter, 'sha256_file', fake_hash)
    monkeypatch.setattr(video_sorter, 'upload_video', fake_upload)
    monkeypatch.setattr(video_sorter, 'get_new_filepath', lambda *_: str(tmp_path / 'sorted.mp4'))
    monkeypatch.setattr(video_sorter, 'move_video', lambda rec, path: moved.append((rec, path)))

    with caplog.at_level(logging.INFO):
        video_sorter.upload_files([(recording, course)], str(tmp_path), journal)

    journal.close()
    assert hash_calls == [str(source)]
    assert len(clients) == 2
    assert [call[2] for call in upload_calls] == clients
    assert [call[4] for call in upload_calls] == [0, 1]
    assert all(call[5]['journal'] is journal for call in upload_calls)
    assert all(call[5]['source_sha256'] == 'source-hash' for call in upload_calls)
    assert len(moved) == 1
    assert 'owner u0100001, entry entry-0' in caplog.text
    assert 'owner u0100002, entry entry-1' in caplog.text


def test_manual_reconciliation_stops_owner_and_leaves_source(
    tmp_path, monkeypatch, caplog
):
    source, recording, course = upload_pair(tmp_path, host_count=1)
    journal = UploadJournal(str(tmp_path / 'upload_journal.sqlite3'))
    moved = []

    monkeypatch.setattr(video_sorter, 'sha256_file', lambda _: 'source-hash')
    monkeypatch.setattr(video_sorter, 'get_kaltura_client', lambda: object())

    def needs_review(*_args, **_kwargs):
        raise UploadNeedsManualReconciliation('attachment outcome is unknown')

    monkeypatch.setattr(video_sorter, 'upload_video', needs_review)
    monkeypatch.setattr(video_sorter, 'get_new_filepath', lambda *_: str(tmp_path / 'sorted.mp4'))
    monkeypatch.setattr(video_sorter, 'move_video', lambda *args: moved.append(args))

    with caplog.at_level(logging.ERROR):
        video_sorter.upload_files([(recording, course)], str(tmp_path), journal)

    journal.close()
    assert source.exists()
    assert moved == []
    assert 'Manual Kaltura reconciliation is required' in caplog.text
    assert 'owner u0100001' in caplog.text
