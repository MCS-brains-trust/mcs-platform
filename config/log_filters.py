"""
Logging filters to prevent PII and sensitive data from leaking into log output.

Automatically scrubs email addresses, Australian TFNs, and phone numbers.
Applied via the LOGGING configuration in settings.py.
"""
import logging
import re


class SensitiveDataFilter(logging.Filter):
    """Redact PII patterns (emails, TFNs, phone numbers) from log records."""

    PATTERNS = [
        # Email addresses
        (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
        # Australian TFN: 9 digits, optionally separated by spaces or hyphens
        (re.compile(r"\b\d{3}[\s-]?\d{3}[\s-]?\d{3}\b"), "[TFN]"),
        # Australian phone: 04xx xxx xxx, +61 x xxxx xxxx, or (0x) xxxx xxxx
        (re.compile(r"(?:\+61|0)\d[\s.-]?\d{4}[\s.-]?\d{4}"), "[PHONE]"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._scrub_arg(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._scrub_arg(a) for a in record.args)
        # Scrub exception tracebacks. The message/args are stringified with a
        # traceback appended by the handler's formatter *after* this filter runs,
        # so exception text (which can carry PII in str(exc) or repr'd locals) is
        # otherwise never scrubbed. Format it now, scrub it, and stash it in
        # exc_text so the formatter uses our scrubbed copy verbatim.
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
            record.exc_info = None
        if record.exc_text:
            record.exc_text = self._scrub(record.exc_text)
        return True

    def _scrub_arg(self, value):
        """Scrub a single log arg. Non-strings are scrubbed via their str(),
        but the original value is kept when no PII is found so numeric format
        specifiers (%d/%f) still work; when PII is redacted the (string)
        redacted form is substituted."""
        if isinstance(value, str):
            return self._scrub(value)
        text = str(value)
        scrubbed = self._scrub(text)
        return scrubbed if scrubbed != text else value

    def _scrub(self, value):
        if not isinstance(value, str):
            return value
        for pattern, replacement in self.PATTERNS:
            value = pattern.sub(replacement, value)
        return value
