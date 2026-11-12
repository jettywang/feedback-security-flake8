"""Integration tests for the ``--require-codes`` option."""
from __future__ import annotations

from flake8.main.cli import main


class FakeSecurityChecker:
    """A fake checker plugin reporting two security codes."""

    def __init__(self, tree):
        """Construct an instance of the fake checker."""

    def run(self):
        """Always report two fake security violations."""
        yield 1, 0, "SEC100 fake security error", type(self)
        yield 2, 0, "SEC101 fake security error", type(self)


def _write_config(tmp_path, flake8_section=""):
    cfg = tmp_path.joinpath("tox.ini")
    cfg.write_text(f"""\
[flake8]
{flake8_section}
[flake8:local-plugins]
extension =
    SEC = {FakeSecurityChecker.__module__}:{FakeSecurityChecker.__name__}
""")
    return cfg


def _write_file(tmp_path, src="x = 1\ny = 2\n"):
    t_py = tmp_path.joinpath("t.py")
    t_py.write_text(src)
    return t_py


def test_require_codes_unset_reports_normally(tmp_path, capsys):
    """Without --require-codes both codes are reported as usual."""
    cfg = _write_config(tmp_path)
    t_py = _write_file(tmp_path)

    assert main((str(t_py), "--config", str(cfg))) == 1
    out, err = capsys.readouterr()
    assert out == (
        f"{t_py}:1:1: SEC100 fake security error\n"
        f"{t_py}:2:1: SEC101 fake security error\n"
    )
    assert err == ""


def test_require_codes_beats_extend_ignore(tmp_path, capsys):
    """Required codes are reported despite ``extend-ignore``."""
    cfg = _write_config(tmp_path, "extend-ignore = SEC1")
    t_py = _write_file(tmp_path)

    # a normal run allows the repository to ignore the codes
    assert main((str(t_py), "--config", str(cfg))) == 0
    out, err = capsys.readouterr()
    assert out == err == ""

    # the enforced run still reports the required code
    argv = (str(t_py), "--config", str(cfg), "--require-codes=SEC100")
    assert main(argv) == 1
    out, err = capsys.readouterr()
    assert out == f"{t_py}:1:1: SEC100 fake security error\n"
    assert err == ""


def test_require_codes_beats_per_file_ignores(tmp_path, capsys):
    """Required codes are reported despite ``per-file-ignores``."""
    cfg = _write_config(tmp_path, "per-file-ignores = t.py: SEC1")
    t_py = _write_file(tmp_path)

    assert main((str(t_py), "--config", str(cfg))) == 0
    out, err = capsys.readouterr()
    assert out == err == ""

    argv = (str(t_py), "--config", str(cfg), "--require-codes=SEC100")
    assert main(argv) == 1
    out, err = capsys.readouterr()
    assert out == f"{t_py}:1:1: SEC100 fake security error\n"
    assert err == ""


def test_require_codes_beats_inline_noqa(tmp_path, capsys):
    """Required codes are reported despite ``# noqa`` comments."""
    cfg = _write_config(tmp_path)
    t_py = _write_file(tmp_path, "x = 1  # noqa\ny = 2  # noqa: SEC101\n")

    assert main((str(t_py), "--config", str(cfg))) == 0
    out, err = capsys.readouterr()
    assert out == err == ""

    argv = (str(t_py), "--config", str(cfg), "--require-codes=SEC100")
    assert main(argv) == 1
    out, err = capsys.readouterr()
    assert out == f"{t_py}:1:1: SEC100 fake security error\n"
    assert err == ""


def test_require_codes_beats_file_level_noqa(tmp_path, capsys):
    """Required codes are reported despite ``# flake8: noqa``."""
    cfg = _write_config(tmp_path)
    t_py = _write_file(tmp_path, "# flake8: noqa\nx = 1\ny = 2\n")

    assert main((str(t_py), "--config", str(cfg))) == 0
    out, err = capsys.readouterr()
    assert out == err == ""

    argv = (str(t_py), "--config", str(cfg), "--require-codes=SEC100")
    assert main(argv) == 1
    out, err = capsys.readouterr()
    assert out == f"{t_py}:1:1: SEC100 fake security error\n"
    assert err == ""


def test_require_codes_cannot_be_set_in_config(tmp_path, capsys):
    """The option is caller-side only and ignored in config files."""
    cfg = _write_config(
        tmp_path, "extend-ignore = SEC1\nrequire-codes = SEC100",
    )
    t_py = _write_file(tmp_path)

    # ``require-codes`` is not registered for config parsing so the
    # repository cannot widen or weaken the caller's enforced set
    assert main((str(t_py), "--config", str(cfg))) == 0
    out, err = capsys.readouterr()
    assert out == err == ""
