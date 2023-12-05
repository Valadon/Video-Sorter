from dotenv import load_dotenv
load_dotenv()
from data_types import *
from mock_kaltura_client import *
import os
import hashlib
import logging

def read_in_chunks(file_object, chunk_size=65536):
    while True:
        data = file_object.read(chunk_size)
        if not data:
            break
        yield data

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

def upload_video (filepath: str, kaltura_client: KalturaClient, kaltura_name: str, kaltura_desc:str, user: str, chunk_size: int, ):
    # File uploading
    ## Step 1: Get an upload token
    uploadToken = KalturaUploadToken()
    token = kaltura_client.uploadToken.add(uploadToken)

    ## Step 2: Upload the file
    num_bytes = os.stat(filepath).st_size
    uploadTokenId = token.id
    with open(filepath) as fileData:
        finalChunkIndex = int(float(num_bytes) / float(chunk_size)) * chunk_size
        bytesRead = 0

        logging.info(f'Starting an upload of size: {num_bytes} in chunks of size {chunk_size} bytes')
        for chunk in read_in_chunks(fileData, chunk_size=chunk_size):
            resume = (bytesRead != 0)
            finalChunk = (bytesRead == finalChunkIndex)
            resumeAt = bytesRead
            bytesRead += len(chunk)
            chunkData = (filepath, chunk)
            kaltura_client.uploadToken.upload(uploadTokenId, chunkData, resume, finalChunk, resumeAt)
            logging.info(f'Upload progress: {int((bytesRead / num_bytes) * 100)}%')

    ## Step 3: Create a media entry
    mediaEntry = KalturaMediaEntry()
    mediaEntry.name = kaltura_name
    mediaEntry.description = kaltura_desc
    mediaEntry.mediaType = KalturaMediaType.VIDEO
    mediaEntry.userId = user
    entry = kaltura_client.media.add(mediaEntry)

    ## Step 4: Attach the video
    entry_id = entry.id
    resource = KalturaUploadedFileTokenResource()
    resource.token = uploadTokenId

    kaltura_client.media.addContent(entry_id, resource)

def upload_lecture_video (rec: LectureRecording, course: Course, kaltura_client: KalturaClient, chunk_size: int, kaltura_name, instructorIndex: int = -1):
    if instructorIndex == -1:
        user = course.get_first_host_alphabetically().unid
    else:
        user = course.hosts[instructorIndex].unid

    desc = f'Class recording for {course.course_number_full} {course.name} on {rec.date.strftime("%d-%m-%Y")}'

    upload_video(
        filepath=rec.filepath, 
        kaltura_client=kaltura_client, 
        kaltura_name=kaltura_name, 
        kaltura_desc=desc,
        user=user,
        chunk_size=chunk_size
    )