from dataclasses import dataclass
import io
import logging
from collections.abc import Callable

from data_types import *
from mock_kaltura_client import *
from upload_journal import (
    STATE_ATTACHED,
    STATE_ATTACHING,
    STATE_BYTES_SUBMITTING,
    STATE_BYTES_UPLOADED,
    STATE_ENTRY_CREATED,
    STATE_ENTRY_CREATING,
    STATE_MANUAL_RECONCILE,
    STATE_NEW,
    STATE_TOKEN_CREATED,
    UploadJournal,
    sha256_file,
)
import os
import hashlib


UPLOAD_CHUNK_BYTES = 10_240_000
MAX_CHUNK_ATTEMPTS_WITHOUT_PROGRESS = 3
BYTE_RECONCILE_PREFIX = 'byte-stage reconciliation required:'


class UploadNeedsManualReconciliation(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadResult:
    entry_id: str | None
    already_completed: bool = False

def get_kaltura_client() -> KalturaClient:
    '''
    Returns a "handle" for the KalturaClient, representing 
    one "conversation" with the Kaltura API.
    '''
    config = KalturaConfiguration()
    client = KalturaClient(config)

    widgetId = f"_{os.environ['PARTNER_ID']}"
    expiry = 14400

    result = client.session.startWidgetSession(widgetId, expiry)

    # Authenticate the session
    hashString = hashlib.sha256((result.ks + os.environ['TOKEN']).encode('ascii')).hexdigest() # Create a hash of the token, so we don't need to transmit the unencrypted token.
    result = client.appToken.startSession(
        id=os.environ['TOKEN_ID'],
        tokenHash=hashString,
    )

    return client


####################################### ABOUT AUTHENTICATION #######################################
# A hash is a one-way function, you input data and it returns a fixed length "signature" of that 
# data. The sha256 algorithm is a well known algorithm that is known to be very secure, in the 
# sense that it is mathematically nearly impossible to get the original string by starting with the 
# hash. Because of this, you can use it to prove that you actually do know the original input data 
# without exposing the original input data. To verify it, all the Kaltura server needs to do is 
# take the token they have on file, use the sha256 algorithm to create a hash of it, and verify 
# that the hash you sent matches the one they just generated.
#####################################################################################################


def _upload_owner(course: Course, instructor_index: int) -> EventHost:
    if instructor_index == -1:
        return course.get_first_host_alphabetically()
    return course.hosts[instructor_index]


def _media_entry(rec: LectureRecording, course: Course, kaltura_name: str, owner: EventHost):
    media_entry = KalturaMediaEntry()
    media_entry.name = kaltura_name
    media_entry.description = f'Class recording for {course.number} {course.name} on {rec.date.strftime("%d-%m-%Y")}'
    media_entry.mediaType = KalturaMediaType.VIDEO
    media_entry.userId = owner.unid
    return media_entry


def _legacy_upload_video(
    rec: LectureRecording,
    course: Course,
    kaltura_client: KalturaClient,
    kaltura_name: str,
    instructor_index: int,
) -> UploadResult:
    owner = _upload_owner(course, instructor_index)

    # File uploading
    ## Step 1: Get an upload token. This pre-assigns an identifer to the file we are about to upload.
    uploadToken = KalturaUploadToken()
    token = kaltura_client.uploadToken.add(uploadToken)

    ## Step 2: Upload the file using the upload token we just obtained.
    uploadTokenId = token.id
    resume = False
    finalChunk = True
    resumeAt = 0
    with open(rec.filepath, 'rb') as fileData:
        result = kaltura_client.uploadToken.upload(uploadTokenId, fileData, resume, finalChunk, resumeAt)

    ## Step 3: Create a media entry. This is the actual database record.
    mediaEntry = _media_entry(rec, course, kaltura_name, owner)
    entry = kaltura_client.media.add(mediaEntry)

    ## Step 4: Attach the video to the media entry using the upload token to refer to the video file.
    entry_id = entry.id
    resource = KalturaUploadedFileTokenResource()
    resource.token = uploadTokenId

    kaltura_client.media.addContent(entry_id, resource)
    return UploadResult(entry_id)


def _manual_reconcile(
    journal: UploadJournal,
    source_sha256: str,
    owner_id: str,
    detail: str,
    *,
    confirmed_bytes: int | None = None,
):
    journal.update(
        source_sha256,
        owner_id,
        STATE_MANUAL_RECONCILE,
        confirmed_bytes=confirmed_bytes,
        detail=detail,
    )
    raise UploadNeedsManualReconciliation(detail)


def _status_number(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _uploaded_size(token) -> int | None:
    return KalturaClient.UploadTokenService._uploaded_size(token)


def receipt_allows_bytes_resume(receipt) -> bool:
    """Return whether an explicit operator action may resume only file bytes."""
    if receipt.entry_id or not receipt.upload_token_id:
        return False
    if receipt.state == STATE_BYTES_SUBMITTING:
        return True
    if receipt.state != STATE_MANUAL_RECONCILE:
        return False
    detail = receipt.detail or ''
    return detail.startswith(BYTE_RECONCILE_PREFIX) or detail.startswith(
        'Upload byte outcome remains unknown'
    ) or detail.startswith('Upload bytes could not be reconciled')


def _authoritative_upload_position(
    kaltura_client,
    upload_token_id,
    source_size,
    *,
    allow_missing_as_zero=False,
):
    token = kaltura_client.uploadToken.get(upload_token_id)
    status = _status_number(token.status)
    uploaded_size = _uploaded_size(token)
    if uploaded_size is None and allow_missing_as_zero and status == 0:
        uploaded_size = 0
    elif uploaded_size is None:
        raise KalturaOutcomeUnknown(
            'Kaltura did not report uploadedFileSize for the recorded upload token'
        )
    if token.fileSize is not None:
        token_file_size = KalturaClient.UploadTokenService._nonnegative_integer(
            token.fileSize
        )
        if token_file_size is None or token_file_size != source_size:
            raise KalturaOutcomeUnknown(
                'The recorded upload token has an unexpected fileSize '
                f'(expected {source_size}, Kaltura reported {token.fileSize!r})'
            )
    if uploaded_size > source_size:
        raise KalturaOutcomeUnknown(
            f'Kaltura reported {uploaded_size} uploaded bytes for a '
            f'{source_size}-byte source file'
        )
    if status == 2 and uploaded_size != source_size:
        raise KalturaOutcomeUnknown(
            'Kaltura reported full-upload status with a mismatched byte count '
            f'(expected {source_size}, reported {uploaded_size})'
        )
    if status not in (0, 1, 2):
        raise KalturaApiError(
            f'The recorded upload token cannot be resumed (status {token.status!r})'
        )
    return token, status, uploaded_size


def _report_progress(
    source_name: str,
    owner_id: str,
    confirmed_bytes: int,
    source_size: int,
    progress: Callable[[int, int], None] | None,
):
    percent = 100 if source_size == 0 else int(confirmed_bytes * 100 / source_size)
    logging.info(
        'Kaltura upload progress: file=%s owner=%s confirmed_bytes=%d '
        'source_bytes=%d percent=%d',
        source_name,
        owner_id,
        confirmed_bytes,
        source_size,
        percent,
    )
    if progress is not None:
        progress(confirmed_bytes, source_size)


def _hold_byte_stage(
    journal,
    source_sha256,
    owner_id,
    detail,
    *,
    confirmed_bytes,
):
    _manual_reconcile(
        journal,
        source_sha256,
        owner_id,
        f'{BYTE_RECONCILE_PREFIX} {detail}',
        confirmed_bytes=confirmed_bytes,
    )


def _upload_bytes_in_chunks(
    file_path: str,
    owner_id: str,
    kaltura_client: KalturaClient,
    journal: UploadJournal,
    source_sha256: str,
    *,
    progress: Callable[[int, int], None] | None = None,
    chunk_size: int = UPLOAD_CHUNK_BYTES,
):
    if chunk_size <= 0:
        raise ValueError('chunk_size must be positive')
    receipt = journal.get(source_sha256, owner_id)
    if receipt is None or not receipt.upload_token_id:
        raise UploadNeedsManualReconciliation(
            'The upload journal has no recorded token for this source and owner.'
        )

    source_size = os.path.getsize(file_path)
    if receipt.source_size is not None and receipt.source_size != source_size:
        _hold_byte_stage(
            journal,
            source_sha256,
            owner_id,
            f'source size changed from {receipt.source_size} to {source_size} bytes',
            confirmed_bytes=receipt.confirmed_bytes,
        )

    try:
        _, status, offset = _authoritative_upload_position(
            kaltura_client,
            receipt.upload_token_id,
            source_size,
            allow_missing_as_zero=receipt.state == STATE_TOKEN_CREATED,
        )
    except KalturaApiError as error:
        _hold_byte_stage(
            journal,
            source_sha256,
            owner_id,
            f'could not establish the server upload position: {error}',
            confirmed_bytes=receipt.confirmed_bytes,
        )

    receipt = journal.update(
        source_sha256,
        owner_id,
        STATE_BYTES_SUBMITTING,
        source_size=source_size,
        confirmed_bytes=offset,
    )
    _report_progress(receipt.source_name, owner_id, offset, source_size, progress)

    if status == 2:
        return journal.update(
            source_sha256,
            owner_id,
            STATE_BYTES_UPLOADED,
            source_size=source_size,
            confirmed_bytes=source_size,
        )

    attempts_without_progress = 0
    last_status = status
    with open(file_path, 'rb') as source:
        while offset < source_size:
            source.seek(offset)
            chunk = source.read(min(chunk_size, source_size - offset))
            if not chunk:
                _hold_byte_stage(
                    journal,
                    source_sha256,
                    owner_id,
                    f'source ended unexpectedly at byte {offset}',
                    confirmed_bytes=offset,
                )
            expected_end = offset + len(chunk)
            upload_error = None
            try:
                response_token = kaltura_client.uploadToken.uploadChunk(
                    receipt.upload_token_id,
                    io.BytesIO(chunk),
                    offset > 0,
                    expected_end == source_size,
                    offset,
                )
                server_offset = _uploaded_size(response_token)
                response_status = _status_number(response_token.status)
                if server_offset is None or response_status not in (0, 1, 2):
                    _, response_status, server_offset = _authoritative_upload_position(
                        kaltura_client,
                        receipt.upload_token_id,
                        source_size,
                    )
            except KalturaApiError as error:
                upload_error = error
                try:
                    _, response_status, server_offset = _authoritative_upload_position(
                        kaltura_client,
                        receipt.upload_token_id,
                        source_size,
                    )
                except KalturaApiError as status_error:
                    _hold_byte_stage(
                        journal,
                        source_sha256,
                        owner_id,
                        f'{error}; follow-up token check failed: {status_error}',
                        confirmed_bytes=offset,
                    )

            if server_offset < offset or server_offset > expected_end:
                _hold_byte_stage(
                    journal,
                    source_sha256,
                    owner_id,
                    'Kaltura returned an unexpected chunk position '
                    f'(sent {offset}..{expected_end}, reported {server_offset})',
                    confirmed_bytes=offset,
                )
            if response_status == 2 and server_offset != source_size:
                _hold_byte_stage(
                    journal,
                    source_sha256,
                    owner_id,
                    'Kaltura finalized before the expected source size '
                    f'(expected {source_size}, reported {server_offset})',
                    confirmed_bytes=offset,
                )
            if server_offset == offset:
                attempts_without_progress += 1
                if attempts_without_progress >= MAX_CHUNK_ATTEMPTS_WITHOUT_PROGRESS:
                    reason = (
                        str(upload_error)
                        if upload_error is not None
                        else 'Kaltura accepted the request but reported no byte progress'
                    )
                    _hold_byte_stage(
                        journal,
                        source_sha256,
                        owner_id,
                        f'{reason}; no progress after {attempts_without_progress} attempts '
                        f'at byte {offset}',
                        confirmed_bytes=offset,
                    )
                continue

            if upload_error is not None:
                logging.warning(
                    'Kaltura chunk response failed, but the recorded token '
                    'confirmed progress from byte %d to %d: %s',
                    offset,
                    server_offset,
                    upload_error,
                )
            attempts_without_progress = 0
            offset = server_offset
            last_status = response_status
            receipt = journal.update(
                source_sha256,
                owner_id,
                STATE_BYTES_SUBMITTING,
                source_size=source_size,
                confirmed_bytes=offset,
            )
            _report_progress(receipt.source_name, owner_id, offset, source_size, progress)

    if last_status == 2:
        receipt = journal.update(
            source_sha256,
            owner_id,
            STATE_BYTES_UPLOADED,
            source_size=source_size,
            confirmed_bytes=source_size,
        )
        _report_progress(
            receipt.source_name,
            owner_id,
            source_size,
            source_size,
            progress,
        )
        return receipt

    final_error = KalturaOutcomeUnknown(
        'Kaltura did not return full-upload status after the final data chunk'
    )
    try:
        token = kaltura_client.uploadToken.waitForFullUpload(
            receipt.upload_token_id,
            expected_size=source_size,
        )
        if _status_number(token.status) == 2:
            receipt = journal.update(
                source_sha256,
                owner_id,
                STATE_BYTES_UPLOADED,
                source_size=source_size,
                confirmed_bytes=source_size,
            )
            _report_progress(
                receipt.source_name,
                owner_id,
                source_size,
                source_size,
                progress,
            )
            return receipt
    except KalturaApiError as error:
        final_error = error

    _hold_byte_stage(
        journal,
        source_sha256,
        owner_id,
        f'could not confirm final upload: {final_error}',
        confirmed_bytes=source_size,
    )


def resume_upload_bytes_only(
    file_path: str,
    owner_id: str,
    kaltura_client: KalturaClient,
    journal: UploadJournal,
    source_sha256: str,
    *,
    progress: Callable[[int, int], None] | None = None,
):
    """Explicitly resume one byte-stage receipt without creating a media entry."""
    receipt = journal.get(source_sha256, owner_id)
    if receipt is None or not receipt_allows_bytes_resume(receipt):
        raise UploadNeedsManualReconciliation(
            'This receipt is not eligible for a bytes-only resume.'
        )
    if sha256_file(file_path) != source_sha256:
        raise UploadNeedsManualReconciliation(
            'The selected source file no longer matches the journaled SHA-256.'
        )
    return _upload_bytes_in_chunks(
        file_path,
        owner_id,
        kaltura_client,
        journal,
        source_sha256,
        progress=progress,
    )


def upload_video(
    rec: LectureRecording,
    course: Course,
    kaltura_client: KalturaClient,
    kaltura_name,
    instructorIndex: int = -1,
    *,
    journal: UploadJournal | None = None,
    source_sha256: str | None = None,
) -> UploadResult:
    """Upload one recording for one owner, resuming only confirmed stages.

    Callers that omit ``journal`` keep the original public API. Production
    callers should pass one durable journal and a hash computed once per source
    recording.
    """
    if journal is None:
        return _legacy_upload_video(
            rec,
            course,
            kaltura_client,
            kaltura_name,
            instructorIndex,
        )

    owner = _upload_owner(course, instructorIndex)
    source_sha256 = source_sha256 or sha256_file(rec.filepath)
    receipt = journal.get_or_create(
        source_sha256,
        owner.unid,
        os.path.basename(rec.filepath),
        os.path.getsize(rec.filepath),
    )

    if receipt.state == STATE_ATTACHED:
        return UploadResult(receipt.entry_id, already_completed=True)
    if receipt.state == STATE_MANUAL_RECONCILE:
        raise UploadNeedsManualReconciliation(
            receipt.detail or 'A previous Kaltura operation requires manual reconciliation.'
        )

    if receipt.state == STATE_BYTES_SUBMITTING:
        confirmed_bytes = receipt.confirmed_bytes
        try:
            _, status, confirmed_bytes = _authoritative_upload_position(
                kaltura_client,
                receipt.upload_token_id,
                os.path.getsize(rec.filepath),
            )
        except KalturaApiError as error:
            _manual_reconcile(
                journal,
                source_sha256,
                owner.unid,
                f'{BYTE_RECONCILE_PREFIX} interrupted upload could not be '
                f'reconciled from the recorded token: {error}',
                confirmed_bytes=confirmed_bytes,
            )
        if status == 2:
            receipt = journal.update(
                source_sha256,
                owner.unid,
                STATE_BYTES_UPLOADED,
                confirmed_bytes=confirmed_bytes,
            )
        else:
            _manual_reconcile(
                journal,
                source_sha256,
                owner.unid,
                f'{BYTE_RECONCILE_PREFIX} interrupted upload is at byte '
                f'{confirmed_bytes}; use the explicit bytes-only resume command',
                confirmed_bytes=confirmed_bytes,
            )

    if receipt.state == STATE_ENTRY_CREATING:
        _manual_reconcile(
            journal,
            source_sha256,
            owner.unid,
            'Kaltura may have created the media entry without returning its id.',
        )
    if receipt.state == STATE_ATTACHING:
        _manual_reconcile(
            journal,
            source_sha256,
            owner.unid,
            'Kaltura may have attached content to the recorded entry; verify it before retrying.',
        )

    if receipt.state == STATE_NEW:
        token = kaltura_client.uploadToken.add(KalturaUploadToken(
            fileName=os.path.basename(rec.filepath),
            fileSize=os.path.getsize(rec.filepath),
            autoFinalize=False,
        ))
        receipt = journal.update(
            source_sha256,
            owner.unid,
            STATE_TOKEN_CREATED,
            upload_token_id=token.id,
        )

    if receipt.state == STATE_TOKEN_CREATED:
        if not receipt.upload_token_id:
            _manual_reconcile(
                journal,
                source_sha256,
                owner.unid,
                'The upload journal has no token id for a token-created receipt.',
            )
        receipt = _upload_bytes_in_chunks(
            rec.filepath,
            owner.unid,
            kaltura_client,
            journal,
            source_sha256,
        )

    if receipt.state == STATE_BYTES_UPLOADED:
        journal.update(
            source_sha256,
            owner.unid,
            STATE_ENTRY_CREATING,
        )
        try:
            entry = kaltura_client.media.add(
                _media_entry(rec, course, kaltura_name, owner)
            )
        except KalturaOutcomeUnknown as error:
            _manual_reconcile(
                journal,
                source_sha256,
                owner.unid,
                f'Kaltura media entry outcome is unknown: {error}',
            )
        except KalturaApiError:
            journal.update(
                source_sha256,
                owner.unid,
                STATE_BYTES_UPLOADED,
            )
            raise
        receipt = journal.update(
            source_sha256,
            owner.unid,
            STATE_ENTRY_CREATED,
            entry_id=entry.id,
        )

    if receipt.state == STATE_ENTRY_CREATED:
        if not receipt.entry_id or not receipt.upload_token_id:
            _manual_reconcile(
                journal,
                source_sha256,
                owner.unid,
                'The upload journal lacks the entry or token id needed to attach content.',
            )
        resource = KalturaUploadedFileTokenResource()
        resource.token = receipt.upload_token_id
        journal.update(
            source_sha256,
            owner.unid,
            STATE_ATTACHING,
        )
        try:
            kaltura_client.media.addContent(receipt.entry_id, resource)
        except KalturaOutcomeUnknown as error:
            _manual_reconcile(
                journal,
                source_sha256,
                owner.unid,
                f'Kaltura content attachment outcome is unknown: {error}',
            )
        except KalturaApiError:
            journal.update(
                source_sha256,
                owner.unid,
                STATE_ENTRY_CREATED,
            )
            raise
        receipt = journal.update(
            source_sha256,
            owner.unid,
            STATE_ATTACHED,
        )

    return UploadResult(receipt.entry_id)
