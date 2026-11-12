"""Integration tests for the legacy api."""
from __future__ import annotations

from flake8.api import legacy


def test_legacy_api(tmpdir):
    """A basic end-to-end test for the legacy api reporting errors."""
    with tmpdir.as_cwd():
        t_py = tmpdir.join("t.py")
        t_py.write("import os  # unused import\n")

        style_guide = legacy.get_style_guide()
        report = style_guide.check_files([t_py.strpath])
        assert report.total_errors == 1


def test_legacy_api_kwargs_cannot_restrict_plugin_loading(tmpdir):
    """Plugin discovery happens before kwargs are applied.

    The restricted mode is a caller-controlled command-line boundary, so
    mutating the parsed options afterwards must not pretend to have restricted
    plugin discovery: a local plugin declared in the project is already
    imported by the time ``get_style_guide`` returns.
    """
    package_dir = tmpdir.mkdir("legacymarkerpkg")
    package_dir.join("__init__.py").write("")
    marker = tmpdir.join("imported.marker")
    package_dir.join("plugin.py").write(
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
    tmpdir.join("tox.ini").write(
        "[flake8:local-plugins]\n"
        "extension =\n    ABC = legacymarkerpkg.plugin:MarkerPlugin\n"
        "paths =\n    .\n",
    )
    tmpdir.join("t.py").write("x = 1\n")

    with tmpdir.as_cwd():
        legacy.get_style_guide(no_local_plugins=True)

    assert marker.exists()
