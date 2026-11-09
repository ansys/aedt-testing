"""
This is the cascade of "how this cluster works" - AEDT install root, working
directory, SLURM defaults, environment variables that must be exported before
launching/collecting AEDT (e.g. LD_LIBRARY_PATH/ANSYSEM_ROOT<ver> for
standalone collection), polling behaviour, and the reference-results folder.

Per-project TOML files (Level 3) still take precedence over anything set
here - site.toml only supplies fallback defaults.
"""

import os
import re
from pathlib import Path
from typing import Any
from typing import Dict

from aedttest.logger import logger

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 fallback


REQUIRED_KEYS = ("working_dir",)
STALE_ENV_PREFIXES = ("ANSYSEM_ROOT", "ANSYS_DESKTOP")
ANSYSEM_ROOT_PATTERN = re.compile(r"^ANSYSEM_ROOT(\d+)$")


def load_site_config(site_toml: Path) -> Dict[str, Any]:
    with open(site_toml, "rb") as file:
        raw = tomllib.load(file)

    site = raw.get("site", {})

    missing = [key for key in REQUIRED_KEYS if not site.get(key)]
    if missing:
        raise ValueError(f"site.toml is missing required [site] key(s): {', '.join(missing)}")

    aedt_versions_raw = site.get("aedt_versions", {})
    if not aedt_versions_raw:
        raise ValueError("site.toml must define at least one [site.aedt_versions.<version>] table")

    aedt_versions = {}
    for version, version_config in aedt_versions_raw.items():
        if "root" not in version_config:
            raise ValueError(f"[site.aedt_versions.{version}] is missing required 'root'")
        env = dict(version_config.get("env", {}))
        for key in env:
            match = ANSYSEM_ROOT_PATTERN.match(key)
            if match and match.group(1) != str(version):
                logger.warning(
                    f"[site.aedt_versions.{version}] sets {key}, which belongs to version {match.group(1)} - "
                    f"did you mean ANSYSEM_ROOT{version}?"
                )
        aedt_versions[str(version)] = {
            "root": Path(version_config["root"]),
            "env": env,
        }

    poll_timeout = site.get("poll_timeout") or None

    return {
        "aedt_versions": aedt_versions,
        "working_dir": site["working_dir"],
        "reference_folder": site.get("reference_folder"),
        "out_dir": site.get("out_dir"),
        "poll_interval": site.get("poll_interval", 30),
        "poll_timeout": poll_timeout,
        "max_parallel_jobs": site.get("max_parallel_jobs") or None,
        "slurm": dict(site.get("slurm", {})),
        "scheduler": dict(site.get("scheduler", {})),
    }


def _is_posix() -> bool:
    return os.name == "posix"


def apply_version_env(site_config: Dict[str, Any], version: str) -> None:
    """Export the environment for one specific AEDT version into os.environ.

    Stale ``ANSYSEM_ROOT*``/``ANSYS_DESKTOP*`` variables from the caller's shell
    are removed first, so PyAEDT cannot pick up another version. Then
    ``ANSYSEM_ROOT<version>`` (and, on Linux, ``LD_LIBRARY_PATH``) default to the
    version's ``root``; anything in the version's ``env`` table overrides them.
    """
    version = str(version)
    version_config = site_config["aedt_versions"].get(version)
    if not version_config:
        raise ValueError(f"No [site.aedt_versions.{version}] entry in site.toml")

    for key in list(os.environ):
        if key.startswith(STALE_ENV_PREFIXES):
            del os.environ[key]

    root = str(version_config["root"])
    os.environ[f"ANSYSEM_ROOT{version}"] = root
    if _is_posix():
        os.environ["LD_LIBRARY_PATH"] = root

    for key, value in version_config["env"].items():
        os.environ[key] = os.path.expandvars(str(value))
