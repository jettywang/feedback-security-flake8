"""Tests for the metadata-only plugin preflight checklist tool."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

from flake8.exceptions import ExecutionError
from flake8.exceptions import FailedToLoadPlugin
from flake8.plugins import finder

TOOL_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "tools"
    / "plugin_preflight.py"
)
_spec = importlib.util.spec_from_file_location("plugin_preflight", TOOL_PATH)
plugin_preflight = importlib.util.module_from_spec(_spec)
sys.modules["plugin_preflight"] = plugin_preflight
_spec.loader.exec_module(plugin_preflight)


def sample_tree_plugin(tree):
    """Importable target for simulated extension entry points."""


def _write_dist(path, name, version, eps=()):
    """Write a minimal *.dist-info directory with the given metadata."""
    dist_info = path / f"{name.replace('-', '_')}.dist-info"
    dist_info.mkdir()
    dist_info.joinpath("METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
    )
    if eps:
        lines = []
        for group, entries in eps.items():
            lines.append(f"[{group}]")
            lines.extend(f"{ep_name} = {value}" for ep_name, value in entries)
        dist_info.joinpath("entry_points.txt").write_text("\n".join(lines))


@pytest.fixture
def core_dists(tmp_path):
    """Simulated pyflakes / pycodestyle / flake8 distributions."""
    _write_dist(tmp_path, "pyflakes", "9000.1.0")
    _write_dist(tmp_path, "pycodestyle", "9000.2.0")
    _write_dist(
        tmp_path,
        "flake8",
        "9001",
        eps={
            "flake8.extension": (
                ("F", "flake8.plugins.pyflakes:FlakesChecker"),
                ("E", "flake8.plugins.pycodestyle:pycodestyle_logical"),
                ("W", "flake8.plugins.pycodestyle:pycodestyle_physical"),
            ),
            "flake8.report": (
                ("default", "flake8.formatting.default:Default"),
                ("pylint", "flake8.formatting.default:Pylint"),
            ),
        },
    )
    return tmp_path


def _core_manifest():
    return [
        {"dist": "pyflakes", "version": "9000.1.0", "extensions": ["F"]},
        {
            "dist": "pycodestyle",
            "version": "9000.2.0",
            "extensions": ["E", "W"],
        },
        {
            "dist": "flake8",
            "version": "9001",
            "reports": ["default", "pylint"],
        },
    ]


def _manifest(tmp_path, plugins):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"plugins": plugins}))
    return plugin_preflight.load_manifest(str(manifest))


def _check(report, dist):
    (check,) = [c for c in report.checks if c.dist == dist]
    return check


def test_normal_upgrade_is_cleared_for_loading(core_dists):
    _write_dist(
        core_dists,
        "flake8-bandit",
        "1.7.9",
        eps={
            "flake8.extension": (
                ("S", "tests.unit.test_plugin_preflight:sample_tree_plugin"),
            ),
        },
    )
    approved = _manifest(
        core_dists,
        [
            *_core_manifest(),
            {"dist": "flake8-bandit", "version": "1.7.9", "extensions": ["S"]},
        ],
    )

    plugins = plugin_preflight.discover_plugins(str(core_dists))
    report = plugin_preflight.preflight(plugins, approved)

    assert report.ok
    assert _check(report, "flake8-bandit").status == "ok"
    assert {p.entry_point.name for p in report.loadable} == {
        "F", "E", "W", "S", "default", "pylint",
    }

    # everything cleared by the preflight loads and classifies cleanly
    loaded = plugin_preflight.load_approved_plugins(
        report, finder.PluginOptions.blank(),
    )
    assert {p.entry_name for p in loaded.checkers.tree} == {"F", "S"}


def test_missing_distribution_is_blocked(core_dists):
    approved = _manifest(
        core_dists,
        [
            *_core_manifest(),
            {"dist": "flake8-bandit", "version": "1.7.9", "extensions": ["S"]},
        ],
    )

    plugins = plugin_preflight.discover_plugins(str(core_dists))
    report = plugin_preflight.preflight(plugins, approved)

    assert not report.ok
    check = _check(report, "flake8-bandit")
    assert check.status == "missing-dist"
    assert check.approved_version == "1.7.9"
    assert not [p for p in report.loadable if p.package == "flake8-bandit"]

    with pytest.raises(ExecutionError):
        plugin_preflight.load_approved_plugins(
            report, finder.PluginOptions.blank(),
        )


def test_same_named_entry_from_other_distribution_is_blocked(core_dists):
    target = "tests.unit.test_plugin_preflight:sample_tree_plugin"
    _write_dist(
        core_dists,
        "flake8-sec-a",
        "2.0.0",
        eps={"flake8.extension": (("SEC", target),)},
    )
    # an unapproved distribution shadows the same entry-point name; its
    # entry point is deliberately unimportable to prove it never loads
    _write_dist(
        core_dists,
        "flake8-sec-evil",
        "9.9.9",
        eps={"flake8.extension": (("SEC", "dne:dne"),)},
    )
    approved = _manifest(
        core_dists,
        [
            *_core_manifest(),
            {
                "dist": "flake8-sec-a",
                "version": "2.0.0",
                "extensions": ["SEC"],
            },
        ],
    )

    plugins = plugin_preflight.discover_plugins(str(core_dists))
    report = plugin_preflight.preflight(plugins, approved)

    assert not report.ok
    assert _check(report, "flake8-sec-a").status == "ok"
    assert _check(report, "flake8-sec-evil").status == "unexpected-dist"
    (collision,) = report.collisions
    assert collision.group == "flake8.extension"
    assert collision.name == "SEC"
    assert collision.providers == ("flake8-sec-a", "flake8-sec-evil")

    # only the approved distribution's SEC entry is cleared
    sec_entries = [p for p in report.loadable if p.entry_point.name == "SEC"]
    assert [p.package for p in sec_entries] == ["flake8-sec-a"]

    # the cleared subset loads fine -- the shadowing entry never executes
    finder.load_plugins(report.loadable, finder.PluginOptions.blank())
    # whereas loading everything discovered would blow up on `dne:dne`
    with pytest.raises(FailedToLoadPlugin):
        finder.load_plugins(plugins, finder.PluginOptions.blank())
    # and the gate refuses to load anything while the checklist is dirty
    with pytest.raises(ExecutionError):
        plugin_preflight.load_approved_plugins(
            report, finder.PluginOptions.blank(),
        )


def test_unapproved_version_is_blocked(core_dists):
    _write_dist(
        core_dists,
        "flake8-bandit",
        "1.8.0",
        eps={
            "flake8.extension": (
                ("S", "tests.unit.test_plugin_preflight:sample_tree_plugin"),
            ),
        },
    )
    approved = _manifest(
        core_dists,
        [
            *_core_manifest(),
            {"dist": "flake8-bandit", "version": "1.7.9", "extensions": ["S"]},
        ],
    )

    plugins = plugin_preflight.discover_plugins(str(core_dists))
    report = plugin_preflight.preflight(plugins, approved)

    assert not report.ok
    check = _check(report, "flake8-bandit")
    assert check.status == "version-mismatch"
    assert check.installed_version == "1.8.0"
    assert not [p for p in report.loadable if p.package == "flake8-bandit"]


def test_entry_mismatch_is_blocked(core_dists):
    _write_dist(
        core_dists,
        "flake8-bandit",
        "1.7.9",
        eps={
            "flake8.extension": (
                ("S", "tests.unit.test_plugin_preflight:sample_tree_plugin"),
                ("XX", "tests.unit.test_plugin_preflight:sample_tree_plugin"),
            ),
        },
    )
    approved = _manifest(
        core_dists,
        [
            *_core_manifest(),
            {
                "dist": "flake8-bandit",
                "version": "1.7.9",
                "extensions": ["S", "Q"],
            },
        ],
    )

    plugins = plugin_preflight.discover_plugins(str(core_dists))
    report = plugin_preflight.preflight(plugins, approved)

    assert not report.ok
    check = _check(report, "flake8-bandit")
    assert check.status == "entry-mismatch"
    assert check.missing_entries == ("flake8.extension:Q",)
    assert check.unexpected_entries == ("flake8.extension:XX",)
    # the approved entry is still cleared; the unexpected one is not
    bandit_entries = [
        p.entry_point.name for p in report.loadable
        if p.package == "flake8-bandit"
    ]
    assert bandit_entries == ["S"]


def test_cli_reports_checklist(core_dists, capsys):
    manifest = core_dists / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "plugins": [
                    *_core_manifest(),
                    {
                        "dist": "flake8-bandit",
                        "version": "1.7.9",
                        "extensions": ["S"],
                    },
                ],
            },
        ),
    )

    ret = plugin_preflight.main(
        ["--manifest", str(manifest), "--path", str(core_dists)],
    )

    assert ret == 1
    out = capsys.readouterr().out
    assert "[missing-dist] flake8-bandit" in out
    assert "result: FAIL" in out
