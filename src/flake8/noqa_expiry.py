"""Optional tracking of dated ``# noqa`` exemptions.

This module implements an opt-in report for ``# noqa`` comments that
carry an expiry date, for example::

    example = lambda: 'example'  # noqa: E731 [until 2026-12-31]

Plain ``# noqa`` comments (without a date) are completely unaffected.
When ``--noqa-expiry-report`` is supplied, Flake8 records every dated
exemption in the checked files, the violations each exemption actually
hides, and classifies each one as ``valid``, ``expired``, or
``unmatched`` (no corresponding violation).  The resulting JSON report
is intended to be consumed by maintenance scripts.  Flake8 never
rewrites the tracked comments or the code around them.
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import tokenize
from typing import Any
from typing import NamedTuple

from flake8 import exceptions
from flake8 import utils

LOG = logging.getLogger(__name__)

NOQA_UNTIL_REGEXP = re.compile(
    # We're looking for items that look like this:
    # ``# noqa [until 2026-12-31]``
    # ``# noqa: E501 [until 2026-12-31]``
    # ``# noqa: E501,W605 [until 2026-12-31]``
    # Everything else about the comment follows the regular ``# noqa``
    # rules (see ``defaults.NOQA_INLINE_REGEXP``).
    r"# noqa(?::[\s]?(?P<codes>([A-Z]+[0-9]+(?:[,\s]+)?)+))?"
    r"\s*\[until\s+(?P<until>\d{4}-\d{2}-\d{2})\]",
    re.IGNORECASE,
)

VALID = "valid"
EXPIRED = "expired"
UNMATCHED = "unmatched"


class HiddenViolation(NamedTuple):
    """A violation that was suppressed by a dated exemption."""

    code: str
    line_number: int
    column_number: int
    text: str


class Exemption:
    """A single dated ``# noqa`` comment found in a checked file."""

    def __init__(
        self,
        filename: str,
        line_number: int,
        codes: tuple[str, ...] | None,
        until: datetime.date,
        physical_line: str,
    ) -> None:
        self.filename = filename
        self.line_number = line_number
        #: Tuple of error codes the exemption applies to, or ``None``
        #: for a blanket ``# noqa [until ...]`` comment.
        self.codes = codes
        self.until = until
        self.physical_line = physical_line
        #: Violations actually hidden by this exemption.
        self.hidden: list[HiddenViolation] = []

    def status(self, today: datetime.date) -> str:
        """Classify this exemption relative to ``today``."""
        if self.until < today:
            return EXPIRED
        elif not self.hidden:
            return UNMATCHED
        else:
            return VALID

    def as_dict(self, today: datetime.date) -> dict[str, Any]:
        """Return a JSON-serializable representation of this exemption."""
        return {
            "filename": self.filename,
            "line_number": self.line_number,
            "codes": list(self.codes) if self.codes is not None else None,
            "until": self.until.isoformat(),
            "status": self.status(today),
            "hidden": [violation._asdict() for violation in self.hidden],
        }


class NoqaExpiryRecorder:
    """Collect dated exemptions and the violations they hide."""

    def __init__(self, today: datetime.date, report_path: str) -> None:
        self.today = today
        self.report_path = report_path
        self.exemptions: list[Exemption] = []
        self._by_location: dict[tuple[str, int], Exemption] = {}

    def scan_file(self, filename: str) -> None:
        """Register every dated exemption found in ``filename``."""
        if filename == "-":
            # stdin has already been consumed by the checker
            return
        try:
            with tokenize.open(filename) as f:
                lines = f.readlines()
        except (OSError, SyntaxError, UnicodeDecodeError) as e:
            LOG.debug("could not scan %r for dated exemptions: %s",
                      filename, e)
            return

        for line_number, physical_line in enumerate(lines, 1):
            match = NOQA_UNTIL_REGEXP.search(physical_line)
            if match is None:
                continue
            try:
                until = datetime.date.fromisoformat(match.group("until"))
            except ValueError:
                LOG.debug(
                    "ignoring exemption with invalid date at %s:%d",
                    filename, line_number,
                )
                continue
            codes_str = match.group("codes")
            codes = (
                tuple(utils.parse_comma_separated_list(codes_str))
                if codes_str is not None
                else None
            )
            exemption = Exemption(
                filename=filename,
                line_number=line_number,
                codes=codes,
                until=until,
                physical_line=physical_line.rstrip("\r\n"),
            )
            self.exemptions.append(exemption)
            self._by_location[(filename, line_number)] = exemption

    def record_hidden(self, error: Any) -> None:
        """Associate a suppressed violation with its dated exemption."""
        exemption = self._by_location.get(
            (error.filename, error.line_number),
        )
        if exemption is None and error.physical_line is not None:
            # the ``noqa`` comment may live on a different physical line
            # than the violation (e.g. multi-line strings)
            if NOQA_UNTIL_REGEXP.search(error.physical_line) is None:
                return
            exemption = self._find_by_line_text(
                error.filename, error.physical_line.rstrip("\r\n"),
            )
        if exemption is None:
            return
        exemption.hidden.append(
            HiddenViolation(
                code=error.code,
                line_number=error.line_number,
                column_number=error.column_number,
                text=error.text,
            ),
        )

    def _find_by_line_text(
        self, filename: str, physical_line: str,
    ) -> Exemption | None:
        for exemption in self.exemptions:
            if (
                exemption.filename == filename
                and exemption.physical_line == physical_line
            ):
                return exemption
        return None

    def as_dict(self) -> dict[str, Any]:
        """Return the full report as a JSON-serializable mapping."""
        exemptions = [
            exemption.as_dict(self.today) for exemption in self.exemptions
        ]
        summary = {VALID: 0, EXPIRED: 0, UNMATCHED: 0}
        for exemption in exemptions:
            summary[exemption["status"]] += 1
        return {
            "today": self.today.isoformat(),
            "summary": summary,
            "exemptions": exemptions,
        }

    def write_report(self) -> None:
        """Write the JSON report to the configured path."""
        with open(self.report_path, "w", encoding="utf-8") as f:
            json.dump(self.as_dict(), f, indent=2)
            f.write("\n")
        LOG.info("wrote noqa expiry report to %s", self.report_path)


_recorder: NoqaExpiryRecorder | None = None


def configure(options: Any) -> NoqaExpiryRecorder | None:
    """Enable the recorder if ``--noqa-expiry-report`` was supplied."""
    global _recorder
    _recorder = None
    report_path = getattr(options, "noqa_expiry_report", None)
    if not report_path:
        return None

    date_override = getattr(options, "noqa_expiry_date", None)
    if date_override is None:
        today = datetime.date.today()
    else:
        try:
            today = datetime.date.fromisoformat(date_override)
        except ValueError:
            raise exceptions.ExecutionError(
                f"--noqa-expiry-date must be a valid YYYY-MM-DD date, "
                f"got {date_override!r}",
            )
    _recorder = NoqaExpiryRecorder(today=today, report_path=report_path)
    return _recorder


def active() -> NoqaExpiryRecorder | None:
    """Return the currently configured recorder, if any."""
    return _recorder


def record_hidden(error: Any) -> None:
    """Record a suppressed violation if the recorder is enabled."""
    if _recorder is not None:
        _recorder.record_hidden(error)
