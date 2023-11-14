# The existing Kaltura Python library doesn't quite seem to work correctly, 
# so in the meantime I developed a very simple mock of that client that does 
# what we need it to do. Ideally, if the client library is fixed it won't 
# take much to migrate scripts to use it.

import requests

class KalturaConfiguration:
    pass

class KalturaUploadToken:
    def __init__(self, id=None):
        self.id = id

    @staticmethod
    def fromJsonResponse(res):
        return KalturaUploadToken(id=res['id'])

class KalturaServiceBase:
    def __init__(self, client):
        self.client: KalturaClient = client
    
class KalturaMediaEntry:
    def __init__(self):
        self.name = None
        self.description = None
        self.mediaType = None
        self.id = None

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
    
    def getRequestData(self, data={}) -> dict:
            return {
                'ks': self.sessionData.ks,
                'partnerId': self.sessionData.partnerId,
                **data
            }
    
    class SessionService(KalturaServiceBase):
        def startWidgetSession(self, widgetId: str, expiry: int):
            res = requests.post(KalturaClient.kurl('session/action/startWidgetSession'), json={
                'expiry': expiry,
                'widgetId': widgetId
            }).json()
            self.client.sessionData = KalturaClient.SessionData(res)
            return self.client.sessionData

    class AppTokenService(KalturaServiceBase):
        def startSession (self, id, tokenHash):
            res = requests.post(KalturaClient.kurl('apptoken/action/startSession'), json=self.client.getRequestData({
                'id': id,
                'tokenHash': tokenHash
            })).json()
            self.client.sessionData = KalturaClient.SessionData(res)
            return self.client.sessionData
        
    class UploadTokenService(KalturaServiceBase):
        def add (self, uploadToken):
            res = requests.post(KalturaClient.kurl('uploadtoken/action/add'), json=self.client.getRequestData()).json()
            return KalturaUploadToken(id=res['id'])
        
        def upload (self, uploadTokenId, fileData, resume, finalChunk, resumeAt):
            url = KalturaClient.kurl(
                'uploadtoken/action/upload', 
                uploadTokenId=uploadTokenId, 
                resume='true' if resume else 'false',
                finalChunk='true' if finalChunk else 'false',
                resumeAt=resumeAt,
                ks=self.client.sessionData.ks,
                partnerId=self.client.sessionData.partnerId
            )
            res = requests.post(url, files={
                'fileData': fileData
            }).json()
            return {}
        
    class MediaService(KalturaServiceBase):
        def add (self, mediaEntry: KalturaMediaEntry):
            res = requests.post(KalturaClient.kurl('media/action/add'), json=self.client.getRequestData({
                'entry': mediaEntry.toDict()
            })).json()
            return KalturaMediaEntry.fromJsonResponse(res)
        
        def addContent(self, entry_id, resource):
            res = requests.post(KalturaClient.kurl('media/action/addContent'), json=self.client.getRequestData({
                'entryId': entry_id,
                'resource': resource.toDict()
            })).json()
            return {}
        
    class UserService(KalturaServiceBase):
        def getByLoginId(self, loginId) -> KalturaUser:
            res = requests.post(KalturaClient.kurl('user/action/getByLoginId'), json=self.client.getRequestData({
                'loginId': loginId
            })).json()
            print(res)
            return KalturaUser.fromJsonResponse(res)
        
        def get(self, userId) -> KalturaUser:
            res = requests.post(KalturaClient.kurl('user/action/get'), json=self.client.getRequestData({
                'userId': userId
            })).json()
            print(res)
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