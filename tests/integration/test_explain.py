"""Integration tests for the read-only violation explanation entry point."""
from __future__ import annotations

from flake8.formatting import base
from flake8.main import application
from flake8.style_guide import SuppressionReason


class _RecordingFormatter(base.BaseFormatter):
    """Record the violations that actually reach the report."""

    def __init__(self, options) -> None:
        super().__init__(options)
        self.reported: list[tuple[str, str, int]] = []

    def format(self, error):
        return None

    def handle(self, error) -> None:
        self.reported.append((error.filename, error.code, error.line_number))


def _run(workdir, files, extra_argv):
    paths = []
    for name, source in files.items():
        target = workdir.join(name)
        target.write(source)
        paths.append(target.strpath)

    app = application.Application()
    app.initialize([*extra_argv, *paths])

    recording = _RecordingFormatter(app.options)
    app.formatter = recording
    app.guide.formatter = recording
    for guide in app.guide.style_guides:
        guide.formatter = recording

    app.run_checks()
    app.report_errors()

    explanations = []
    manager = app.file_checker_manager
    for filename, results, _ in manager.results:
        for code, line_number, column, text, physical_line in results:
            explanations.append(
                app.guide.explain_error(
                    code=code,
                    filename=filename,
                    line_number=line_number,
                    column_number=column,
                    text=text,
                    physical_line=physical_line,
                ),
            )
    return recording, explanations


def test_real_results_are_matched_to_suppression_reasons(tmpdir):
    """Each real check result maps to the reason it was reported or hidden."""
    main_py = (
        "import sys\n"  # 1: F401 selected -> reported
        "import os  # noqa\n"  # 2: F401 silenced inline
        "x=1\n"  # 3: E225 fires but the code is not selected
    )
    ignored_py = "import json\n"  # F401 selected but ignored for this file

    argv = [
        "--select=F",
        "--per-file-ignores=ignored.py:F401",
    ]

    with tmpdir.as_cwd():
        recording, explanations = _run(
            tmpdir,
            {"main.py": main_py, "ignored.py": ignored_py},
            argv,
        )

    def reason_for(basename, code, line_number):
        matches = [
            item
            for item in explanations
            if item.filename.endswith(basename)
            and item.code == code
            and item.line_number == line_number
        ]
        assert len(matches) == 1
        return matches[0].reason

    assert reason_for("main.py", "F401", 1) is SuppressionReason.Reported
    assert (
        reason_for("main.py", "F401", 2)
        is SuppressionReason.InlineIgnored
    )
    assert (
        reason_for("main.py", "E225", 3) is SuppressionReason.NotSelected
    )
    assert (
        reason_for("ignored.py", "F401", 1)
        is SuppressionReason.PerFileIgnored
    )

    # the four distinct check results were all explained
    assert len(explanations) == 4

    # only the selected, non-suppressed F401 in main.py reaches the report
    assert len(recording.reported) == 1
    reported_filename, reported_code, reported_line = recording.reported[0]
    assert reported_filename.endswith("main.py")
    assert reported_code == "F401"
    assert reported_line == 1

    # the explanations agree with what the formatter actually received
    explained_reported = {
        (item.filename, item.code, item.line_number)
        for item in explanations
        if item.reason is SuppressionReason.Reported
    }
    assert explained_reported == set(recording.reported)

    # no source lines leak into the explanation structure
    for item in explanations:
        assert not hasattr(item, "physical_line")


def test_explain_is_read_only_across_a_real_run(tmpdir):
    """Explaining again does not add reports or change statistics."""
    source = "import os  # noqa\n"

    with tmpdir.as_cwd():
        recording, explanations = _run(
            tmpdir, {"t.py": source}, ["--select=F"],
        )

    assert len(explanations) == 1
    assert explanations[0].reason is SuppressionReason.InlineIgnored
    assert recording.reported == []

    # a second, purely explanatory pass records nothing new
    with tmpdir.as_cwd():
        target = tmpdir.join("t.py")
        app = application.Application()
        app.initialize(["--select=F", target.strpath])
        guide = app.guide
        before = list(guide.stats.error_codes())
        explanation = guide.explain_error(
            code="F401",
            filename=target.strpath,
            line_number=1,
            column_number=0,
            text="'os' imported but unused",
            physical_line=source,
        )
        assert explanation.reason is SuppressionReason.InlineIgnored
        assert guide.stats.error_codes() == before
