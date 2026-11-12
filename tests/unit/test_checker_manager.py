"""Tests for the Manager object for FileCheckers."""
from __future__ import annotations

import errno
import logging
import multiprocessing
from unittest import mock

import pytest

from flake8 import checker
from flake8 import exceptions
from flake8.main.options import JobsArgument
from flake8.plugins import finder


def style_guide_mock():
    """Create a mock StyleGuide object."""
    return mock.MagicMock(**{"options.jobs": JobsArgument("4")})


def _parallel_checker_manager():
    """Call Manager.run() and return the number of calls to `run_serial`."""
    style_guide = style_guide_mock()
    manager = checker.Manager(style_guide, finder.Checkers([], [], []), [])
    # multiple files is needed for parallel mode
    manager.filenames = ("file1", "file2")
    return manager


def test_oserrors_cause_serial_fall_back():
    """Verify that OSErrors will cause the Manager to fallback to serial."""
    err = OSError(errno.ENOSPC, "Ominous message about spaceeeeee")
    with mock.patch("_multiprocessing.SemLock", side_effect=err):
        manager = _parallel_checker_manager()
        with mock.patch.object(manager, "run_serial") as serial:
            manager.run()
    assert serial.call_count == 1


def test_oserrors_are_reraised():
    """Verify that unexpected OSErrors will cause the Manager to reraise."""
    err = OSError(errno.EAGAIN, "Ominous message")
    with mock.patch("_multiprocessing.SemLock", side_effect=err):
        manager = _parallel_checker_manager()
        with (
            mock.patch.object(manager, "run_serial") as serial,
            pytest.raises(OSError),
        ):
            manager.run()
    assert serial.call_count == 0


def test_multiprocessing_cpu_count_not_implemented():
    """Verify that jobs is 0 if cpu_count is unavailable."""
    style_guide = style_guide_mock()
    style_guide.options.jobs = JobsArgument("auto")

    with mock.patch.object(
        multiprocessing,
        "cpu_count",
        side_effect=NotImplementedError,
    ):
        manager = checker.Manager(style_guide, finder.Checkers([], [], []), [])
    assert manager.jobs == 0


def test_jobs_count_limited_to_file_count():
    style_guide = style_guide_mock()
    style_guide.options.jobs = JobsArgument("4")
    style_guide.options.filenames = ["file1", "file2"]
    manager = checker.Manager(style_guide, finder.Checkers([], [], []), [])
    assert manager.jobs == 4
    manager.start()
    assert manager.jobs == 2


def test_make_checkers():
    """Verify that we create a list of FileChecker instances."""
    style_guide = style_guide_mock()
    style_guide.options.filenames = ["file1", "file2"]
    manager = checker.Manager(style_guide, finder.Checkers([], [], []), [])
    manager.start()
    assert manager.filenames == ("file1", "file2")


def _manager_with_tree(tmp_path, *, file_budget=None, exclude=()):
    for name in ("a.py", "b.py", "c.py"):
        tmp_path.joinpath(name).write_text("import os\n")
    excluded_dir = tmp_path.joinpath("vendor")
    excluded_dir.mkdir()
    excluded_dir.joinpath("d.py").write_text("import sys\n")

    style_guide = style_guide_mock()
    style_guide.options.filenames = [str(tmp_path)]
    style_guide.options.stdin_display_name = "stdin"
    style_guide.options.filename = ("*.py",)
    style_guide.options.exclude = [str(excluded_dir), *exclude]
    style_guide.options.extend_exclude = []
    style_guide.options.file_budget = file_budget
    return checker.Manager(style_guide, finder.Checkers([], [], []), [])


def test_manager_tracks_excluded_paths(tmp_path, caplog):
    manager = _manager_with_tree(tmp_path)
    with caplog.at_level(logging.INFO, logger="flake8.checker"):
        manager.start()

    assert len(manager.filenames) == 3
    assert manager.discovery_truncated is False
    assert manager.excluded_paths == (str(tmp_path.joinpath("vendor")),)
    assert any(
        "excluded from checks" in record.getMessage()
        and "vendor" in record.getMessage()
        for record in caplog.records
    )
    assert not any(
        "File budget" in record.getMessage() for record in caplog.records
    )


def test_manager_budget_truncates_and_warns(tmp_path, caplog):
    manager = _manager_with_tree(tmp_path, file_budget=2)
    with caplog.at_level(logging.WARNING, logger="flake8.checker"):
        manager.start()

    assert len(manager.filenames) == 2
    assert manager.discovery_truncated is True
    assert manager.file_budget == 2
    warning = next(
        record
        for record in caplog.records
        if "File budget" in record.getMessage()
    )
    assert warning.levelno == logging.WARNING
    assert "2" in warning.getMessage()


def test_manager_exclusion_and_truncation_are_reported_separately(
    tmp_path, caplog,
):
    manager = _manager_with_tree(tmp_path, file_budget=1)
    with caplog.at_level(logging.INFO, logger="flake8.checker"):
        manager.start()

    assert manager.excluded_paths == (str(tmp_path.joinpath("vendor")),)
    assert manager.discovery_truncated is True
    messages = [record.getMessage() for record in caplog.records]
    assert any("excluded from checks" in msg for msg in messages)
    assert any("File budget" in msg for msg in messages)


def test_manager_negative_budget_raises(tmp_path):
    manager = _manager_with_tree(tmp_path, file_budget=-1)
    with pytest.raises(exceptions.ExecutionError, match="file-budget"):
        manager.start()


def test_manager_jobs_capped_to_budgeted_file_count(tmp_path):
    manager = _manager_with_tree(tmp_path, file_budget=1)
    assert manager.jobs == 4
    manager.start()
    assert manager.jobs == 1
    assert len(manager.filenames) == 1
