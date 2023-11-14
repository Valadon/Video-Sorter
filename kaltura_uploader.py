from dotenv import load_dotenv
load_dotenv()
from data_types import *
from mock_kaltura_client import *
import os
import hashlib

def get_kaltura_client() -> KalturaClient:
    config = KalturaConfiguration()
    client = KalturaClient(config)

    widgetId = f"_{os.environ['PARTNER_ID']}"
    expiry = 14400

    result = client.session.startWidgetSession(widgetId, expiry)
    hashString = hashlib.sha256((result.ks + os.environ['TOKEN']).encode('ascii')).hexdigest()
    result = client.appToken.startSession(
        id=os.environ['TOKEN_ID'],
        tokenHash=hashString,
    )

    return client

def upload_video (rec: Recording, course: Course, kalturaClient: KalturaClient):
    # File uploading
    ## Step 1: Get an upload token
    uploadToken = KalturaUploadToken()
    token = kalturaClient.uploadToken.add(uploadToken)

    ## Step 2: Upload the file
    uploadTokenId = token.id
    fileData = open(rec.filepath, 'rb')
    resume = False
    finalChunk = True
    resumeAt = 0
    result = kalturaClient.uploadToken.upload(uploadTokenId, fileData, resume, finalChunk, resumeAt)

    ## Step 3: Create a media entry
    mediaEntry = KalturaMediaEntry()
    mediaEntry.name = rec.filename
    mediaEntry.description = f'Class recording for {course.number} {course.name} on {rec.date.strftime("%d-%m-%Y")}'
    mediaEntry.mediaType = KalturaMediaType.VIDEO
    mediaEntry.userId = course.get_first_instructor_alphabetically().unid
    entry = kalturaClient.media.add(mediaEntry)

    ## Step 4: Attach the video
    entry_id = entry.id
    resource = KalturaUploadedFileTokenResource()
    resource.token = uploadTokenId

    result = kalturaClient.media.addContent(entry_id, resource)
