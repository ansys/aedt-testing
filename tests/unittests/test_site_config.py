import os
from pathlib import Path

import pytest

from aedttest import site_config
from aedttest.site_config import apply_version_env
from aedttest.site_config import load_site_config

SITE_TOML = """
[site]
working_dir = "/work"
max_parallel_jobs = 2

[site.slurm]
partition = "p1"

[site.aedt_versions.261]
root = "/ansys/v261"
env = { SITE_CONFIG_TEST_VAR = "value261" }
"""


@pytest.fixture(autouse=True)
def restore_environ():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


def write_site(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "site.toml"
    path.write_text(content)
    return path


def test_load_site_config(tmp_path):
    config = load_site_config(write_site(tmp_path, SITE_TOML))

    assert config["working_dir"] == "/work"
    assert config["max_parallel_jobs"] == 2
    assert config["poll_interval"] == 30
    assert config["poll_timeout"] is None
    assert config["slurm"] == {"partition": "p1"}
    assert config["aedt_versions"]["261"]["root"] == Path("/ansys/v261")


def test_scheduler_table_is_optional(tmp_path):
    assert load_site_config(write_site(tmp_path, SITE_TOML))["scheduler"] == {}


def test_scheduler_table_is_loaded(tmp_path):
    content = SITE_TOML + '\n[site.scheduler]\ntype = "slurm"\n'

    assert load_site_config(write_site(tmp_path, content))["scheduler"] == {"type": "slurm"}


def test_missing_working_dir_raises(tmp_path):
    with pytest.raises(ValueError, match="working_dir"):
        load_site_config(write_site(tmp_path, "[site]\n"))


def test_missing_aedt_versions_raises(tmp_path):
    with pytest.raises(ValueError, match="aedt_versions"):
        load_site_config(write_site(tmp_path, '[site]\nworking_dir = "/work"\n'))


def test_apply_version_env(tmp_path, monkeypatch):
    monkeypatch.delenv("SITE_CONFIG_TEST_VAR", raising=False)
    config = load_site_config(write_site(tmp_path, SITE_TOML))

    apply_version_env(config, "261")

    assert os.environ["SITE_CONFIG_TEST_VAR"] == "value261"
    monkeypatch.delenv("SITE_CONFIG_TEST_VAR")


def test_apply_unknown_version_raises(tmp_path):
    config = load_site_config(write_site(tmp_path, SITE_TOML))

    with pytest.raises(ValueError, match="242"):
        apply_version_env(config, "242")


def test_apply_version_env_removes_stale_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("ANSYSEM_ROOT251", "/old/251")
    monkeypatch.setenv("ANSYS_DESKTOP261", "/old/desktop")
    monkeypatch.setenv("UNRELATED_VAR", "keep")
    config = load_site_config(write_site(tmp_path, SITE_TOML))

    apply_version_env(config, "261")

    assert "ANSYSEM_ROOT251" not in os.environ
    assert "ANSYS_DESKTOP261" not in os.environ
    assert os.environ["UNRELATED_VAR"] == "keep"


def test_apply_version_env_defaults_to_root(tmp_path, monkeypatch):
    monkeypatch.setattr(site_config, "_is_posix", lambda: True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/stale")
    config = load_site_config(write_site(tmp_path, SITE_TOML))

    apply_version_env(config, "261")

    assert os.environ["ANSYSEM_ROOT261"] == str(Path("/ansys/v261"))
    assert os.environ["LD_LIBRARY_PATH"] == str(Path("/ansys/v261"))


def test_apply_version_env_explicit_env_overrides_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(site_config, "_is_posix", lambda: True)
    content = SITE_TOML.replace(
        'env = { SITE_CONFIG_TEST_VAR = "value261" }',
        'env = { ANSYSEM_ROOT261 = "/custom", LD_LIBRARY_PATH = "/custom/lib" }',
    )
    config = load_site_config(write_site(tmp_path, content))

    apply_version_env(config, "261")

    assert os.environ["ANSYSEM_ROOT261"] == "/custom"
    assert os.environ["LD_LIBRARY_PATH"] == "/custom/lib"


def test_apply_version_env_leaves_ld_library_path_alone_off_linux(tmp_path, monkeypatch):
    monkeypatch.setattr(site_config, "_is_posix", lambda: False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/untouched")
    config = load_site_config(write_site(tmp_path, SITE_TOML))

    apply_version_env(config, "261")

    assert os.environ["LD_LIBRARY_PATH"] == "/untouched"


def test_warns_when_env_sets_another_versions_root(tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(site_config.logger, "warning", warnings.append)
    content = SITE_TOML.replace('SITE_CONFIG_TEST_VAR = "value261"', 'ANSYSEM_ROOT251 = "/ansys/v261"')

    load_site_config(write_site(tmp_path, content))

    assert len(warnings) == 1
    assert "ANSYSEM_ROOT251" in warnings[0]
    assert "ANSYSEM_ROOT261" in warnings[0]


def test_no_warning_for_matching_root(tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(site_config.logger, "warning", warnings.append)
    content = SITE_TOML.replace('SITE_CONFIG_TEST_VAR = "value261"', 'ANSYSEM_ROOT261 = "/ansys/v261"')

    load_site_config(write_site(tmp_path, content))

    assert warnings == []
