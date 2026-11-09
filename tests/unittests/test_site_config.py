import os
from pathlib import Path

import pytest

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
