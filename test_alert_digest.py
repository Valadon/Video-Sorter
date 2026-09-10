from datetime import datetime, timezone
from email import policy
from email.parser import Parser
import logging

import pytest

import alert_digest
from alert_digest import AlertDigestHandler, run_with_alert_digest


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sent = []
        self.quit_called = False
        self.instances.append(self)

    def sendmail(self, from_address, to_addresses, message):
        self.sent.append((from_address, to_addresses, message))

    def quit(self):
        self.quit_called = True


@pytest.fixture
def digest_logger(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(alert_digest.smtplib, 'SMTP', FakeSMTP)

    logger = logging.getLogger('video-sorter-alert-digest-test')
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    handler = AlertDigestHandler(
        'smtp.example.edu',
        'sorter@example.edu',
        ['alerts@example.edu'],
        'Video Sorter Event',
        now=lambda: datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc),
    )
    handler.setLevel(logging.WARNING)
    logger.addHandler(handler)

    yield logger, handler

    logger.removeHandler(handler)
    handler.close()


def sent_body():
    raw_message = FakeSMTP.instances[0].sent[0][2]
    message = Parser(policy=policy.default).parsestr(raw_message)
    return message.get_content()


def test_multiple_warnings_and_errors_send_one_digest(digest_logger):
    logger, handler = digest_logger

    logger.warning('Recording 4603_20260910.mp4 did not match a course.')
    logger.error('LAW 7410-1 upload failed: token expired.')
    logger.warning('Course LAW 7800-1 is missing instructor data.')

    assert handler.send_digest('scheduled processing') is True
    assert len(FakeSMTP.instances) == 1
    assert len(FakeSMTP.instances[0].sent) == 1
    assert FakeSMTP.instances[0].quit_called is True

    body = sent_body()
    assert 'Run: scheduled processing' in body
    assert 'Alerts: 3 (1 error, 2 warning)' in body
    assert 'Recording 4603_20260910.mp4 did not match a course.' in body
    assert 'LAW 7410-1 upload failed: token expired.' in body
    assert 'Course LAW 7800-1 is missing instructor data.' in body

    # A second boundary does not repeat alerts from the previous run.
    assert handler.send_digest('scheduled processing') is False
    assert len(FakeSMTP.instances) == 1


def test_clean_run_sends_no_email(digest_logger):
    logger, handler = digest_logger

    logger.info('No new videos to sort.')

    assert handler.send_digest('scheduled processing') is False
    assert FakeSMTP.instances == []


def test_unhandled_exception_is_included_before_digest_flush(digest_logger):
    logger, handler = digest_logger

    def failing_run():
        logger.warning('Recording problem discovered before the crash.')
        raise RuntimeError('destination became unavailable')

    with pytest.raises(RuntimeError, match='destination became unavailable'):
        run_with_alert_digest(
            failing_run,
            handler,
            'startup and one-pass processing',
            logger=logger,
        )

    assert len(FakeSMTP.instances) == 1
    assert len(FakeSMTP.instances[0].sent) == 1
    body = sent_body()
    assert 'Recording problem discovered before the crash.' in body
    assert 'Unhandled exception during startup and one-pass processing.' in body
    assert 'RuntimeError: destination became unavailable' in body


def test_configured_error_threshold_omits_warnings(digest_logger):
    logger, handler = digest_logger
    handler.setLevel(logging.ERROR)

    logger.warning('A warning below the configured email threshold.')
    logger.error('An error at the configured threshold.')

    assert handler.send_digest('scheduled processing') is True
    body = sent_body()
    assert 'Alerts: 1 (1 error)' in body
    assert 'An error at the configured threshold.' in body
    assert 'A warning below the configured email threshold.' not in body
