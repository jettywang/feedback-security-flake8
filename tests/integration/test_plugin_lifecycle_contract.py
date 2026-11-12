"""Contract tests for the plugin lifecycle as driven by ``Application``.

These tests pin down the *current* behaviour of plugin discovery, import,
classification and option registration so that a future review / audit service
which restricts which plugins may load has a precise baseline to build its
policy on.  The key guarantees exercised here are:

1. ``--help`` is not an early exit: the full discover -> import -> classify ->
   register pipeline runs before argparse prints help.
2. discovery always sees every installed entry point and every local plugin,
   including ``off_by_default`` ones.
3. every discovered plugin module is imported (entry point ``.load()``) even
   when the plugin ends up disabled, so an unloadable plugin breaks ``--help``
   exactly as it breaks a normal check.
4. only *enabled* plugins participate in option registration
   (``add_options`` / ``parse_options``), default selection and checking.
"""
from __future__ import annotations

import pytest

from flake8.exceptions import FailedToLoadPlugin
from flake8.main.cli import main
from flake8.options import config
from flake8.plugins import finder


class EnabledCheck:
    """Enabled-by-default local extension plugin."""

    add_options_calls: list[str] = []
    parse_options_calls: list[str] = []

    def __init__(self, tree):
        self.tree = tree

    def run(self):
        yield 1, 0, "LE100 enabled check fired", type(self)

    @classmethod
    def add_options(cls, parser):
        cls.add_options_calls.append("LE")
        parser.add_option("--local-enabled-opt")

    @classmethod
    def parse_options(cls, options):
        cls.parse_options_calls.append("LE")


class DisabledCheck(EnabledCheck):
    """Plugin which must be opted in via ``--enable-extensions``."""

    off_by_default = True
    add_options_calls: list[str] = []
    parse_options_calls: list[str] = []

    def run(self):
        yield 1, 0, "LD100 disabled check fired", type(self)

    @classmethod
    def add_options(cls, parser):
        cls.add_options_calls.append("LD")
        parser.add_option("--local-disabled-opt")

    @classmethod
    def parse_options(cls, options):
        cls.parse_options_calls.append("LD")


def physical_check(physical_line):
    yield 0, "LP100 physical line check"


class LocalReporter:
    """A local ``flake8.report`` plugin."""

    def __init__(self, options):
        self.options = options


@pytest.fixture
def local_plugin_config(tmp_path):
    cfg_s = f"""\
[flake8:local-plugins]
paths = {tmp_path}
extension =
    LE = {EnabledCheck.__module__}:{EnabledCheck.__name__}
    LD = {DisabledCheck.__module__}:{DisabledCheck.__name__}
    LP = {physical_check.__module__}:{physical_check.__name__}
report =
    LR = {LocalReporter.__module__}:{LocalReporter.__name__}
"""
    cfg = tmp_path.joinpath("tox.ini")
    cfg.write_text(cfg_s)
    EnabledCheck.add_options_calls.clear()
    EnabledCheck.parse_options_calls.clear()
    DisabledCheck.add_options_calls.clear()
    DisabledCheck.parse_options_calls.clear()
    return str(cfg)


def _loaded_plugins(cfg_path):
    cfg, cfg_dir = config.load_config(cfg_path, [], isolated=False)
    opts = finder.parse_plugin_options(
        cfg,
        cfg_dir,
        enable_extensions=None,
        require_plugins=None,
    )
    raw = finder.find_plugins(cfg, opts)
    return raw, finder.load_plugins(raw, opts)


def test_discovery_includes_disabled_and_local_plugins(local_plugin_config):
    """Contract: discovery lists all entry points before any enablement."""
    raw, plugins = _loaded_plugins(local_plugin_config)

    discovered = {plugin.entry_point.name for plugin in raw}
    # the off-by-default LD plugin is discovered just like any other plugin
    assert {"LE", "LD", "LP", "LR"} <= discovered

    assert {p.entry_name for p in plugins.disabled} == {"LD"}
    assert {p.entry_name for p in plugins.checkers.tree} == {"LE", "C90", "F"}
    physical = {p.entry_name for p in plugins.checkers.physical_line}
    assert physical == {"LP", "W"}
    assert "LR" in plugins.reporters


def test_disabled_plugin_is_still_imported(local_plugin_config, monkeypatch):
    """Contract: import happens for every plugin, even disabled ones."""
    imported: list[str] = []
    orig_load = finder._load_plugin

    def spy(plugin):
        imported.append(plugin.entry_point.name)
        return orig_load(plugin)

    monkeypatch.setattr(finder, "_load_plugin", spy)

    cfg, cfg_dir = config.load_config(local_plugin_config, [], isolated=False)
    opts = finder.parse_plugin_options(
        cfg, cfg_dir, enable_extensions=None, require_plugins=None,
    )
    raw = finder.find_plugins(cfg, opts)
    finder.load_plugins(raw, opts)

    assert "LD" in imported
    assert {"LE", "LD", "LP", "LR"} <= set(imported)


def test_enabled_plugins_are_registered_but_disabled_one_is_not(
    local_plugin_config,
):
    """Contract: add_options only runs for enabled plugins."""
    cfg_path = local_plugin_config
    argv = ["--config", cfg_path]

    with pytest.raises(SystemExit) as excinfo:
        main([*argv, "--help"])
    assert excinfo.value.args[0] == 0

    assert EnabledCheck.add_options_calls == ["LE"]
    # LD stays disabled: its option group must not be registered
    assert DisabledCheck.add_options_calls == []


def test_help_runs_full_pipeline_and_then_exits_zero(
    local_plugin_config, capsys,
):
    """Contract: --help exits 0 after loading every plugin."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--help", "--config", local_plugin_config])
    assert excinfo.value.args[0] == 0

    out, err = capsys.readouterr()
    assert out.startswith("usage: flake8 [options] file file ...\n")
    # enabled plugin option is documented; disabled plugin option is not
    assert "--local-enabled-opt" in out
    assert "--local-disabled-opt" not in out
    # the local reporter is advertised in the --format choices
    assert "LR" in out
    assert err == ""


def test_help_includes_disabled_plugin_options_after_opt_in(
    local_plugin_config, capsys,
):
    """Contract: --enable-extensions moves LD into the registered set."""
    with pytest.raises(SystemExit):
        main(
            [
                "--help",
                "--config",
                local_plugin_config,
                "--enable-extensions=LD",
            ],
        )

    out, _ = capsys.readouterr()
    assert "--local-disabled-opt" in out
    assert "--local-enabled-opt" in out


def test_normal_check_skips_disabled_plugin(
    local_plugin_config, tmp_path, capsys,
):
    """Contract: a normal run never executes the disabled plugin."""
    t_py = tmp_path.joinpath("t.py")
    t_py.write_text("import os\n")

    rc = main([str(t_py), "--config", local_plugin_config])
    assert rc == 1

    out, err = capsys.readouterr()
    assert "LE100" in out
    assert "LD100" not in out
    assert err == ""
    assert EnabledCheck.parse_options_calls == ["LE"]


def test_normal_check_runs_disabled_plugin_after_opt_in(
    local_plugin_config, tmp_path, capsys,
):
    """Contract: explicit enablement makes the disabled plugin check."""
    t_py = tmp_path.joinpath("t.py")
    t_py.write_text("import os\n")

    rc = main(
        [
            str(t_py),
            "--config",
            local_plugin_config,
            "--enable-extensions=LD",
        ],
    )
    assert rc == 1

    out, _ = capsys.readouterr()
    assert "LD100" in out
    assert "LE100" in out
    assert EnabledCheck.parse_options_calls == ["LE"]
    assert DisabledCheck.parse_options_calls == ["LD"]


def test_disabled_plugin_option_is_rejected_on_normal_run(
    local_plugin_config, tmp_path, capsys,
):
    """Contract: unregistered options of disabled plugins fail parsing."""
    t_py = tmp_path.joinpath("t.py")
    t_py.write_text("print('hello')\n")

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                str(t_py),
                "--config",
                local_plugin_config,
                "--local-disabled-opt=x",
            ],
        )
    assert excinfo.value.args[0] == 2

    _, err = capsys.readouterr()
    assert "unrecognized arguments: --local-disabled-opt=x" in err


def test_unloadable_plugin_breaks_help_and_check_equally(tmp_path, capsys):
    """Contract: import precedes help, so --help cannot mask a bad plugin."""
    cfg = tmp_path.joinpath("tox.ini")
    cfg.write_text(
        "[flake8:local-plugins]\n"
        "extension =\n    BZ = definitely_not_a_module_12345:Broken\n",
    )
    t_py = tmp_path.joinpath("t.py")
    t_py.write_text("print('hello')\n")

    with pytest.raises(FailedToLoadPlugin):
        main(["--help", "--config", str(cfg)])

    with pytest.raises(FailedToLoadPlugin):
        main([str(t_py), "--config", str(cfg)])
