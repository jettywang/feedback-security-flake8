"""Integration tests for plugin loading."""
from __future__ import annotations

import json
import sys

import pytest

from flake8.main.cli import main
from flake8.main.options import register_default_options
from flake8.main.options import stage1_arg_parser
from flake8.options import aggregator
from flake8.options import config
from flake8.options.manager import OptionManager
from flake8.options.parse_args import parse_args
from flake8.plugins import finder


class ExtensionTestPlugin:
    """Extension test plugin."""

    def __init__(self, tree):
        """Construct an instance of test plugin."""

    def run(self):
        """Do nothing."""

    @classmethod
    def add_options(cls, parser):
        """Register options."""
        parser.add_option("--anopt")


class ReportTestPlugin:
    """Report test plugin."""

    def __init__(self, tree):
        """Construct an instance of test plugin."""

    def run(self):
        """Do nothing."""


@pytest.fixture
def local_config(tmp_path):
    cfg_s = f"""\
[flake8:local-plugins]
extension =
    XE = {ExtensionTestPlugin.__module__}:{ExtensionTestPlugin.__name__}
report =
    XR = {ReportTestPlugin.__module__}:{ReportTestPlugin.__name__}
"""
    cfg = tmp_path.joinpath("tox.ini")
    cfg.write_text(cfg_s)

    return str(cfg)


def test_enable_local_plugin_from_config(local_config):
    """App can load a local plugin from config file."""
    cfg, cfg_dir = config.load_config(local_config, [], isolated=False)
    opts = finder.parse_plugin_options(
        cfg,
        cfg_dir,
        enable_extensions=None,
        require_plugins=None,
    )
    plugins = finder.find_plugins(cfg, opts)
    loaded_plugins = finder.load_plugins(plugins, opts)

    (custom_extension,) = (
        loaded
        for loaded in loaded_plugins.checkers.tree
        if loaded.entry_name == "XE"
    )
    custom_report = loaded_plugins.reporters["XR"]

    assert custom_extension.obj is ExtensionTestPlugin
    assert custom_report.obj is ReportTestPlugin


def test_local_plugin_can_add_option(local_config):
    """A local plugin can add a CLI option."""

    argv = ["--config", local_config, "--anopt", "foo"]

    stage1_parser = stage1_arg_parser()
    stage1_args, rest = stage1_parser.parse_known_args(argv)

    cfg, cfg_dir = config.load_config(
        config=stage1_args.config, extra=[], isolated=False,
    )

    opts = finder.parse_plugin_options(
        cfg,
        cfg_dir,
        enable_extensions=None,
        require_plugins=None,
    )
    plugins = finder.find_plugins(cfg, opts)
    loaded_plugins = finder.load_plugins(plugins, opts)

    option_manager = OptionManager(
        version="123",
        plugin_versions="",
        parents=[stage1_parser],
        formatter_names=[],
    )
    register_default_options(option_manager)
    option_manager.register_plugins(loaded_plugins)

    args = aggregator.aggregate_options(option_manager, cfg, cfg_dir, argv)

    assert args.extended_default_select == ["XE", "C90", "F", "E", "W"]
    assert args.anopt == "foo"


class AlwaysErrors:
    def __init__(self, tree):
        pass

    def run(self):
        yield 1, 0, "ABC123 error", type(self)


class AlwaysErrorsDisabled(AlwaysErrors):
    off_by_default = True


def test_plugin_gets_enabled_by_default(tmp_path, capsys):
    cfg_s = f"""\
[flake8:local-plugins]
extension =
    ABC = {AlwaysErrors.__module__}:{AlwaysErrors.__name__}
"""
    cfg = tmp_path.joinpath("tox.ini")
    cfg.write_text(cfg_s)

    t_py = tmp_path.joinpath("t.py")
    t_py.touch()

    assert main((str(t_py), "--config", str(cfg))) == 1
    out, err = capsys.readouterr()
    assert out == f"{t_py}:1:1: ABC123 error\n"
    assert err == ""


def test_plugin_off_by_default(tmp_path, capsys):
    cfg_s = f"""\
[flake8:local-plugins]
extension =
    ABC = {AlwaysErrorsDisabled.__module__}:{AlwaysErrorsDisabled.__name__}
"""
    cfg = tmp_path.joinpath("tox.ini")
    cfg.write_text(cfg_s)

    t_py = tmp_path.joinpath("t.py")
    t_py.touch()

    cmd = (str(t_py), "--config", str(cfg))

    assert main(cmd) == 0
    out, err = capsys.readouterr()
    assert out == err == ""

    assert main((*cmd, "--enable-extension=ABC")) == 1
    out, err = capsys.readouterr()
    assert out == f"{t_py}:1:1: ABC123 error\n"
    assert err == ""


def yields_physical_line(physical_line):
    yield 0, f"T001 {physical_line!r}"


def test_physical_line_plugin_multiline_string(tmpdir, capsys):
    cfg_s = f"""\
[flake8:local-plugins]
extension =
    T = {yields_physical_line.__module__}:{yields_physical_line.__name__}
"""

    cfg = tmpdir.join("tox.ini")
    cfg.write(cfg_s)

    src = '''\
x = "foo" + """
bar
"""
'''
    t_py = tmpdir.join("t.py")
    t_py.write_binary(src.encode())

    with tmpdir.as_cwd():
        assert main(("t.py", "--config", str(cfg))) == 1

    expected = '''\
t.py:1:1: T001 'x = "foo" + """\\n'
t.py:2:1: T001 'bar\\n'
t.py:3:1: T001 '"""\\n'
'''
    out, err = capsys.readouterr()
    assert out == expected


def test_physical_line_plugin_multiline_fstring(tmpdir, capsys):
    cfg_s = f"""\
[flake8:local-plugins]
extension =
    T = {yields_physical_line.__module__}:{yields_physical_line.__name__}
"""

    cfg = tmpdir.join("tox.ini")
    cfg.write(cfg_s)

    src = '''\
y = 1
x = f"""
hello {y}
"""
'''
    t_py = tmpdir.join("t.py")
    t_py.write_binary(src.encode())

    with tmpdir.as_cwd():
        assert main(("t.py", "--config", str(cfg))) == 1

    expected = '''\
t.py:1:1: T001 'y = 1\\n'
t.py:2:1: T001 'x = f"""\\n'
t.py:3:1: T001 'hello {y}\\n'
t.py:4:1: T001 '"""\\n'
'''
    out, err = capsys.readouterr()
    assert out == expected


def yields_logical_line(logical_line):
    yield 0, f"T001 {logical_line!r}"


def test_logical_line_plugin(tmpdir, capsys):
    cfg_s = f"""\
[flake8]
extend-ignore = F
[flake8:local-plugins]
extension =
    T = {yields_logical_line.__module__}:{yields_logical_line.__name__}
"""

    cfg = tmpdir.join("tox.ini")
    cfg.write(cfg_s)

    src = """\
f'hello world'
"""
    t_py = tmpdir.join("t.py")
    t_py.write_binary(src.encode())

    with tmpdir.as_cwd():
        assert main(("t.py", "--config", str(cfg))) == 1

    expected = """\
t.py:1:1: T001 "f'xxxxxxxxxxx'"
"""
    out, err = capsys.readouterr()
    assert out == expected


def test_escaping_of_fstrings_in_string_redacter(tmpdir, capsys):
    cfg_s = f"""\
[flake8]
extend-ignore = F
[flake8:local-plugins]
extension =
    T = {yields_logical_line.__module__}:{yields_logical_line.__name__}
"""

    cfg = tmpdir.join("tox.ini")
    cfg.write(cfg_s)

    src = """\
f'{{"{hello}": "{world}"}}'
"""
    t_py = tmpdir.join("t.py")
    t_py.write_binary(src.encode())

    with tmpdir.as_cwd():
        assert main(("t.py", "--config", str(cfg))) == 1

    if sys.version_info >= (3, 12):  # pragma: >=3.12 cover
        expected = """\
t.py:1:1: T001 "f'xxx{hello}xxxx{world}xxx'"
"""
    else:  # pragma: <3.12 cover
        expected = """\
t.py:1:1: T001 "f'xxxxxxxxxxxxxxxxxxxxxxxx'"
"""
    out, err = capsys.readouterr()
    assert out == expected


@pytest.mark.xfail(sys.version_info < (3, 14), reason="3.14+")
def test_tstring_logical_line(tmpdir, capsys):  # pragma: >=3.14 cover
    cfg_s = f"""\
[flake8]
extend-ignore = F
[flake8:local-plugins]
extension =
    T = {yields_logical_line.__module__}:{yields_logical_line.__name__}
"""

    cfg = tmpdir.join("tox.ini")
    cfg.write(cfg_s)

    src = """\
t'''
hello {world}
'''
t'{{"{hello}": "{world}"}}'
"""
    t_py = tmpdir.join("t.py")
    t_py.write_binary(src.encode())

    with tmpdir.as_cwd():
        assert main(("t.py", "--config", str(cfg))) == 1

    expected = """\
t.py:1:1: T001 "t'''xxxxxxx{world}x'''"
t.py:4:1: T001 "t'xxx{hello}xxxx{world}xxx'"
"""
    out, err = capsys.readouterr()
    assert out == expected


def _make_marker_project(tmp_path, module_name):
    """Create a temp project whose only plugin writes a marker on import.

    The plugin module records nothing except touching a marker file, so the
    marker distinguishes mere config reading from an actual plugin import.
    """
    marker = tmp_path / f"{module_name}.imported"
    project_dir = tmp_path / "project"
    # a unique top-level package name keeps these projects from colliding in
    # ``sys.modules`` when several tests run in one interpreter
    package_dir = project_dir / module_name
    package_dir.mkdir(parents=True)

    package_dir.joinpath("__init__.py").write_text("")
    package_dir.joinpath("plugin.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).touch()\n"
        "\n"
        "class MarkerPlugin:\n"
        "    def __init__(self, tree):\n"
        "        pass\n"
        "\n"
        "    def run(self):\n"
        "        return []\n",
    )

    cfg = project_dir / "tox.ini"
    cfg.write_text(
        "[flake8]\n"
        "extend-ignore = F\n"
        "[flake8:local-plugins]\n"
        f"extension =\n    ABC = {module_name}.plugin:MarkerPlugin\n"
        "paths =\n    .\n",
    )

    project_dir.joinpath("t.py").write_text("x = 1\n")
    return cfg, project_dir / "t.py", marker


def test_local_plugin_imported_without_restricted_mode(tmp_path):
    """Outside restricted mode the project's local plugin is imported."""
    cfg, t_py, marker = _make_marker_project(
        tmp_path, "marker_plugin_default",
    )

    assert main((str(t_py), "--config", str(cfg))) == 0
    assert marker.exists()


def test_no_local_plugins_skips_project_plugin_on_cli(tmp_path):
    """The CLI flag reads project config but never imports the plugin."""
    cfg, t_py, marker = _make_marker_project(
        tmp_path, "marker_plugin_cli",
    )

    assert main((str(t_py), "--config", str(cfg), "--no-local-plugins")) == 0
    assert not marker.exists()


def test_no_local_plugins_skips_project_plugin_in_api(tmp_path):
    """The same boundary is reachable through the regular API entrypoint."""
    cfg, t_py, marker = _make_marker_project(
        tmp_path, "marker_plugin_api",
    )

    plugins, opts = parse_args(
        (str(t_py), "--config", str(cfg), "--no-local-plugins"),
    )

    # ordinary project configuration was still read
    assert "F" in opts.extend_ignore
    # but no local plugin was discovered, let alone imported
    assert all(
        loaded.plugin.package != "local" for loaded in plugins.all_plugins()
    )
    assert not marker.exists()


def test_no_local_plugins_visible_in_bug_report(tmp_path, capsys):
    """The plugin diagnostics expose the same boundary."""
    cfg, t_py, marker = _make_marker_project(
        tmp_path, "marker_plugin_diagnostic",
    )

    with pytest.raises(SystemExit) as excinfo:
        main(
            (
                str(t_py),
                "--config",
                str(cfg),
                "--no-local-plugins",
                "--bug-report",
            ),
        )
    assert excinfo.value.args[0] == 0

    out, err = capsys.readouterr()
    info = json.loads(out)
    assert info["no_local_plugins"] is True
    assert all(
        entry["plugin"] != "local" for entry in info["plugins"]
    )
    assert err == ""
    assert not marker.exists()
