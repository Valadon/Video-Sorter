"""Durable, per-owner progress for restart-safe Kaltura uploads."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
import sqlite3


STATE_NEW = 'new'
STATE_TOKEN_CREATED = 'token_created'
STATE_BYTES_SUBMITTING = 'bytes_submitting'
STATE_BYTES_UPLOADED = 'bytes_uploaded'
STATE_ENTRY_CREATING = 'entry_creating'
STATE_ENTRY_CREATED = 'entry_created'
STATE_ATTACHING = 'attaching'
STATE_ATTACHED = 'attached'
STATE_MANUAL_RECONCILE = 'manual_reconcile'
VALID_STATES = {
    STATE_NEW,
    STATE_TOKEN_CREATED,
    STATE_BYTES_SUBMITTING,
    STATE_BYTES_UPLOADED,
    STATE_ENTRY_CREATING,
    STATE_ENTRY_CREATED,
    STATE_ATTACHING,
    STATE_ATTACHED,
    STATE_MANUAL_RECONCILE,
}


@dataclass(frozen=True)
class UploadReceipt:
    source_sha256: str
    owner_id: str
    source_name: str
    state: str
    upload_token_id: str | None
    entry_id: str | None
    detail: str | None
    updated_at: str


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(chunk_size), b''):
            digest.update(chunk)
    return digest.hexdigest()


class UploadJournal:
    """SQLite journal keyed by source content and Kaltura owner."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute('PRAGMA synchronous = FULL')
        self._connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS upload_receipts (
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
        self._connection.commit()

    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat(timespec='seconds')

    @staticmethod
    def _receipt(row: sqlite3.Row) -> UploadReceipt:
        return UploadReceipt(**dict(row))

    def get(self, source_sha256: str, owner_id: str) -> UploadReceipt | None:
        row = self._connection.execute(
            '''
            SELECT source_sha256, owner_id, source_name, state,
                   upload_token_id, entry_id, detail, updated_at
            FROM upload_receipts
            WHERE source_sha256 = ? AND owner_id = ?
            ''',
            (source_sha256, owner_id),
        ).fetchone()
        return self._receipt(row) if row is not None else None

    def list_receipts(self, state: str | None = None) -> list[UploadReceipt]:
        if state is not None and state not in VALID_STATES:
            raise ValueError(f'Unknown upload receipt state: {state}')

        query = (
            'SELECT source_sha256, owner_id, source_name, state, '
            'upload_token_id, entry_id, detail, updated_at '
            'FROM upload_receipts'
        )
        parameters = ()
        if state is not None:
            query += ' WHERE state = ?'
            parameters = (state,)
        query += ' ORDER BY updated_at, source_name, owner_id'
        return [
            self._receipt(row)
            for row in self._connection.execute(query, parameters).fetchall()
        ]

    def get_or_create(
        self,
        source_sha256: str,
        owner_id: str,
        source_name: str,
    ) -> UploadReceipt:
        now = self._timestamp()
        with self._connection:
            self._connection.execute(
                '''
                INSERT OR IGNORE INTO upload_receipts (
                    source_sha256, owner_id, source_name, state, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ''',
                (source_sha256, owner_id, source_name, STATE_NEW, now),
            )
        return self.get(source_sha256, owner_id)

    def update(
        self,
        source_sha256: str,
        owner_id: str,
        state: str,
        *,
        upload_token_id: str | None = None,
        entry_id: str | None = None,
        detail: str | None = None,
    ) -> UploadReceipt:
        if state not in VALID_STATES:
            raise ValueError(f'Unknown upload receipt state: {state}')
        current = self.get(source_sha256, owner_id)
        if current is None:
            raise KeyError(f'No upload receipt for {source_sha256}:{owner_id}')

        next_upload_token_id = (
            upload_token_id if upload_token_id is not None else current.upload_token_id
        )
        next_entry_id = entry_id if entry_id is not None else current.entry_id
        with self._connection:
            self._connection.execute(
                '''
                UPDATE upload_receipts
                SET state = ?, upload_token_id = ?, entry_id = ?, detail = ?, updated_at = ?
                WHERE source_sha256 = ? AND owner_id = ?
                ''',
                (
                    state,
                    next_upload_token_id,
                    next_entry_id,
                    detail,
                    self._timestamp(),
                    source_sha256,
                    owner_id,
                ),
            )
        return self.get(source_sha256, owner_id)
