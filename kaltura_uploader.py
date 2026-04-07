from dotenv import load_dotenv
load_dotenv()
from data_types import *
from mock_kaltura_client import *
import os
import hashlib

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


def upload_video (rec: LectureRecording, course: Course, kaltura_client: KalturaClient, kaltura_name, instructorIndex: int = -1):
    # File uploading
    ## Step 1: Get an upload token. This pre-assigns an identifer to the file we are about to upload.
    uploadToken = KalturaUploadToken()
    token = kaltura_client.uploadToken.add(uploadToken)

    ## Step 2: Upload the file using the upload token we just obtained.
    uploadTokenId = token.id
    fileData = open(rec.filepath, 'rb')
    resume = False
    finalChunk = True
    resumeAt = 0
    result = kaltura_client.uploadToken.upload(uploadTokenId, fileData, resume, finalChunk, resumeAt)

    ## Step 3: Create a media entry. This is the actual database record.
    mediaEntry = KalturaMediaEntry()
    mediaEntry.name = kaltura_name
    mediaEntry.description = f'Class recording for {course.number} {course.name} on {rec.date.strftime("%d-%m-%Y")}'
    mediaEntry.mediaType = KalturaMediaType.VIDEO
    if instructorIndex == -1:
        mediaEntry.userId = course.get_first_host_alphabetically().unid
    else:
        mediaEntry.userId = course.hosts[instructorIndex].unid
    entry = kaltura_client.media.add(mediaEntry)

    ## Step 4: Attach the video to the media entry using the upload token to refer to the video file.
    entry_id = entry.id
    resource = KalturaUploadedFileTokenResource()
    resource.token = uploadTokenId

    result = kaltura_client.media.addContent(entry_id, resource)
