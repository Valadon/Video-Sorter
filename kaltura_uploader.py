from dataclasses import dataclass

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
):
    journal.update(
        source_sha256,
        owner_id,
        STATE_MANUAL_RECONCILE,
        detail=detail,
    )
    raise UploadNeedsManualReconciliation(detail)


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
    )

    if receipt.state == STATE_ATTACHED:
        return UploadResult(receipt.entry_id, already_completed=True)
    if receipt.state == STATE_MANUAL_RECONCILE:
        raise UploadNeedsManualReconciliation(
            receipt.detail or 'A previous Kaltura operation requires manual reconciliation.'
        )

    if receipt.state == STATE_BYTES_SUBMITTING:
        if not receipt.upload_token_id:
            _manual_reconcile(
                journal,
                source_sha256,
                owner.unid,
                'Upload bytes may have been submitted, but no upload token was recorded.',
            )
        try:
            kaltura_client.uploadToken.waitForFullUpload(receipt.upload_token_id)
        except KalturaApiError as error:
            _manual_reconcile(
                journal,
                source_sha256,
                owner.unid,
                f'Upload bytes could not be reconciled from the recorded token: {error}',
            )
        receipt = journal.update(
            source_sha256,
            owner.unid,
            STATE_BYTES_UPLOADED,
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
        token = kaltura_client.uploadToken.add(KalturaUploadToken())
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
        journal.update(
            source_sha256,
            owner.unid,
            STATE_BYTES_SUBMITTING,
        )
        try:
            with open(rec.filepath, 'rb') as file_data:
                kaltura_client.uploadToken.upload(
                    receipt.upload_token_id,
                    file_data,
                    False,
                    True,
                    0,
                )
        except KalturaOutcomeUnknown as error:
            _manual_reconcile(
                journal,
                source_sha256,
                owner.unid,
                f'Upload byte outcome remains unknown after token reconciliation: {error}',
            )
        except KalturaApiError:
            journal.update(
                source_sha256,
                owner.unid,
                STATE_TOKEN_CREATED,
            )
            raise
        receipt = journal.update(
            source_sha256,
            owner.unid,
            STATE_BYTES_UPLOADED,
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
