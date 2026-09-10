from datetime import date, time
import io
from types import SimpleNamespace

import pytest
import requests

from data_types import Course, EventHost, LectureRecording
from kaltura_uploader import (
    UploadNeedsManualReconciliation,
    upload_video,
)
from mock_kaltura_client import (
    KalturaApiError,
    KalturaClient,
    KalturaConfiguration,
    KalturaOutcomeUnknown,
    KalturaUploadToken,
)
from upload_journal import STATE_ATTACHED, STATE_MANUAL_RECONCILE, UploadJournal, sha256_file


class FakeUploadTokenService:
    def __init__(self):
        self.add_calls = 0
        self.upload_calls = 0

    def add(self, _upload_token):
        self.add_calls += 1
        return KalturaUploadToken(id=f'token-{self.add_calls}', status=0)

    def upload(self, upload_token_id, _file_data, _resume, _final_chunk, _resume_at):
        self.upload_calls += 1
        return KalturaUploadToken(id=upload_token_id, status=2)

    def waitForFullUpload(self, upload_token_id):
        return KalturaUploadToken(id=upload_token_id, status=2)


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

    with pytest.raises(KalturaOutcomeUnknown, match='last token status 1'):
        client.uploadToken.upload('token-1', io.BytesIO(b'test'), False, True, 0)


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
