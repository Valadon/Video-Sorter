"""Batch warning and error logs into one SMTP message per sorter run."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime
from email.message import EmailMessage
import logging
import smtplib
from typing import TypeVar


Result = TypeVar('Result')


class AlertDigestHandler(logging.Handler):
    """Collect qualifying log records until an explicit batch boundary."""

    def __init__(
        self,
        mailhost: str | tuple[str, int],
        fromaddr: str,
        toaddrs: list[str],
        subject: str,
        *,
        timeout: float = 5.0,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__()
        if isinstance(mailhost, tuple):
            self.mailhost, self.mailport = mailhost
        else:
            self.mailhost, self.mailport = mailhost, 0
        self.fromaddr = fromaddr
        self.toaddrs = toaddrs
        self.subject = subject
        self.timeout = timeout
        self._now = now
        self._records: list[logging.LogRecord] = []
        self.setFormatter(logging.Formatter(
            '[%(levelname)s] %(asctime)s %(message)s',
            datefmt='[%m/%d/%Y %I:%M:%S %p]',
        ))

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, 'skip_alert_digest', False):
            return
        self._records.append(record)

    def send_digest(self, run_name: str) -> bool:
        """Send and clear the current digest, or do nothing when it is empty."""
        self.acquire()
        try:
            if not self._records:
                return False
            records = self._records
            # Clear before attempting delivery so a mail outage cannot make old
            # alerts repeat in every later run. The file log remains authoritative.
            self._records = []
        finally:
            self.release()

        try:
            message = EmailMessage()
            message['From'] = self.fromaddr
            message['To'] = ', '.join(self.toaddrs)
            message['Subject'] = self.subject
            message.set_content(self._format_digest(records, run_name))

            smtp = smtplib.SMTP(self.mailhost, self.mailport, timeout=self.timeout)
            try:
                smtp.sendmail(self.fromaddr, self.toaddrs, message.as_string())
            finally:
                smtp.quit()
        except Exception:
            logging.getLogger().error(
                'Could not send the Video Sorter alert digest for %s. '
                'The alerts remain in the file log.',
                run_name,
                exc_info=True,
                extra={'skip_alert_digest': True},
            )
            return False
        return True

    def _format_digest(self, records: list[logging.LogRecord], run_name: str) -> str:
        counts = Counter(record.levelname for record in records)
        preferred_order = ('CRITICAL', 'ERROR', 'WARNING')
        levels = [level for level in preferred_order if counts[level]]
        levels.extend(sorted(level for level in counts if level not in preferred_order))
        summary = ', '.join(f'{counts[level]} {level.lower()}' for level in levels)

        lines = [
            'Video Sorter alert digest',
            f'Run: {run_name}',
            f'Completed: {self._now().astimezone().isoformat(timespec="seconds")}',
            f'Alerts: {len(records)} ({summary})',
            '',
        ]
        for index, record in enumerate(records, start=1):
            lines.append(f'{index}. {self.format(record)}')
            lines.append('')
        return '\n'.join(lines).rstrip() + '\n'


def run_with_alert_digest(
    action: Callable[[], Result],
    handler: AlertDigestHandler,
    run_name: str,
    *,
    logger: logging.Logger | None = None,
) -> Result:
    """Run one batch, record an unexpected failure, and always flush its digest."""
    run_logger = logger or logging.getLogger()
    try:
        return action()
    except Exception:
        run_logger.exception('Unhandled exception during %s.', run_name)
        raise
    finally:
        handler.send_digest(run_name)
