"""Metadata-only preflight checklist for flake8 plugin upgrades.

CI uses this before a plugin upgrade is allowed to run: it discovers
plugins through flake8's own discovery flow (``flake8.plugins.finder``)
but only ever reads packaging metadata -- nothing is imported.  The
result is checked against an approved manifest (distribution name,
exact version, and expected entry-point names) and only the compliant
plugins are cleared for actual loading via :func:`load_approved_plugins`.

Manifest format (JSON)::

    {
      "plugins": [
        {
          "dist": "flake8-bandit",
          "version": "1.7.9",
          "extensions": ["S"],
          "reports": []
        }
      ]
    }

``extensions`` lists approved ``flake8.extension`` entry-point names and
``reports`` lists approved ``flake8.report`` entry-point names; both
default to empty.  Note that flake8 attributes its built-in ``F`` codes
to the ``pyflakes`` distribution and ``E``/``W`` to ``pycodestyle``, so a
strict manifest should list those distributions too.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import pathlib
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from typing import NamedTuple
from unittest import mock

from flake8 import utils
from flake8.exceptions import ExecutionError
from flake8.plugins import finder


class ApprovedPlugin(NamedTuple):
    """A single approved distribution from the manifest."""

    dist: str
    version: str
    extensions: frozenset[str]
    reports: frozenset[str]


@dataclass(frozen=True)
class DistCheck:
    """Checklist outcome for one discovered (or approved) distribution."""

    dist: str
    status: str
    approved_version: str | None = None
    installed_version: str | None = None
    missing_entries: tuple[str, ...] = ()
    unexpected_entries: tuple[str, ...] = ()


@dataclass(frozen=True)
class Collision:
    """An entry-point name provided by more than one distribution."""

    group: str
    name: str
    providers: tuple[str, ...]


@dataclass
class PreflightReport:
    """The full checklist plus the plugins cleared for loading."""

    checks: list[DistCheck]
    collisions: list[Collision]
    loadable: list[finder.Plugin] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every check passed and no collisions were found."""
        return not self.collisions and all(
            check.status == "ok" for check in self.checks
        )


def load_manifest(path: str) -> list[ApprovedPlugin]:
    """Load the approved-plugin manifest."""
    raw = json.loads(pathlib.Path(path).read_text())
    return [
        ApprovedPlugin(
            dist=utils.normalize_pypi_name(item["dist"]),
            version=item["version"],
            extensions=frozenset(item.get("extensions", ())),
            reports=frozenset(item.get("reports", ())),
        )
        for item in raw["plugins"]
    ]


def _scoped_metadata(path):
    """Return ``distributions``/``distribution`` lookups scoped to `path`."""
    dists = list(importlib.metadata.distributions(path=[str(path)]))
    by_name = {}
    for dist in dists:
        name = dist.metadata.get("name")
        if name is not None:
            by_name[utils.normalize_pypi_name(name)] = dist

    def distributions(**kwargs):
        return iter(dists)

    def distribution(name):
        try:
            return by_name[utils.normalize_pypi_name(name)]
        except KeyError:
            raise importlib.metadata.PackageNotFoundError(name)

    return distributions, distribution


def discover_plugins(path: str | None = None) -> list[finder.Plugin]:
    """Discover plugins via flake8's finder, reading metadata only.

    When ``path`` is given, discovery is scoped to the ``*.dist-info``
    metadata found in that directory instead of the current environment
    (used to rehearse an upgrade against staged metadata).
    """
    if path is None:
        return sorted(finder._find_importlib_plugins())

    distributions, distribution = _scoped_metadata(path)
    with mock.patch.object(
        importlib.metadata, "distributions", distributions,
    ), mock.patch.object(
        importlib.metadata, "distribution", distribution,
    ):
        return sorted(finder._find_importlib_plugins())


def _check_approved(
    approved_plugin: ApprovedPlugin,
    found: list[finder.Plugin] | None,
    loadable: list[finder.Plugin],
) -> DistCheck:
    """Check one approved distribution against what was discovered."""
    if not found:
        return DistCheck(
            approved_plugin.dist,
            "missing-dist",
            approved_version=approved_plugin.version,
        )

    versions = {plugin.version for plugin in found}
    installed = ", ".join(sorted(versions))
    if versions != {approved_plugin.version}:
        return DistCheck(
            approved_plugin.dist,
            "version-mismatch",
            approved_version=approved_plugin.version,
            installed_version=installed,
        )

    approved_names = {
        "flake8.extension": approved_plugin.extensions,
        "flake8.report": approved_plugin.reports,
    }
    seen = set()
    missing = []
    unexpected = []
    for plugin in found:
        entry = plugin.entry_point
        seen.add((entry.group, entry.name))
        if entry.name in approved_names[entry.group]:
            loadable.append(plugin)
        else:
            unexpected.append(f"{entry.group}:{entry.name}")
    for group, names in approved_names.items():
        for name in sorted(names):
            if (group, name) not in seen:
                missing.append(f"{group}:{name}")

    status = "ok" if not missing and not unexpected else "entry-mismatch"
    return DistCheck(
        approved_plugin.dist,
        status,
        approved_version=approved_plugin.version,
        installed_version=installed,
        missing_entries=tuple(sorted(missing)),
        unexpected_entries=tuple(sorted(unexpected)),
    )


def _check_unexpected(dist: str, found: list[finder.Plugin]) -> DistCheck:
    """Report a discovered distribution that is not in the manifest."""
    return DistCheck(
        dist,
        "unexpected-dist",
        installed_version=", ".join(
            sorted({plugin.version for plugin in found}),
        ),
        unexpected_entries=tuple(
            sorted(
                f"{p.entry_point.group}:{p.entry_point.name}" for p in found
            ),
        ),
    )


def preflight(
    plugins: list[finder.Plugin],
    approved: list[ApprovedPlugin],
) -> PreflightReport:
    """Check discovered plugins against the approved manifest."""
    by_dist: dict[str, list[finder.Plugin]] = defaultdict(list)
    for plugin in plugins:
        by_dist[utils.normalize_pypi_name(plugin.package)].append(plugin)

    checks = []
    loadable = []

    for approved_plugin in sorted(approved, key=lambda a: a.dist):
        checks.append(
            _check_approved(
                approved_plugin,
                by_dist.get(approved_plugin.dist),
                loadable,
            ),
        )

    approved_dists = {a.dist for a in approved}
    for dist in sorted(set(by_dist) - approved_dists):
        checks.append(_check_unexpected(dist, by_dist[dist]))

    by_entry: dict[tuple[str, str], set[str]] = defaultdict(set)
    for plugin in plugins:
        entry = plugin.entry_point
        by_entry[(entry.group, entry.name)].add(
            utils.normalize_pypi_name(plugin.package),
        )
    collisions = [
        Collision(group, name, tuple(sorted(providers)))
        for (group, name), providers in sorted(by_entry.items())
        if len(providers) > 1
    ]

    loadable.sort()
    return PreflightReport(checks, collisions, loadable)


def load_approved_plugins(
    report: PreflightReport,
    opts: finder.PluginOptions,
) -> finder.Plugins:
    """Load only preflight-cleared plugins.

    Refuses to load anything at all unless the checklist is fully clean,
    so a failed preflight can never leak a plugin into a real run.
    """
    if not report.ok:
        raise ExecutionError(
            "plugin preflight failed; refusing to load any plugins",
        )
    return finder.load_plugins(report.loadable, opts)


def render(report: PreflightReport) -> str:
    """Render the checklist in a human-readable form."""
    lines = ["== flake8 plugin preflight checklist =="]
    for check in report.checks:
        line = f"[{check.status}] {check.dist}"
        if check.installed_version is not None:
            line += f" installed={check.installed_version}"
        if check.approved_version is not None:
            line += f" approved={check.approved_version}"
        if check.missing_entries:
            line += f" missing: {', '.join(check.missing_entries)}"
        if check.unexpected_entries:
            line += f" unexpected: {', '.join(check.unexpected_entries)}"
        lines.append(line)
    for collision in report.collisions:
        lines.append(
            f"[collision] {collision.group}:{collision.name} provided by: "
            f"{', '.join(collision.providers)}",
        )
    verdict = "OK" if report.ok else "FAIL"
    lines.append(
        f"result: {verdict}; "
        f"{len(report.loadable)} plugin(s) cleared for loading",
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the preflight checklist; exit 0 only when fully approved."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--path",
        default=None,
        help="directory of staged *.dist-info metadata to inspect "
        "instead of the current environment",
    )
    args = parser.parse_args(argv)

    approved = load_manifest(args.manifest)
    plugins = discover_plugins(args.path)
    report = preflight(plugins, approved)
    print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
