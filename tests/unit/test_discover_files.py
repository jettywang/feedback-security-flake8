from __future__ import annotations

import os.path

import pytest

import flake8.discover_files as discover_files
from flake8 import utils
from flake8.discover_files import _filenames_from
from flake8.discover_files import discover
from flake8.discover_files import expand_paths


@pytest.fixture
def files_dir(tmpdir):
    """Create test dir for testing filenames_from."""
    with tmpdir.as_cwd():
        tmpdir.join("a/b/c.py").ensure()
        tmpdir.join("a/b/d.py").ensure()
        tmpdir.join("a/b/e/f.py").ensure()
        yield tmpdir


def _noop(path):
    return False


def _normpath(s):
    return s.replace("/", os.sep)


def _normpaths(pths):
    return {_normpath(pth) for pth in pths}


@pytest.mark.usefixtures("files_dir")
def test_filenames_from_a_directory():
    """Verify that filenames_from walks a directory."""
    filenames = set(_filenames_from(_normpath("a/b/"), predicate=_noop))
    # should include all files
    expected = _normpaths(("a/b/c.py", "a/b/d.py", "a/b/e/f.py"))
    assert filenames == expected


@pytest.mark.usefixtures("files_dir")
def test_filenames_from_a_directory_with_a_predicate():
    """Verify that predicates filter filenames_from."""
    filenames = set(
        _filenames_from(
            arg=_normpath("a/b/"),
            predicate=lambda path: path.endswith(_normpath("b/c.py")),
        ),
    )
    # should not include c.py
    expected = _normpaths(("a/b/d.py", "a/b/e/f.py"))
    assert filenames == expected


@pytest.mark.usefixtures("files_dir")
def test_filenames_from_a_directory_with_a_predicate_from_the_current_dir():
    """Verify that predicates filter filenames_from."""
    filenames = set(
        _filenames_from(
            arg=_normpath("./a/b"),
            predicate=lambda path: path == "c.py",
        ),
    )
    # none should have matched the predicate so all returned
    expected = _normpaths(("./a/b/c.py", "./a/b/d.py", "./a/b/e/f.py"))
    assert filenames == expected


@pytest.mark.usefixtures("files_dir")
def test_filenames_from_a_single_file():
    """Verify that we simply yield that filename."""
    filenames = set(_filenames_from(_normpath("a/b/c.py"), predicate=_noop))
    assert filenames == {_normpath("a/b/c.py")}


def test_filenames_from_a_single_file_does_not_exist():
    """Verify that a passed filename which does not exist is returned back."""
    filenames = set(_filenames_from(_normpath("d/n/e.py"), predicate=_noop))
    assert filenames == {_normpath("d/n/e.py")}


def test_filenames_from_exclude_doesnt_exclude_directory_names(tmpdir):
    """Verify that we don't greedily exclude subdirs."""
    tmpdir.join("1/dont_return_me.py").ensure()
    tmpdir.join("2/1/return_me.py").ensure()
    exclude = [tmpdir.join("1").strpath]

    def predicate(pth):
        return utils.fnmatch(os.path.abspath(pth), exclude)

    with tmpdir.as_cwd():
        filenames = list(_filenames_from(".", predicate=predicate))
    assert filenames == [os.path.join(".", "2", "1", "return_me.py")]


def test_filenames_from_predicate_applies_to_initial_arg(tmp_path):
    """Test that the predicate is also applied to the passed argument."""
    fname = str(tmp_path.joinpath("f.py"))
    ret = tuple(_filenames_from(fname, predicate=lambda _: True))
    assert ret == ()


def test_filenames_from_predicate_applies_to_dirname(tmp_path):
    """Test that the predicate can filter whole directories."""
    a_dir = tmp_path.joinpath("a")
    a_dir.mkdir()
    a_dir.joinpath("b.py").touch()

    b_py = tmp_path.joinpath("b.py")
    b_py.touch()

    def predicate(p):
        # filter out the /a directory
        return p.endswith("a")

    ret = tuple(_filenames_from(str(tmp_path), predicate=predicate))
    assert ret == (str(b_py),)


def _expand_paths(
    *,
    paths=(".",),
    stdin_display_name="stdin",
    filename_patterns=("*.py",),
    exclude=(),
):
    return set(
        expand_paths(
            paths=paths,
            stdin_display_name=stdin_display_name,
            filename_patterns=filename_patterns,
            exclude=exclude,
        ),
    )


@pytest.mark.usefixtures("files_dir")
def test_expand_paths_honors_exclude():
    expected = _normpaths(("./a/b/c.py", "./a/b/e/f.py"))
    assert _expand_paths(exclude=["d.py"]) == expected


@pytest.mark.usefixtures("files_dir")
def test_expand_paths_defaults_to_dot():
    expected = _normpaths(("./a/b/c.py", "./a/b/d.py", "./a/b/e/f.py"))
    assert _expand_paths(paths=()) == expected


def test_default_stdin_name_is_not_filtered():
    assert _expand_paths(paths=("-",)) == {"-"}


def test_alternate_stdin_name_is_filtered():
    ret = _expand_paths(
        paths=("-",),
        stdin_display_name="wat",
        exclude=("wat",),
    )
    assert ret == set()


def test_filename_included_even_if_not_matching_include(tmp_path):
    some_file = str(tmp_path.joinpath("some/file"))
    assert _expand_paths(paths=(some_file,)) == {some_file}


def _discover(**kwargs):
    kwargs.setdefault("paths", (".",))
    kwargs.setdefault("stdin_display_name", "stdin")
    kwargs.setdefault("filename_patterns", ("*.py",))
    kwargs.setdefault("exclude", ())
    return discover(**kwargs)


@pytest.mark.usefixtures("files_dir")
def test_discover_without_budget_matches_expand_paths():
    discovered = _discover()
    assert set(discovered.files) == _normpaths(
        ("./a/b/c.py", "./a/b/d.py", "./a/b/e/f.py"),
    )
    assert discovered.file_budget is None
    assert discovered.truncated is False
    assert discovered.excluded == ()


@pytest.mark.usefixtures("files_dir")
def test_discover_budget_truncates_and_preserves_order():
    full = _discover().files
    budgeted = _discover(file_budget=2)

    assert len(budgeted.files) == 2
    # the budgeted set is an ordered prefix of the full discovery
    assert budgeted.files == full[:2]
    assert budgeted.file_budget == 2
    assert budgeted.truncated is True


@pytest.mark.usefixtures("files_dir")
def test_discover_budget_larger_than_files_is_not_truncated():
    discovered = _discover(file_budget=100)
    assert discovered.truncated is False
    assert len(discovered.files) == 3
    assert discovered.file_budget == 100


@pytest.mark.usefixtures("files_dir")
def test_discover_budget_zero_truncates_immediately():
    discovered = _discover(file_budget=0)
    assert discovered.files == ()
    assert discovered.truncated is True
    assert discovered.file_budget == 0


@pytest.mark.usefixtures("files_dir")
def test_discover_records_excluded_paths_separately():
    discovered = _discover(exclude=["d.py"])
    assert _normpath("./a/b/d.py") in discovered.excluded
    assert all(not file.endswith("d.py") for file in discovered.files)
    # exclusion alone must never be flagged as a budget truncation
    assert discovered.truncated is False


@pytest.mark.usefixtures("files_dir")
def test_discover_exclusion_and_truncation_are_distinguishable():
    discovered = _discover(exclude=["d.py"], file_budget=1)
    assert discovered.excluded
    assert discovered.truncated is True
    assert len(discovered.files) == 1


@pytest.mark.usefixtures("files_dir")
def test_discover_stops_walking_once_budget_is_reached():
    """A reached budget closes the walker instead of walking the tree."""
    closed = []
    original = discover_files._filenames_from

    class TrackingGenerator:
        def __init__(self, inner):
            self.inner = inner

        def __iter__(self):
            return self

        def __next__(self):
            return next(self.inner)

        def close(self):
            closed.append(True)
            self.inner.close()

    def tracking(*args, **kwargs):
        return TrackingGenerator(original(*args, **kwargs))

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(discover_files, "_filenames_from", tracking)
        discovered = _discover(file_budget=1)

    assert len(discovered.files) == 1
    assert discovered.truncated is True
    assert closed == [True]


def test_discover_does_not_follow_symlinked_directories(tmp_path):
    outside = tmp_path.joinpath("outside")
    outside.mkdir()
    outside.joinpath("escape.py").write_text("import os\n")

    root = tmp_path.joinpath("root")
    root.mkdir()
    root.joinpath("inside.py").write_text("import sys\n")
    root.joinpath("link").symlink_to(outside, target_is_directory=True)

    discovered = _discover(paths=(str(root),))
    files = set(discovered.files)
    assert str(root.joinpath("inside.py")) in files
    assert not any("escape.py" in file for file in files)


def test_discover_always_includes_stdin():
    discovered = _discover(paths=("-",))
    assert discovered.files == ("-",)
    assert discovered.truncated is False


def test_discover_stdin_counts_toward_budget():
    discovered = _discover(paths=("-",), file_budget=0)
    assert discovered.files == ()
    assert discovered.truncated is True


def test_discover_explicit_file_counts_toward_budget(tmp_path):
    first = tmp_path.joinpath("a.py")
    second = tmp_path.joinpath("b.py")
    first.write_text("")
    second.write_text("")

    discovered = _discover(paths=(str(first), str(second)), file_budget=1)
    assert discovered.files == (str(first),)
    assert discovered.truncated is True
