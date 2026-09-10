from datetime import date, time
import io
import sqlite3
from types import SimpleNamespace

import pytest
import requests

from data_types import Course, EventHost, LectureRecording
from kaltura_uploader import (
    BYTE_RECONCILE_PREFIX,
    UploadNeedsManualReconciliation,
    _upload_bytes_in_chunks,
    receipt_allows_bytes_resume,
    resume_upload_bytes_only,
    upload_video,
)
from mock_kaltura_client import (
    KalturaApiError,
    KalturaClient,
    KalturaConfiguration,
    KalturaOutcomeUnknown,
    KalturaUploadToken,
)
from upload_journal import (
    STATE_ATTACHED,
    STATE_BYTES_SUBMITTING,
    STATE_BYTES_UPLOADED,
    STATE_ENTRY_CREATING,
    STATE_MANUAL_RECONCILE,
    STATE_TOKEN_CREATED,
    UploadJournal,
    sha256_file,
)


class FakeUploadTokenService:
    def __init__(self):
        self.add_calls = 0
        self.upload_calls = 0
        self.chunk_calls = []
        self.file_size = 0
        self.uploaded_size = 0
        self.token_id = None

    def add(self, upload_token):
        self.add_calls += 1
        self.token_id = f'token-{self.add_calls}'
        self.file_size = upload_token.fileSize
        return self._token()

    def _token(self):
        status = 2 if self.file_size == self.uploaded_size and self.file_size else (
            1 if self.uploaded_size else 0
        )
        return KalturaUploadToken(
            id=self.token_id,
            status=status,
            fileSize=self.file_size,
            uploadedFileSize=self.uploaded_size,
        )

    def get(self, upload_token_id):
        assert upload_token_id == self.token_id
        return self._token()

    def uploadChunk(
        self, upload_token_id, chunk_data, resume, final_chunk, resume_at
    ):
        assert upload_token_id == self.token_id
        data = chunk_data.read()
        self.chunk_calls.append((resume_at, len(data), resume, final_chunk))
        assert resume_at == self.uploaded_size
        self.uploaded_size += len(data)
        return self._token()

    def upload(self, upload_token_id, _file_data, _resume, _final_chunk, _resume_at):
        self.upload_calls += 1
        return KalturaUploadToken(id=upload_token_id, status=2)

    def waitForFullUpload(self, upload_token_id, expected_size=None):
        token = self.get(upload_token_id)
        if expected_size is not None:
            assert token.uploadedFileSize == expected_size
        assert token.status == 2
        return token


class FakeMediaService:
    def __init__(self, add_errors=None, attach_errors=None):
        self.add_errors = list(add_errors or [])
        self.attach_errors = list(attach_errors or [])
        self.add_calls = 0
        self.attach_calls = 0

    def add(self, _media_entry):
        self.add_calls += 1
        if self.add_errors:
            error = self.add_errors.pop(0)
            if error is not None:
                raise error
        return SimpleNamespace(id=f'entry-{self.add_calls}')

    def addContent(self, _entry_id, _resource):
        self.attach_calls += 1
        if self.attach_errors:
            error = self.attach_errors.pop(0)
            if error is not None:
                raise error
        return {'ok': True}


class FakeClient:
    def __init__(self, *, add_errors=None, attach_errors=None):
        self.uploadToken = FakeUploadTokenService()
        self.media = FakeMediaService(add_errors, attach_errors)


@pytest.fixture
def upload_case(tmp_path):
    source = tmp_path / '4603_20260910-1_20260910-090000_S1R1.mp4'
    source.write_bytes(b'unique classroom recording bytes')
    recording = LectureRecording(
        str(source), date(2026, 9, 10), time(9, 0), '4603', 'extron'
    )
    hosts = [
        EventHost('ALPHA', 'OWNER', '00100001'),
        EventHost('BETA', 'OWNER', '00100002'),
    ]
    course = Course(
        'LAW 7000',
        '1',
        'Recovery Test',
        'Owner',
        '4603',
        {'Thursday'},
        time(9, 0),
        hosts,
    )
    journal = UploadJournal(str(tmp_path / 'upload_journal.sqlite3'))
    yield recording, course, journal, sha256_file(str(source))
    journal.close()


def test_completed_owner_is_reused_without_remote_calls(upload_case):
    recording, course, journal, source_hash = upload_case
    first_client = FakeClient()

    first = upload_video(
        recording,
        course,
        first_client,
        'Recovery Test_Owner_09-10-26',
        0,
        journal=journal,
        source_sha256=source_hash,
    )
    journal_path = journal.path
    journal.close()
    reopened_journal = UploadJournal(journal_path)
    second_client = FakeClient()
    try:
        second = upload_video(
            recording,
            course,
            second_client,
            'Recovery Test_Owner_09-10-26',
            0,
            journal=reopened_journal,
            source_sha256=source_hash,
        )

        assert first.already_completed is False
        assert second.already_completed is True
        assert second.entry_id == first.entry_id
        assert second_client.uploadToken.add_calls == 0
        assert second_client.uploadToken.upload_calls == 0
        assert second_client.media.add_calls == 0
        assert second_client.media.attach_calls == 0
        receipts = reopened_journal.list_receipts(STATE_ATTACHED)
        assert len(receipts) == 1
        assert receipts[0].entry_id == first.entry_id
    finally:
        reopened_journal.close()


def test_multi_owner_retry_does_not_repeat_completed_owner(upload_case):
    recording, course, journal, source_hash = upload_case
    first_owner_client = FakeClient()
    upload_video(
        recording,
        course,
        first_owner_client,
        'Recovery Test_Owner_09-10-26',
        0,
        journal=journal,
        source_sha256=source_hash,
    )

    second_owner_client = FakeClient(add_errors=[KalturaApiError('definite API rejection')])
    with pytest.raises(KalturaApiError, match='definite API rejection'):
        upload_video(
            recording,
            course,
            second_owner_client,
            'Recovery Test_Owner_09-10-26',
            1,
            journal=journal,
            source_sha256=source_hash,
        )

    retry_first_client = FakeClient()
    first_retry = upload_video(
        recording,
        course,
        retry_first_client,
        'Recovery Test_Owner_09-10-26',
        0,
        journal=journal,
        source_sha256=source_hash,
    )
    retry_second_client = FakeClient()
    upload_video(
        recording,
        course,
        retry_second_client,
        'Recovery Test_Owner_09-10-26',
        1,
        journal=journal,
        source_sha256=source_hash,
    )

    assert first_retry.already_completed is True
    assert retry_first_client.uploadToken.add_calls == 0
    assert retry_first_client.media.add_calls == 0
    # Owner two resumes after the confirmed byte upload instead of sending it again.
    assert retry_second_client.uploadToken.add_calls == 0
    assert retry_second_client.uploadToken.upload_calls == 0
    assert retry_second_client.media.add_calls == 1
    assert retry_second_client.media.attach_calls == 1


def test_unknown_media_add_is_held_without_blind_retry(upload_case):
    recording, course, journal, source_hash = upload_case
    first_client = FakeClient(
        add_errors=[KalturaOutcomeUnknown('response lost after media add')]
    )

    with pytest.raises(UploadNeedsManualReconciliation, match='media entry outcome is unknown'):
        upload_video(
            recording,
            course,
            first_client,
            'Recovery Test_Owner_09-10-26',
            0,
            journal=journal,
            source_sha256=source_hash,
        )

    retry_client = FakeClient()
    with pytest.raises(UploadNeedsManualReconciliation):
        upload_video(
            recording,
            course,
            retry_client,
            'Recovery Test_Owner_09-10-26',
            0,
            journal=journal,
            source_sha256=source_hash,
        )

    assert journal.get(source_hash, course.hosts[0].unid).state == STATE_MANUAL_RECONCILE
    assert retry_client.uploadToken.add_calls == 0
    assert retry_client.media.add_calls == 0


def test_unknown_attachment_is_held_with_known_entry(upload_case):
    recording, course, journal, source_hash = upload_case
    first_client = FakeClient(
        attach_errors=[KalturaOutcomeUnknown('response lost after attachment')]
    )

    with pytest.raises(UploadNeedsManualReconciliation, match='attachment outcome is unknown'):
        upload_video(
            recording,
            course,
            first_client,
            'Recovery Test_Owner_09-10-26',
            0,
            journal=journal,
            source_sha256=source_hash,
        )

    receipt = journal.get(source_hash, course.hosts[0].unid)
    assert receipt.state == STATE_MANUAL_RECONCILE
    assert receipt.entry_id == 'entry-1'


def prepare_chunk_receipt(tmp_path, data=b'abcdefghij', state=STATE_TOKEN_CREATED):
    source = tmp_path / 'recording.mp4'
    source.write_bytes(data)
    source_hash = sha256_file(str(source))
    journal = UploadJournal(str(tmp_path / 'chunks.sqlite3'))
    journal.get_or_create(source_hash, 'u0000001', source.name, len(data))
    journal.update(
        source_hash,
        'u0000001',
        state,
        upload_token_id='token-existing',
    )
    client = FakeClient()
    client.uploadToken.token_id = 'token-existing'
    client.uploadToken.file_size = len(data)
    return source, source_hash, journal, client


@pytest.mark.parametrize(
    ('data', 'expected_calls'),
    [
        (
            b'abcdefghij',
            [
                (0, 4, False, False),
                (4, 4, True, False),
                (8, 2, True, True),
            ],
        ),
        (
            b'abcdefgh',
            [
                (0, 4, False, False),
                (4, 4, True, True),
            ],
        ),
    ],
)
def test_chunk_upload_marks_only_last_data_chunk_final(
    tmp_path, data, expected_calls
):
    source, source_hash, journal, client = prepare_chunk_receipt(tmp_path, data)
    progress = []
    try:
        receipt = _upload_bytes_in_chunks(
            str(source),
            'u0000001',
            client,
            journal,
            source_hash,
            chunk_size=4,
            progress=lambda confirmed, total: progress.append((confirmed, total)),
        )
        assert receipt.state == STATE_BYTES_UPLOADED
        assert receipt.confirmed_bytes == len(data)
        assert client.uploadToken.chunk_calls == expected_calls
        assert progress[-1] == (len(data), len(data))
    finally:
        journal.close()


def test_fresh_token_may_start_when_uploaded_size_is_empty(tmp_path):
    source, source_hash, journal, client = prepare_chunk_receipt(tmp_path)
    original_get = client.uploadToken.get
    first_get = True

    def empty_fresh_position(upload_token_id):
        nonlocal first_get
        token = original_get(upload_token_id)
        if first_get:
            first_get = False
            token.uploadedFileSize = None
        return token

    client.uploadToken.get = empty_fresh_position
    try:
        receipt = _upload_bytes_in_chunks(
            str(source),
            'u0000001',
            client,
            journal,
            source_hash,
            chunk_size=4,
        )
        assert receipt.state == STATE_BYTES_UPLOADED
        assert client.uploadToken.chunk_calls[0] == (0, 4, False, False)
    finally:
        journal.close()


def test_explicit_resume_uses_authoritative_partial_offset_and_stops_at_bytes(
    tmp_path,
):
    source, source_hash, journal, client = prepare_chunk_receipt(
        tmp_path, state=STATE_BYTES_SUBMITTING
    )
    client.uploadToken.uploaded_size = 4
    journal.update(
        source_hash,
        'u0000001',
        STATE_BYTES_SUBMITTING,
        confirmed_bytes=0,
    )
    try:
        receipt = resume_upload_bytes_only(
            str(source),
            'u0000001',
            client,
            journal,
            source_hash,
        )
        assert receipt.state == STATE_BYTES_UPLOADED
        assert receipt.entry_id is None
        assert client.uploadToken.chunk_calls[0] == (4, 6, True, True)
        assert client.media.add_calls == 0
    finally:
        journal.close()


def test_explicit_resume_requires_reported_uploaded_size(tmp_path):
    source, source_hash, journal, client = prepare_chunk_receipt(
        tmp_path, state=STATE_BYTES_SUBMITTING
    )
    original_get = client.uploadToken.get

    def missing_position(upload_token_id):
        token = original_get(upload_token_id)
        token.uploadedFileSize = None
        return token

    client.uploadToken.get = missing_position
    try:
        with pytest.raises(
            UploadNeedsManualReconciliation,
            match='did not report uploadedFileSize',
        ):
            resume_upload_bytes_only(
                str(source),
                'u0000001',
                client,
                journal,
                source_hash,
            )
        assert client.uploadToken.chunk_calls == []
    finally:
        journal.close()


def test_eof_partial_token_polls_full_without_uploading_an_empty_chunk(tmp_path):
    source, source_hash, journal, client = prepare_chunk_receipt(
        tmp_path, state=STATE_BYTES_SUBMITTING
    )
    source_size = source.stat().st_size
    client.uploadToken.uploaded_size = source_size
    get_calls = 0

    def partial_at_eof(_upload_token_id):
        nonlocal get_calls
        get_calls += 1
        return KalturaUploadToken(
            id='token-existing',
            status=1,
            fileSize=source_size,
            uploadedFileSize=source_size,
        )

    def becomes_full(_upload_token_id, expected_size=None):
        assert expected_size == source_size
        return KalturaUploadToken(
            id='token-existing',
            status=2,
            fileSize=source_size,
            uploadedFileSize=source_size,
        )

    client.uploadToken.get = partial_at_eof
    client.uploadToken.waitForFullUpload = becomes_full
    try:
        receipt = resume_upload_bytes_only(
            str(source), 'u0000001', client, journal, source_hash
        )
        assert receipt.state == STATE_BYTES_UPLOADED
        assert receipt.confirmed_bytes == source_size
        assert client.uploadToken.chunk_calls == []
        assert client.media.add_calls == 0
        assert get_calls == 1
    finally:
        journal.close()


def test_eof_partial_token_that_never_becomes_full_stays_held(tmp_path):
    source, source_hash, journal, client = prepare_chunk_receipt(
        tmp_path, state=STATE_BYTES_SUBMITTING
    )
    source_size = source.stat().st_size
    client.uploadToken.uploaded_size = source_size
    client.uploadToken.get = lambda _token_id: KalturaUploadToken(
        id='token-existing',
        status=1,
        fileSize=source_size,
        uploadedFileSize=source_size,
    )
    client.uploadToken.waitForFullUpload = lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(
            KalturaOutcomeUnknown('last token status 1')
        )
    )
    try:
        with pytest.raises(
            UploadNeedsManualReconciliation,
            match='could not confirm final upload.*last token status 1',
        ):
            resume_upload_bytes_only(
                str(source), 'u0000001', client, journal, source_hash
            )
        receipt = journal.get(source_hash, 'u0000001')
        assert receipt.state == STATE_MANUAL_RECONCILE
        assert receipt.confirmed_bytes == source_size
        assert receipt.entry_id is None
        assert client.uploadToken.chunk_calls == []
        assert client.media.add_calls == 0
    finally:
        journal.close()


def test_resume_holds_on_populated_token_file_size_mismatch(tmp_path):
    source, source_hash, journal, client = prepare_chunk_receipt(
        tmp_path, state=STATE_BYTES_SUBMITTING
    )
    original_get = client.uploadToken.get

    def mismatched_size(upload_token_id):
        token = original_get(upload_token_id)
        token.fileSize = len(source.read_bytes()) + 1
        return token

    client.uploadToken.get = mismatched_size
    try:
        with pytest.raises(
            UploadNeedsManualReconciliation,
            match='unexpected fileSize',
        ):
            resume_upload_bytes_only(
                str(source),
                'u0000001',
                client,
                journal,
                source_hash,
            )
        assert client.uploadToken.chunk_calls == []
    finally:
        journal.close()


def test_ambiguous_chunk_response_uses_matching_token_progress(tmp_path, caplog):
    source, source_hash, journal, client = prepare_chunk_receipt(tmp_path)
    original_upload_chunk = client.uploadToken.uploadChunk
    failed_once = False

    def ambiguous_after_acceptance(*args):
        nonlocal failed_once
        token = original_upload_chunk(*args)
        if not failed_once:
            failed_once = True
            raise KalturaOutcomeUnknown(
                'upload file chunk did not return a response '
                '(exception chain ConnectionError -> RemoteDisconnected, '
                'elapsed 12.3s, request bytes 4, endpoint https://www.kaltura.com)'
            )
        return token

    client.uploadToken.uploadChunk = ambiguous_after_acceptance
    try:
        with caplog.at_level('WARNING'):
            receipt = _upload_bytes_in_chunks(
                str(source),
                'u0000001',
                client,
                journal,
                source_hash,
                chunk_size=4,
            )
        assert receipt.state == STATE_BYTES_UPLOADED
        assert client.uploadToken.chunk_calls == [
            (0, 4, False, False),
            (4, 4, True, False),
            (8, 2, True, True),
        ]
        assert 'ConnectionError -> RemoteDisconnected' in caplog.text
        assert 'elapsed 12.3s' in caplog.text
    finally:
        journal.close()


def test_ambiguous_chunk_without_progress_holds_same_token(tmp_path):
    source, source_hash, journal, client = prepare_chunk_receipt(tmp_path)

    def never_accepted(*_args):
        raise KalturaOutcomeUnknown(
            'upload file chunk did not return a response '
            '(exception chain ConnectionError, elapsed 180.0s, request bytes 4)'
        )

    client.uploadToken.uploadChunk = never_accepted
    try:
        with pytest.raises(
            UploadNeedsManualReconciliation,
            match='ConnectionError.*elapsed 180.0s.*no progress after 3 attempts',
        ):
            _upload_bytes_in_chunks(
                str(source),
                'u0000001',
                client,
                journal,
                source_hash,
                chunk_size=4,
            )
        receipt = journal.get(source_hash, 'u0000001')
        assert receipt.state == STATE_MANUAL_RECONCILE
        assert receipt.confirmed_bytes == 0
        assert receipt.upload_token_id == 'token-existing'
        assert receipt.entry_id is None
        assert receipt_allows_bytes_resume(receipt)
    finally:
        journal.close()


def test_bytes_resume_rejects_changed_source_hash(tmp_path):
    source, source_hash, journal, client = prepare_chunk_receipt(
        tmp_path, state=STATE_BYTES_SUBMITTING
    )
    source.write_bytes(b'changed bytes')
    try:
        with pytest.raises(UploadNeedsManualReconciliation, match='SHA-256'):
            resume_upload_bytes_only(
                str(source),
                'u0000001',
                client,
                journal,
                source_hash,
            )
        assert client.uploadToken.chunk_calls == []
    finally:
        journal.close()


def test_entry_stage_receipt_is_never_eligible_for_bytes_resume(tmp_path):
    source, source_hash, journal, client = prepare_chunk_receipt(
        tmp_path, state=STATE_ENTRY_CREATING
    )
    try:
        assert receipt_allows_bytes_resume(
            journal.get(source_hash, 'u0000001')
        ) is False
        with pytest.raises(UploadNeedsManualReconciliation, match='not eligible'):
            resume_upload_bytes_only(
                str(source),
                'u0000001',
                client,
                journal,
                source_hash,
            )
    finally:
        journal.close()


def test_existing_journal_schema_migrates_without_losing_receipt(tmp_path):
    path = tmp_path / 'legacy.sqlite3'
    connection = sqlite3.connect(path)
    connection.execute(
        '''
        CREATE TABLE upload_receipts (
            source_sha256 TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            state TEXT NOT NULL,
            upload_token_id TEXT,
            entry_id TEXT,
            detail TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (source_sha256, owner_id)
        )
        '''
    )
    connection.execute(
        'INSERT INTO upload_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        ('hash', 'owner', 'source.mp4', STATE_BYTES_SUBMITTING, 'token', None, None, 'now'),
    )
    connection.commit()
    connection.close()

    with UploadJournal(str(path)) as journal:
        receipt = journal.get('hash', 'owner')
        assert receipt.source_name == 'source.mp4'
        assert receipt.source_size is None
        assert receipt.confirmed_bytes == 0


def make_http_client():
    client = KalturaClient(KalturaConfiguration())
    client.sessionData = KalturaClient.SessionData({
        'ks': 'test-session',
        'partnerId': 1234567,
    })
    client.UPLOAD_STATUS_POLL_ATTEMPTS = 2
    client.UPLOAD_STATUS_POLL_INTERVAL = 0
    return client


class FakeResponse:
    def __init__(self, url, *, payload=None, status_code=200, content=b'{}', content_type='application/json'):
        self.url = url
        self.payload = payload
        self.status_code = status_code
        self.content = content
        self.headers = {'Content-Type': content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'HTTP {self.status_code}')

    def json(self):
        if self.payload is None:
            raise ValueError('not JSON')
        return self.payload


def test_non_json_2xx_upload_reconciles_full_token(monkeypatch):
    client = make_http_client()
    calls = []

    def fake_post(url, json=None, files=None, timeout=None):
        calls.append((url, files))
        if files:
            return FakeResponse(
                url,
                content=b'<html>accepted</html>',
                content_type='text/html',
            )
        return FakeResponse(
            url,
            payload={'id': 'token-1', 'status': 2},
        )

    monkeypatch.setattr('mock_kaltura_client.requests.post', fake_post)

    token = client.uploadToken.upload('token-1', io.BytesIO(b'test'), False, True, 0)

    assert token.status == 2
    assert len(calls) == 2
    assert calls[0][0].startswith(
        'https://www.kaltura.com/api_v3/service/uploadtoken/action/upload'
    )
    assert 'uploadtoken/action/get' in calls[1][0]


def test_non_json_2xx_upload_with_pending_token_stays_unknown(monkeypatch):
    client = make_http_client()

    def fake_post(url, json=None, files=None, timeout=None):
        if files:
            return FakeResponse(
                url,
                content=b'<html>accepted</html>',
                content_type='text/html',
            )
        return FakeResponse(
            url,
            payload={'id': 'token-1', 'status': 1},
        )

    monkeypatch.setattr('mock_kaltura_client.requests.post', fake_post)

    with pytest.raises(KalturaOutcomeUnknown, match='non-JSON.*last token status 1'):
        client.uploadToken.upload('token-1', io.BytesIO(b'test'), False, True, 0)


def test_upload_transport_error_reports_elapsed_and_safe_exception_chain(monkeypatch):
    client = make_http_client()
    times = iter((100.0, 112.5))

    monkeypatch.setattr('mock_kaltura_client.time.monotonic', lambda: next(times))
    monkeypatch.setattr(
        'mock_kaltura_client.requests.post',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            requests.ConnectionError('secret-bearing request failed')
        ),
    )

    with pytest.raises(KalturaOutcomeUnknown) as caught:
        client.post_upload(
            'https://www.kaltura.com/upload?ks=do-not-log',
            io.BytesIO(b'test'),
            request_bytes=4,
        )

    message = str(caught.value)
    assert 'ConnectionError' in message
    assert 'elapsed 12.5s' in message
    assert 'request bytes 4' in message
    assert 'https://www.kaltura.com/upload' in message
    assert 'do-not-log' not in message


def test_upload_chunk_rejects_different_returned_token(monkeypatch):
    client = make_http_client()
    monkeypatch.setattr(
        'mock_kaltura_client.requests.post',
        lambda url, **_kwargs: FakeResponse(
            url,
            payload={
                'id': 'different-token',
                'status': 1,
                'uploadedFileSize': 4,
            },
        ),
    )

    with pytest.raises(KalturaOutcomeUnknown, match='different token id'):
        client.uploadToken.uploadChunk(
            'token-1', io.BytesIO(b'test'), False, False, 0
        )


@pytest.mark.parametrize('invalid_size', ['NaN', 'Infinity', '-Infinity', -1, 1.5])
def test_uploaded_size_parser_rejects_invalid_numbers(invalid_size):
    token = KalturaUploadToken(uploadedFileSize=invalid_size)
    assert KalturaClient.UploadTokenService._uploaded_size(token) is None


def test_media_add_http_500_is_unknown(monkeypatch):
    client = make_http_client()

    monkeypatch.setattr(
        'mock_kaltura_client.requests.post',
        lambda *_args, **_kwargs: FakeResponse(
            'https://www.kaltura.com/api_v3/service/media/action/add',
            status_code=500,
            content=b'upstream failed',
            content_type='text/plain',
        ),
    )

    with pytest.raises(KalturaOutcomeUnknown, match='create media entry failed'):
        client.media.add(SimpleNamespace(toDict=lambda: {'name': 'test'}))


def test_add_content_http_500_is_unknown(monkeypatch):
    client = make_http_client()

    monkeypatch.setattr(
        'mock_kaltura_client.requests.post',
        lambda *_args, **_kwargs: FakeResponse(
            'https://www.kaltura.com/api_v3/service/media/action/addContent',
            status_code=500,
            content=b'upstream failed',
            content_type='text/plain',
        ),
    )

    with pytest.raises(KalturaOutcomeUnknown, match='attach uploaded content failed'):
        client.media.addContent(
            'entry-1',
            SimpleNamespace(toDict=lambda: {'token': 'token-1'}),
        )


@pytest.mark.parametrize(
    'payload',
    [{}, {'id': 'different-entry'}],
)
def test_add_content_unusable_success_response_is_unknown(monkeypatch, payload):
    client = make_http_client()

    monkeypatch.setattr(
        'mock_kaltura_client.requests.post',
        lambda *_args, **_kwargs: FakeResponse(
            'https://www.kaltura.com/api_v3/service/media/action/addContent',
            payload=payload,
        ),
    )

    with pytest.raises(KalturaOutcomeUnknown, match='no matching entry id'):
        client.media.addContent(
            'entry-1',
            SimpleNamespace(toDict=lambda: {'token': 'token-1'}),
        )


def test_media_lookup_uses_exact_name_and_owner(monkeypatch):
    client = make_http_client()
    requests_seen = []

    def fake_post(url, json=None, **_kwargs):
        requests_seen.append((url, json))
        return FakeResponse(
            url,
            payload={
                'objects': [
                    {
                        'id': 'entry-1',
                        'name': 'Recovery Test_Owner_09-10-26',
                        'userId': 'u00100001',
                        'status': 2,
                        'createdAt': 1789052400,
                        'duration': 3600,
                    }
                ]
            },
        )

    monkeypatch.setattr('mock_kaltura_client.requests.post', fake_post)

    entries = client.media.listByNameAndOwner(
        'Recovery Test_Owner_09-10-26', 'u00100001'
    )

    assert [entry.id for entry in entries] == ['entry-1']
    assert entries[0].userId == 'u00100001'
    assert entries[0].status == 2
    assert entries[0].createdAt == 1789052400
    assert entries[0].duration == 3600
    assert requests_seen[0][0].split('?', 1)[0].endswith('/service/media/action/list')
    assert requests_seen[0][1]['filter'] == {
        'objectType': 'KalturaMediaEntryFilter',
        'nameEqual': 'Recovery Test_Owner_09-10-26',
        'userIdEqual': 'u00100001',
    }
