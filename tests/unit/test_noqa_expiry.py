"""Tests for the flake8.noqa_expiry module."""
from __future__ import annotations

import argparse
import datetime
import json

import pytest

from flake8 import exceptions
from flake8 import noqa_expiry
from flake8.violation import Violation

TODAY = datetime.date(2026, 11, 12)


@pytest.fixture
def recorder(tmp_path):
    return noqa_expiry.NoqaExpiryRecorder(
        today=TODAY, report_path=str(tmp_path / "report.json"),
    )


def _write(tmp_path, lines):
    path = tmp_path / "example.py"
    path.write_text("".join(lines))
    return str(path)


def _violation(filename, line_number, code="F401"):
    return Violation(
        code=code,
        filename=filename,
        line_number=line_number,
        column_number=1,
        text=f"{code} something",
        physical_line=None,
    )


@pytest.mark.parametrize(
    "line,codes,until",
    (
        ("x = 1  # noqa [until 2026-12-31]", None, "2026-12-31"),
        ("x = 1  # noqa: E501 [until 2026-12-31]", ("E501",), "2026-12-31"),
        (
            "x = 1  # noqa: E501,W605 [until 2026-12-31]",
            ("E501", "W605"),
            "2026-12-31",
        ),
        ("x = 1  # NOQA: E501 [UNTIL 2026-12-31]", ("E501",), "2026-12-31"),
        ("x = 1  # noqa: E501[until 2026-12-31]", ("E501",), "2026-12-31"),
    ),
)
def test_regexp_matches(line, codes, until):
    match = noqa_expiry.NOQA_UNTIL_REGEXP.search(line)
    assert match is not None
    assert match.group("until") == until
    if codes is None:
        assert match.group("codes") is None
    else:
        found = tuple(match.group("codes").replace(",", " ").split())
        assert found == codes


@pytest.mark.parametrize(
    "line",
    (
        "x = 1",
        "x = 1  # noqa",
        "x = 1  # noqa: E501",
        "x = 1  # noqa: E501 [until later]",
        "x = 1  # noqa: E501 [until 2026-1-1]",
    ),
)
def test_regexp_does_not_match(line):
    assert noqa_expiry.NOQA_UNTIL_REGEXP.search(line) is None


def test_scan_file_registers_exemptions(recorder, tmp_path):
    filename = _write(tmp_path, (
        "import os  # noqa: F401 [until 2027-01-01]\n",
        "import sys  # noqa: F401 [until 2026-01-01]\n",
        "x = 1  # noqa: E501\n",
    ))
    recorder.scan_file(filename)
    assert len(recorder.exemptions) == 2
    first, second = recorder.exemptions
    assert first.codes == ("F401",)
    assert first.until == datetime.date(2027, 1, 1)
    assert second.until == datetime.date(2026, 1, 1)


def test_scan_file_ignores_invalid_date(recorder, tmp_path):
    filename = _write(tmp_path, (
        "x = 1  # noqa: E501 [until 2026-13-99]\n",
    ))
    recorder.scan_file(filename)
    assert recorder.exemptions == []


def test_scan_missing_file_is_ignored(recorder):
    recorder.scan_file("does-not-exist.py")
    assert recorder.exemptions == []


def test_status_classification(recorder, tmp_path):
    filename = _write(tmp_path, (
        "a = 1  # noqa: F401 [until 2027-01-01]\n",
        "b = 2  # noqa: F401 [until 2026-01-01]\n",
        "c = 3  # noqa: E501 [until 2027-06-30]\n",
    ))
    recorder.scan_file(filename)
    recorder.record_hidden(_violation(filename, 1))
    recorder.record_hidden(_violation(filename, 2))

    statuses = [e.status(TODAY) for e in recorder.exemptions]
    assert statuses == ["valid", "expired", "unmatched"]


def test_report_is_json_serializable(recorder, tmp_path):
    filename = _write(tmp_path, (
        "a = 1  # noqa: F401 [until 2027-01-01]\n",
    ))
    recorder.scan_file(filename)
    recorder.record_hidden(_violation(filename, 1))
    recorder.write_report()

    with open(recorder.report_path) as f:
        report = json.load(f)
    assert report["today"] == "2026-11-12"
    assert report["summary"] == {"valid": 1, "expired": 0, "unmatched": 0}
    (exemption,) = report["exemptions"]
    assert exemption["filename"] == filename
    assert exemption["line_number"] == 1
    assert exemption["codes"] == ["F401"]
    assert exemption["until"] == "2027-01-01"
    assert exemption["status"] == "valid"
    assert exemption["hidden"] == [
        {
            "code": "F401",
            "line_number": 1,
            "column_number": 1,
            "text": "F401 something",
        },
    ]


def test_record_hidden_without_exemption_is_ignored(recorder, tmp_path):
    filename = _write(tmp_path, ("a = 1  # noqa: F401\n",))
    recorder.scan_file(filename)
    recorder.record_hidden(_violation(filename, 1))
    assert recorder.exemptions == []


def test_record_hidden_via_physical_line(recorder, tmp_path):
    # the violation line may differ from the line holding the comment
    filename = _write(tmp_path, (
        "x = '''  # noqa: F401 [until 2027-01-01]\n",
        "blah\n",
        "'''\n",
    ))
    recorder.scan_file(filename)
    error = _violation(filename, 2)
    error = error._replace(
        physical_line="x = '''  # noqa: F401 [until 2027-01-01]\n",
    )
    recorder.record_hidden(error)
    assert len(recorder.exemptions[0].hidden) == 1


def test_configure_disabled_by_default():
    options = argparse.Namespace(
        noqa_expiry_report=None, noqa_expiry_date=None,
    )
    assert noqa_expiry.configure(options) is None
    assert noqa_expiry.active() is None


def test_configure_with_date_override():
    options = argparse.Namespace(
        noqa_expiry_report="report.json", noqa_expiry_date="2026-11-12",
    )
    recorder = noqa_expiry.configure(options)
    assert recorder is noqa_expiry.active()
    assert recorder.today == datetime.date(2026, 11, 12)
    noqa_expiry.configure(
        argparse.Namespace(noqa_expiry_report=None, noqa_expiry_date=None),
    )


def test_configure_rejects_invalid_date():
    options = argparse.Namespace(
        noqa_expiry_report="report.json", noqa_expiry_date="not-a-date",
    )
    with pytest.raises(exceptions.ExecutionError):
        noqa_expiry.configure(options)
