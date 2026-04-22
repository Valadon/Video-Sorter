'''
The existing Kaltura Python library doesn't quite seem to work correctly, 
so in the meantime I developed a very simple mock of that client that does 
what we need it to do. Ideally, if the client library is fixed it won't 
take much to migrate scripts to use it.
'''

import requests

class KalturaApiError(RuntimeError):
    pass

class KalturaConfiguration:
    pass

class KalturaUploadToken:
    def __init__(self, id=None, uploadUrl=None, status=None):
        self.id = id
        self.uploadUrl = uploadUrl
        self.status = status

    @staticmethod
    def fromJsonResponse(res):
        return KalturaUploadToken(
            id=res['id'],
            uploadUrl=res.get('uploadUrl', None),
            status=res.get('status', None),
        )

class KalturaServiceBase:
    def __init__(self, client):
        self.client: KalturaClient = client
    
class KalturaMediaEntry:
    def __init__(self):
        self.name = None
        self.description = None
        self.mediaType = None
        self.id = None
        self.userId = None

    @staticmethod
    def fromJsonResponse(res):
        mediaEntry = KalturaMediaEntry()
        mediaEntry.name = res['name']
        mediaEntry.description = res['description']
        mediaEntry.mediaType = res['mediaType']
        mediaEntry.id = res['id']
        return mediaEntry

    def toDict (self):
        result = {
            'name': self.name,
            'description': self.description,
            'mediaType': self.mediaType,
        }
        if self.userId:
            result['userId'] = self.userId
        return result
    
class KalturaUploadedFileTokenResource:
    def __init__(self):
        self.token = None
        self.objectType = 'KalturaUploadedFileTokenResource'
    
    def toDict(self):
        return {
            'objectType': self.objectType,
            'token': self.token,
        }

class KalturaMediaType:
    VIDEO = 1
    IMAGE = 2
    AUDIO = 5

class KalturaUser:
    def __init__(self) -> None:
        self.loginId = None
        self.id = None

    @staticmethod
    def fromJsonResponse(res):
        user = KalturaUser()
        user.loginId = res.get('loginId', None)
        user.id = res['id']
        return user

class KalturaClient:
    @staticmethod
    def kurl (path, **kwargs):
        query = '?format=1'
        for key, value in kwargs.items():
            query += f'&{key}={value}'
        url = f'https://www.kaltura.com/api_v3/service/{path}{query}'
        return url
    
    @staticmethod
    def _parse_response(response):
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as exc:
            raise KalturaApiError("Kaltura returned a non-JSON response") from exc

        if isinstance(payload, dict) and payload.get('objectType') == 'KalturaAPIException':
            code = payload.get('code', 'UNKNOWN')
            message = payload.get('message', 'Unknown Kaltura API error')
            raise KalturaApiError(f"{code}: {message}")

        return payload

    def post_json(self, path: str, data=None, **kwargs):
        response = requests.post(self.kurl(path, **kwargs), json=data)
        return self._parse_response(response)

    def post_upload(self, url: str, fileData):
        response = requests.post(url, files={
            'fileData': fileData
        })
        return self._parse_response(response)

    def getRequestData(self, data=None) -> dict:
            if data is None:
                data = {}
            return {
                'ks': self.sessionData.ks,
                'partnerId': self.sessionData.partnerId,
                **data
            }
    
    class SessionService(KalturaServiceBase):
        def startWidgetSession(self, widgetId: str, expiry: int):
            res = self.client.post_json('session/action/startWidgetSession', {
                'expiry': expiry,
                'widgetId': widgetId
            })
            self.client.sessionData = KalturaClient.SessionData(res)
            return self.client.sessionData

    class AppTokenService(KalturaServiceBase):
        def startSession (self, id, tokenHash):
            res = self.client.post_json('apptoken/action/startSession', self.client.getRequestData({
                'id': id,
                'tokenHash': tokenHash
            }))
            self.client.sessionData = KalturaClient.SessionData(res)
            return self.client.sessionData
        
    class UploadTokenService(KalturaServiceBase):
        def add (self, uploadToken):
            res = self.client.post_json('uploadtoken/action/add', self.client.getRequestData())
            token = KalturaUploadToken.fromJsonResponse(res)
            if token.uploadUrl:
                self.client.upload_urls[token.id] = token.uploadUrl
            return token
        
        def upload (self, uploadTokenId, fileData, resume, finalChunk, resumeAt):
            query = (
                f'uploadTokenId={uploadTokenId}'
                f'&resume={"true" if resume else "false"}'
                f'&finalChunk={"true" if finalChunk else "false"}'
                f'&resumeAt={resumeAt}'
                f'&ks={self.client.sessionData.ks}'
                f'&partnerId={self.client.sessionData.partnerId}'
            )

            upload_url = self.client.upload_urls.get(uploadTokenId, None)
            if upload_url:
                separator = '&' if '?' in upload_url else '?'
                url = f'{upload_url}{separator}{query}'
            else:
                url = KalturaClient.kurl('uploadtoken/action/upload', **{
                    'uploadTokenId': uploadTokenId,
                    'resume': 'true' if resume else 'false',
                    'finalChunk': 'true' if finalChunk else 'false',
                    'resumeAt': resumeAt,
                    'ks': self.client.sessionData.ks,
                    'partnerId': self.client.sessionData.partnerId,
                })

            res = self.client.post_upload(url, fileData)
            return KalturaUploadToken.fromJsonResponse(res)
        
    class MediaService(KalturaServiceBase):
        def add (self, mediaEntry: KalturaMediaEntry):
            res = self.client.post_json('media/action/add', self.client.getRequestData({
                'entry': mediaEntry.toDict()
            }))
            return KalturaMediaEntry.fromJsonResponse(res)
        
        def addContent(self, entry_id, resource):
            res = self.client.post_json('media/action/addContent', self.client.getRequestData({
                'entryId': entry_id,
                'resource': resource.toDict()
            }))
            return res
        
    class UserService(KalturaServiceBase):
        def getByLoginId(self, loginId) -> KalturaUser:
            res = self.client.post_json('user/action/getByLoginId', self.client.getRequestData({
                'loginId': loginId
            }))
            return KalturaUser.fromJsonResponse(res)
        
        def get(self, userId) -> KalturaUser:
            res = self.client.post_json('user/action/get', self.client.getRequestData({
                'userId': userId
            }))
            return KalturaUser.fromJsonResponse(res)
    
    class SessionData:
            def __init__(self, jsonResponse):
                self.ks = jsonResponse['ks']
                self.partnerId = jsonResponse['partnerId']
                self.userId = jsonResponse.get('userId', None)

    def __init__(self, config):
         self.session = self.SessionService(self)
         self.appToken = self.AppTokenService(self)
         self.uploadToken = self.UploadTokenService(self)
         self.media = self.MediaService(self)
         self.user = self.UserService(self)
         self.config = config
         self.sessionData = None
         self.upload_urls: dict[str, str] = {}
