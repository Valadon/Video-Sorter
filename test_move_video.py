from datetime import date, time
import logging

import pytest

from data_types import LectureRecording
import video_sorter


def test_move_video_raises_when_the_move_cannot_be_verified(tmp_path, monkeypatch, caplog):
    source = tmp_path / 'recording.mp4'
    destination = tmp_path / 'sorted.mp4'
    source.write_bytes(b'recording')
    recording = LectureRecording(
        str(source), date(2026, 9, 10), time(10, 0), '4603', 'extron'
    )
    monkeypatch.setattr(video_sorter.shutil, 'move', lambda *_: str(destination))

    with caplog.at_level(logging.ERROR), pytest.raises(OSError, match='Move verification failed'):
        video_sorter.move_video(recording, str(destination))

    assert source.exists()
    assert not destination.exists()
    assert recording.filepath == str(source)
    assert 'An error occurred while moving' in caplog.text


def test_move_video_updates_recording_only_after_verified_move(tmp_path):
    source = tmp_path / 'recording.mp4'
    destination = tmp_path / 'sorted.mp4'
    source.write_bytes(b'recording')
    recording = LectureRecording(
        str(source), date(2026, 9, 10), time(10, 0), '4603', 'extron'
    )

    video_sorter.move_video(recording, str(destination))

    assert not source.exists()
    assert destination.read_bytes() == b'recording'
    assert recording.filepath == str(destination)
