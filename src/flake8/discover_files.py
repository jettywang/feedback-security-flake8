"""Functions related to discovering paths."""
from __future__ import annotations

import logging
import os.path
from collections.abc import Callable
from collections.abc import Generator
from collections.abc import Sequence
from typing import NamedTuple

from flake8 import utils

LOG = logging.getLogger(__name__)


class DiscoveredFiles(NamedTuple):
    """Outcome of a budget-aware file discovery.

    :param files:
        The ordered collection of lintable files that should be checked.
    :param excluded:
        Paths which were skipped because they matched an exclusion pattern.
    :param file_budget:
        The budget the discovery was limited to, or ``None`` when no limit
        was in effect.
    :param truncated:
        ``True`` when discovery stopped early because ``file_budget`` was
        reached, otherwise ``False``.
    """

    files: tuple[str, ...]
    excluded: tuple[str, ...]
    file_budget: int | None
    truncated: bool


def _filenames_from(
    arg: str,
    *,
    predicate: Callable[[str], bool],
) -> Generator[str]:
    """Generate filenames from an argument.

    :param arg:
        Parameter from the command-line.
    :param predicate:
        Predicate to use to filter out filenames. If the predicate
        returns ``True`` we will exclude the filename, otherwise we
        will yield it. By default, we include every filename
        generated.
    :returns:
        Generator of paths
    """
    if predicate(arg):
        return

    if os.path.isdir(arg):
        for root, sub_directories, files in os.walk(arg):
            # NOTE(sigmavirus24): os.walk() will skip a directory if you
            # remove it from the list of sub-directories.
            for directory in tuple(sub_directories):
                joined = os.path.join(root, directory)
                if predicate(joined):
                    sub_directories.remove(directory)

            for filename in files:
                joined = os.path.join(root, filename)
                if not predicate(joined):
                    yield joined
    else:
        yield arg


def _make_is_excluded(
    *,
    stdin_display_name: str,
    exclude: Sequence[str],
    on_excluded: Callable[[str], None] | None = None,
) -> Callable[[str], bool]:
    """Build the exclusion predicate shared by the discovery entry points."""

    def is_excluded(arg: str) -> bool:
        if arg == "-":
            # if the stdin_display_name is the default, always include it
            if stdin_display_name == "stdin":
                return False
            arg = stdin_display_name

        excluded = utils.matches_filename(
            arg,
            patterns=exclude,
            log_message='"%(path)s" has %(whether)sbeen excluded',
            logger=LOG,
        )
        if excluded and on_excluded is not None:
            on_excluded(arg)
        return excluded

    return is_excluded


def expand_paths(
    *,
    paths: Sequence[str],
    stdin_display_name: str,
    filename_patterns: Sequence[str],
    exclude: Sequence[str],
) -> Generator[str]:
    """Expand out ``paths`` from commandline to the lintable files."""
    if not paths:
        paths = ["."]

    is_excluded = _make_is_excluded(
        stdin_display_name=stdin_display_name,
        exclude=exclude,
    )

    return (
        filename
        for path in paths
        for filename in _filenames_from(path, predicate=is_excluded)
        if (
            # always lint `-`
            filename == "-"
            # always lint explicitly passed (even if not matching filter)
            or path == filename
            # otherwise, check the file against filtered patterns
            or utils.fnmatch(filename, filename_patterns)
        )
    )


def discover(
    *,
    paths: Sequence[str],
    stdin_display_name: str,
    filename_patterns: Sequence[str],
    exclude: Sequence[str],
    file_budget: int | None = None,
) -> DiscoveredFiles:
    """Discover lintable files, honoring exclusions and an optional budget.

    This is the budget-aware counterpart of :func:`expand_paths`. Besides the
    ordered files to check it reports which paths were excluded and whether
    the budget truncated discovery, so callers can tell exclusion apart from
    budget limiting. When ``file_budget`` is reached the underlying walk is
    closed immediately instead of traversing the remainder of the tree.

    :param file_budget:
        Maximum number of lintable files to return. ``None`` means no limit.
    :returns:
        A :class:`DiscoveredFiles` describing the discovery outcome.
    """
    if not paths:
        paths = ["."]

    excluded: list[str] = []
    is_excluded = _make_is_excluded(
        stdin_display_name=stdin_display_name,
        exclude=exclude,
        on_excluded=excluded.append,
    )

    def is_lintable(path: str, filename: str) -> bool:
        return (
            # always lint `-`
            filename == "-"
            # always lint explicitly passed (even if not matching filter)
            or path == filename
            # otherwise, check the file against filtered patterns
            or utils.fnmatch(filename, filename_patterns)
        )

    files: list[str] = []
    truncated = False
    for path in paths:
        walker = _filenames_from(path, predicate=is_excluded)
        for filename in walker:
            if not is_lintable(path, filename):
                continue
            if file_budget is not None and len(files) >= file_budget:
                truncated = True
                walker.close()
                break
            files.append(filename)
        if truncated:
            break

    return DiscoveredFiles(
        files=tuple(files),
        excluded=tuple(excluded),
        file_budget=file_budget,
        truncated=truncated,
    )
