"""Tests for the flake8.style_guide.StyleGuide class."""
from __future__ import annotations

import argparse
from unittest import mock

import pytest

from flake8 import statistics
from flake8 import style_guide
from flake8 import utils
from flake8.formatting import base


def create_options(**kwargs):
    """Create and return an instance of argparse.Namespace."""
    kwargs.setdefault("select", [])
    kwargs.setdefault("extended_default_select", [])
    kwargs.setdefault("extended_default_ignore", [])
    kwargs.setdefault("extend_select", [])
    kwargs.setdefault("ignore", [])
    kwargs.setdefault("extend_ignore", [])
    kwargs.setdefault("disable_noqa", False)
    kwargs.setdefault("enable_extensions", [])
    kwargs.setdefault("per_file_ignores", [])
    return argparse.Namespace(**kwargs)


def test_handle_error_does_not_raise_type_errors():
    """Verify that we handle our inputs better."""
    formatter = mock.create_autospec(base.BaseFormatter, instance=True)
    guide = style_guide.StyleGuide(
        create_options(select=["T111"], ignore=[]),
        formatter=formatter,
        stats=statistics.Statistics(),
    )

    assert 1 == guide.handle_error(
        "T111", "file.py", 1, 1, "error found", "a = 1",
    )


def test_style_guide_manager():
    """Verify how the StyleGuideManager creates a default style guide."""
    formatter = mock.create_autospec(base.BaseFormatter, instance=True)
    options = create_options()
    guide = style_guide.StyleGuideManager(options, formatter=formatter)
    assert guide.default_style_guide.options is options
    assert len(guide.style_guides) == 1


PER_FILE_IGNORES_UNPARSED = [
    "first_file.py:W9",
    "second_file.py:F4,F9",
    "third_file.py:E3",
    "sub_dir/*:F4",
]


@pytest.mark.parametrize(
    "style_guide_file,filename,expected",
    [
        ("first_file.py", "first_file.py", True),
        ("first_file.py", "second_file.py", False),
        ("sub_dir/*.py", "first_file.py", False),
        ("sub_dir/*.py", "sub_dir/file.py", True),
        ("sub_dir/*.py", "other_dir/file.py", False),
    ],
)
def test_style_guide_applies_to(style_guide_file, filename, expected):
    """Verify that we match a file to its style guide."""
    formatter = mock.create_autospec(base.BaseFormatter, instance=True)
    options = create_options()
    guide = style_guide.StyleGuide(
        options,
        formatter=formatter,
        stats=statistics.Statistics(),
        filename=style_guide_file,
    )
    assert guide.applies_to(filename) is expected


def test_style_guide_manager_pre_file_ignores_parsing():
    """Verify how the StyleGuideManager creates a default style guide."""
    formatter = mock.create_autospec(base.BaseFormatter, instance=True)
    options = create_options(per_file_ignores=PER_FILE_IGNORES_UNPARSED)
    guide = style_guide.StyleGuideManager(options, formatter=formatter)
    assert len(guide.style_guides) == 5
    expected = [
        utils.normalize_path(p)
        for p in [
            "first_file.py",
            "second_file.py",
            "third_file.py",
            "sub_dir/*",
        ]
    ]
    assert expected == [g.filename for g in guide.style_guides[1:]]


@pytest.mark.parametrize(
    "ignores,violation,filename,handle_error_return",
    [
        (["E1", "E2"], "F401", "first_file.py", 1),
        (["E1", "E2"], "E121", "first_file.py", 0),
        (["E1", "E2"], "F401", "second_file.py", 0),
        (["E1", "E2"], "F401", "third_file.py", 1),
        (["E1", "E2"], "E311", "third_file.py", 0),
        (["E1", "E2"], "F401", "sub_dir/file.py", 0),
    ],
)
def test_style_guide_manager_pre_file_ignores(
    ignores, violation, filename, handle_error_return,
):
    """Verify how the StyleGuideManager creates a default style guide."""
    formatter = mock.create_autospec(base.BaseFormatter, instance=True)
    options = create_options(
        ignore=ignores,
        select=["E", "F", "W"],
        per_file_ignores=PER_FILE_IGNORES_UNPARSED,
    )
    guide = style_guide.StyleGuideManager(options, formatter=formatter)
    assert (
        guide.handle_error(violation, filename, 1, 1, "Fake text")
        == handle_error_return
    )


@pytest.mark.parametrize(
    "filename,expected",
    [
        ("first_file.py", utils.normalize_path("first_file.py")),
        ("second_file.py", utils.normalize_path("second_file.py")),
        ("third_file.py", utils.normalize_path("third_file.py")),
        ("fourth_file.py", None),
        ("sub_dir/__init__.py", utils.normalize_path("sub_dir/*")),
        ("other_dir/__init__.py", None),
    ],
)
def test_style_guide_manager_style_guide_for(filename, expected):
    """Verify the style guide selection function."""
    formatter = mock.create_autospec(base.BaseFormatter, instance=True)
    options = create_options(per_file_ignores=PER_FILE_IGNORES_UNPARSED)
    guide = style_guide.StyleGuideManager(options, formatter=formatter)

    file_guide = guide.style_guide_for(filename)
    assert file_guide.filename == expected


def _make_manager(**kwargs):
    """Build a StyleGuideManager with mocked formatter for explanations."""
    formatter = mock.create_autospec(base.BaseFormatter, instance=True)
    options = create_options(**kwargs)
    return style_guide.StyleGuideManager(options, formatter=formatter)


@pytest.mark.parametrize(
    "options_kwargs,filename,code,physical_line,expected_reason",
    [
        # selected code on an unannotated line is reported
        (
            {"select": ["E", "F", "W"]},
            "plain.py",
            "F401",
            "import os\n",
            style_guide.SuppressionReason.Reported,
        ),
        # a code outside the selected list never reaches the report
        (
            {"select": ["E"]},
            "plain.py",
            "F401",
            "import os\n",
            style_guide.SuppressionReason.NotSelected,
        ),
        # a globally selected code ignored for this file only
        (
            {
                "select": ["E", "F", "W"],
                "per_file_ignores": ["ignored.py:F401"],
            },
            "ignored.py",
            "F401",
            "import os\n",
            style_guide.SuppressionReason.PerFileIgnored,
        ),
        # per-file rule does not bleed into other files
        (
            {
                "select": ["E", "F", "W"],
                "per_file_ignores": ["ignored.py:F401"],
            },
            "plain.py",
            "F401",
            "import os\n",
            style_guide.SuppressionReason.Reported,
        ),
        # selected code silenced by a blanket inline noqa
        (
            {"select": ["E", "F", "W"]},
            "plain.py",
            "F401",
            "import os  # noqa\n",
            style_guide.SuppressionReason.InlineIgnored,
        ),
        # selected code silenced by a specific inline noqa
        (
            {"select": ["E", "F", "W"]},
            "plain.py",
            "F401",
            "import os  # noqa: F401\n",
            style_guide.SuppressionReason.InlineIgnored,
        ),
        # an unrelated noqa code does not suppress the violation
        (
            {"select": ["E", "F", "W"]},
            "plain.py",
            "F401",
            "import os  # noqa: E501\n",
            style_guide.SuppressionReason.Reported,
        ),
        # --disable-noqa makes an inline comment ineffective
        (
            {"select": ["E", "F", "W"], "disable_noqa": True},
            "plain.py",
            "F401",
            "import os  # noqa\n",
            style_guide.SuppressionReason.Reported,
        ),
    ],
)
def test_explain_error_reasons(
    options_kwargs, filename, code, physical_line, expected_reason,
):
    """Verify explain_error classifies every decision outcome."""
    guide = _make_manager(**options_kwargs)

    explanation = guide.explain_error(
        code=code,
        filename=filename,
        line_number=1,
        column_number=0,
        text="unused import",
        physical_line=physical_line,
    )

    assert isinstance(explanation, style_guide.ViolationExplanation)
    assert explanation.reason is expected_reason
    assert explanation.code == code
    assert explanation.filename == filename
    assert explanation.line_number == 1
    # the source line is never part of the explanation
    assert not hasattr(explanation, "physical_line")
    assert physical_line not in explanation


def test_explain_error_does_not_report_or_record():
    """Verify explaining is read-only and leaves no report state behind."""
    formatter = mock.create_autospec(base.BaseFormatter, instance=True)
    options = create_options(select=["E", "F", "W"])
    guide = style_guide.StyleGuideManager(options, formatter=formatter)

    explanation = guide.explain_error(
        code="F401",
        filename="plain.py",
        line_number=2,
        column_number=4,
        text="unused import",
        physical_line="import os  # noqa\n",
    )

    assert explanation.reason is style_guide.SuppressionReason.InlineIgnored
    formatter.handle.assert_not_called()
    formatter.beginning.assert_not_called()
    assert guide.stats.error_codes() == []


def test_explain_error_matches_handle_error_outcome():
    """Verify the explanation agrees with the actual reporting decision."""
    formatter = mock.create_autospec(base.BaseFormatter, instance=True)
    options = create_options(
        select=["E", "F", "W"],
        per_file_ignores=["ignored.py:F401"],
    )
    guide = style_guide.StyleGuideManager(options, formatter=formatter)

    cases = [
        ("plain.py", "import os\n", 1),
        ("plain.py", "import os  # noqa\n", 0),
        ("ignored.py", "import os\n", 0),
    ]
    for filename, physical_line, reported in cases:
        handled = guide.handle_error(
            "F401", filename, 1, 0, "unused import", physical_line,
        )
        explanation = guide.explain_error(
            "F401", filename, 1, 0, "unused import", physical_line,
        )
        expects_report = (
            explanation.reason is style_guide.SuppressionReason.Reported
        )
        assert bool(handled) is expects_report
