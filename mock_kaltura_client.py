'''
The existing Kaltura Python library doesn't quite seem to work correctly, 
so in the meantime I developed a very simple mock of that client that does 
what we need it to do. Ideally, if the client library is fixed it won't 
take much to migrate scripts to use it.
'''

import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

class KalturaApiError(RuntimeError):
    pass

class KalturaOutcomeUnknown(KalturaApiError):
    """The request may have reached Kaltura, but no usable receipt was returned."""

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
        self.status = None
        self.createdAt = None
        self.duration = None

    @staticmethod
    def fromJsonResponse(res):
        mediaEntry = KalturaMediaEntry()
        mediaEntry.name = res.get('name')
        mediaEntry.description = res.get('description')
        mediaEntry.mediaType = res.get('mediaType')
        mediaEntry.id = res['id']
        mediaEntry.userId = res.get('userId')
        mediaEntry.status = res.get('status')
        mediaEntry.createdAt = res.get('createdAt')
        mediaEntry.duration = res.get('duration')
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
    JSON_TIMEOUT = (15, 60)
    # The production Kaltura upload route can spend about a minute negotiating
    # before returning a response for a large classroom recording. Keep this
    # bounded, but well above the proven 63-second production upload time.
    UPLOAD_TIMEOUT = (180, 900)
    UPLOAD_STATUS_POLL_ATTEMPTS = 15
    UPLOAD_STATUS_POLL_INTERVAL = 2

    @staticmethod
    def kurl (path, **kwargs):
        return KalturaClient._with_query_params(
            f'https://www.kaltura.com/api_v3/service/{path}',
            {'format': 1, **kwargs},
        )

    @staticmethod
    def _with_query_params(url: str, params: dict) -> str:
        """Merge encoded parameters into a Kaltura URL without duplicating keys."""
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.update({key: str(value) for key, value in params.items()})
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    @staticmethod
    def _upload_action_url(_upload_url: str) -> str:
        """Use the primary upload action endpoint used by the proven legacy client.

        Kaltura can return a data-center upload host such as my-upload.kaltura.com,
        but that host acknowledged production uploads without advancing the token
        from PENDING. The earlier working sorter deliberately ignored uploadUrl and
        posted to the primary API action endpoint instead.
        """
        return 'https://www.kaltura.com/api_v3/service/uploadtoken/action/upload'

    @staticmethod
    def _safe_endpoint(url: str) -> str:
        endpoint_parts = urlsplit(url or '')
        endpoint = urlunsplit((endpoint_parts.scheme, endpoint_parts.netloc, endpoint_parts.path, '', ''))
        return endpoint or 'unknown endpoint'

    @staticmethod
    def _response_details(response) -> str:
        status = getattr(response, 'status_code', 'unknown')
        headers = getattr(response, 'headers', {}) or {}
        content_type = headers.get('Content-Type', 'unknown')
        endpoint = KalturaClient._safe_endpoint(getattr(response, 'url', '') or '')

        content = getattr(response, 'content', b'')
        try:
            response_bytes = len(content)
        except TypeError:
            response_bytes = 'unknown'

        return (
            f'HTTP {status}, content-type {content_type}, '
            f'endpoint {endpoint}, response bytes {response_bytes}'
        )
    
    @staticmethod
    def _parse_response(response, operation='Kaltura API request'):
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            status = getattr(response, 'status_code', None)
            error_type = (
                KalturaOutcomeUnknown
                if isinstance(status, int) and (status >= 500 or status in (408, 425))
                else KalturaApiError
            )
            raise error_type(
                f'{operation} failed ({KalturaClient._response_details(response)})'
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise KalturaOutcomeUnknown(
                f'{operation} failed: Kaltura returned a non-JSON response '
                f'({KalturaClient._response_details(response)})'
            ) from exc

        if isinstance(payload, dict) and payload.get('objectType') == 'KalturaAPIException':
            code = payload.get('code', 'UNKNOWN')
            message = payload.get('message', 'Unknown Kaltura API error')
            raise KalturaApiError(f'{operation} failed: {code}: {message}')

        return payload

    def post_json(self, path: str, data=None, operation=None, **kwargs):
        operation = operation or path
        url = self.kurl(path, **kwargs)
        try:
            response = requests.post(url, json=data, timeout=self.JSON_TIMEOUT)
        except requests.RequestException as exc:
            raise KalturaOutcomeUnknown(
                f'{operation} did not return a response '
                f'({type(exc).__name__}, endpoint {self._safe_endpoint(url)})'
            ) from exc
        return self._parse_response(response, operation)

    def post_upload(self, url: str, fileData, operation='upload file bytes'):
        try:
            response = requests.post(
                url,
                files={'fileData': fileData},
                timeout=self.UPLOAD_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise KalturaOutcomeUnknown(
                f'{operation} did not return a response '
                f'({type(exc).__name__}, endpoint {self._safe_endpoint(url)})'
            ) from exc

        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            status = getattr(response, 'status_code', None)
            error_type = (
                KalturaOutcomeUnknown
                if isinstance(status, int) and (status >= 500 or status in (408, 425))
                else KalturaApiError
            )
            raise error_type(
                f'{operation} failed ({self._response_details(response)})'
            ) from exc

        if not getattr(response, 'content', b''):
            return None

        return self._parse_response(response, operation)

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
            }, operation='start widget session')
            self.client.sessionData = KalturaClient.SessionData(res)
            return self.client.sessionData

    class AppTokenService(KalturaServiceBase):
        def startSession (self, id, tokenHash):
            res = self.client.post_json('apptoken/action/startSession', self.client.getRequestData({
                'id': id,
                'tokenHash': tokenHash
            }), operation='start app-token session')
            self.client.sessionData = KalturaClient.SessionData(res)
            return self.client.sessionData
        
    class UploadTokenService(KalturaServiceBase):
        def add (self, uploadToken):
            res = self.client.post_json(
                'uploadtoken/action/add',
                self.client.getRequestData(),
                operation='create upload token',
            )
            token = KalturaUploadToken.fromJsonResponse(res)
            if token.uploadUrl:
                self.client.upload_urls[token.id] = token.uploadUrl
            return token

        def get(self, uploadTokenId):
            res = self.client.post_json(
                'uploadtoken/action/get',
                self.client.getRequestData({'uploadTokenId': uploadTokenId}),
                operation='verify accepted upload',
            )
            return KalturaUploadToken.fromJsonResponse(res)

        def waitForFullUpload(self, uploadTokenId):
            last_status = None
            for attempt in range(self.client.UPLOAD_STATUS_POLL_ATTEMPTS):
                token = self.get(uploadTokenId)
                last_status = token.status
                if token.status == 2:
                    return token
                if token.status in (4, 5):
                    raise KalturaApiError(
                        'upload file bytes failed according to the upload token '
                        f'(token status {token.status})'
                    )
                if attempt < self.client.UPLOAD_STATUS_POLL_ATTEMPTS - 1:
                    time.sleep(self.client.UPLOAD_STATUS_POLL_INTERVAL)

            raise KalturaOutcomeUnknown(
                'upload file bytes could not be confirmed from the upload token '
                f'(last token status {last_status})'
            )
        
        def upload (self, uploadTokenId, fileData, resume, finalChunk, resumeAt):
            upload_url = self.client._upload_action_url(
                self.client.upload_urls.get(uploadTokenId, '')
            )

            url = self.client._with_query_params(upload_url, {
                'format': 1,
                'uploadTokenId': uploadTokenId,
                'resume': 'true' if resume else 'false',
                'finalChunk': 'true' if finalChunk else 'false',
                'resumeAt': resumeAt,
                'ks': self.client.sessionData.ks,
                'partnerId': self.client.sessionData.partnerId,
            })

            try:
                res = self.client.post_upload(url, fileData, operation='upload file bytes')
            except KalturaOutcomeUnknown:
                return self.waitForFullUpload(uploadTokenId)

            if not isinstance(res, dict) or not res.get('id'):
                return self.waitForFullUpload(uploadTokenId)

            token = KalturaUploadToken.fromJsonResponse(res)
            if token.status == 2:
                return token
            return self.waitForFullUpload(uploadTokenId)
        
    class MediaService(KalturaServiceBase):
        def add (self, mediaEntry: KalturaMediaEntry):
            res = self.client.post_json('media/action/add', self.client.getRequestData({
                'entry': mediaEntry.toDict()
            }), operation='create media entry')
            if not isinstance(res, dict) or not res.get('id'):
                raise KalturaOutcomeUnknown(
                    'create media entry returned no entry id; outcome requires reconciliation'
                )
            return KalturaMediaEntry.fromJsonResponse(res)
        
        def addContent(self, entry_id, resource):
            res = self.client.post_json('media/action/addContent', self.client.getRequestData({
                'entryId': entry_id,
                'resource': resource.toDict()
            }), operation='attach uploaded content')
            if not isinstance(res, dict) or res.get('id') != entry_id:
                raise KalturaOutcomeUnknown(
                    'attach uploaded content returned no matching entry id; '
                    'outcome requires reconciliation'
                )
            return res

        def get(self, entry_id):
            res = self.client.post_json(
                'media/action/get',
                self.client.getRequestData({'entryId': entry_id}),
                operation='get media entry',
            )
            if not isinstance(res, dict) or not res.get('id'):
                raise KalturaApiError('get media entry returned no entry id')
            return KalturaMediaEntry.fromJsonResponse(res)

        def listByNameAndOwner(self, name, owner_id):
            res = self.client.post_json(
                'media/action/list',
                self.client.getRequestData({
                    'filter': {
                        'objectType': 'KalturaMediaEntryFilter',
                        'nameEqual': name,
                        'userIdEqual': owner_id,
                    },
                }),
                operation='list media entries by exact name and owner',
            )
            if not isinstance(res, dict) or not isinstance(res.get('objects'), list):
                raise KalturaApiError('list media entries returned an unusable result')
            return [
                KalturaMediaEntry.fromJsonResponse(item)
                for item in res['objects']
                if isinstance(item, dict) and item.get('id')
            ]
        
    class UserService(KalturaServiceBase):
        def getByLoginId(self, loginId) -> KalturaUser:
            res = self.client.post_json('user/action/getByLoginId', self.client.getRequestData({
                'loginId': loginId
            }), operation='look up Kaltura user by login ID')
            return KalturaUser.fromJsonResponse(res)
        
        def get(self, userId) -> KalturaUser:
            res = self.client.post_json('user/action/get', self.client.getRequestData({
                'userId': userId
            }), operation='look up Kaltura user')
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
