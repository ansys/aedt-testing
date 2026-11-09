"""Submit one SLURM job per project and persist the job mapping.

The SLURM-specific command building (``sbatch``) lives in
``aedttest.clusters.slurm_scheduler``; the names below are re-exported from
there so existing imports keep working.

Data extraction (opening solved projects in NG mode, pulling mesh/curve
data via ``simulation_data.py``) is a fully separate, later "collect" step -
see ``aedttest.aedt_test_runner`` / ``simulation_data.py`` - and is not part
of this module.
"""

import json
import subprocess  # noqa: F401  (tests patch ``slurm_submit.subprocess``; same module object as in slurm_scheduler)
from pathlib import Path
from time import sleep
from typing import Any
from typing import Dict
from typing import Optional

from aedttest.clusters.slurm_scheduler import COMMON_PREFIX_RELATIVE_PATH  # noqa: F401
from aedttest.clusters.slurm_scheduler import DEFAULT_SRUN_ARGS  # noqa: F401
from aedttest.clusters.slurm_scheduler import LAUNCHER_RELATIVE_PATH  # noqa: F401
from aedttest.clusters.slurm_scheduler import SBATCH_SUBMIT_TIMEOUT  # noqa: F401
from aedttest.clusters.slurm_scheduler import SRUN_WRAPPER_RELATIVE_PATH  # noqa: F401
from aedttest.clusters.slurm_scheduler import TERMINAL_STATES
from aedttest.clusters.slurm_scheduler import build_ansysedt_argv  # noqa: F401
from aedttest.clusters.slurm_scheduler import build_batchoptions_string  # noqa: F401
from aedttest.clusters.slurm_scheduler import build_launcher_argv  # noqa: F401
from aedttest.clusters.slurm_scheduler import build_sbatch_argv  # noqa: F401
from aedttest.clusters.slurm_scheduler import default_slurm_settings
from aedttest.clusters.slurm_scheduler import get_job_states
from aedttest.clusters.slurm_scheduler import submit_job  # noqa: F401
from aedttest.clusters.slurm_scheduler import submit_project
from aedttest.logger import logger


def submit_all(
    project_tests_config: Dict[str, Any],
    aedt_root: Path,
    working_dir: str,
    job_map_path: Path,
    global_slurm_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Submit one SLURM job per project and persist the job mapping.

    Parameters
    ----------
    project_tests_config : dict
        Output of ``aedttest.aedt_test_runner.read_configs`` or
        ``discover_projects_from_folder``. Each entry must have ``"path"``
        (absolute project path) and ``["distribution"]["cores"]``; an
        optional per-project ``"slurm"`` table overrides
        ``global_slurm_config``/defaults (see ``default_slurm_settings()``).
    aedt_root : Path
        Path to the AEDT installation root.
    working_dir : str
        Working directory for all SLURM jobs (``--chdir``).
    job_map_path : Path
        Where to write the ``project_name -> {job_id, project_path}`` JSON map.
    global_slurm_config : dict, optional
        Fallback SLURM/AEDT-batch settings applied when a project doesn't
        define its own ``[project.slurm]`` table.

    Returns
    -------
    dict
        The job map that was written to ``job_map_path``.

    """
    base_slurm_config = dict(default_slurm_settings(), **(global_slurm_config or {}))
    job_map_path.parent.mkdir(parents=True, exist_ok=True)

    job_map: Dict[str, Dict[str, Any]] = {}
    for project_name, project_config in project_tests_config.items():
        slurm_config = dict(base_slurm_config, **project_config.get("slurm", {}))
        cores = project_config["distribution"]["cores"]
        project_path = project_config["path"]

        try:
            job_id = submit_project(
                project_name=project_name,
                project_path=project_path,
                cores=cores,
                aedt_root=aedt_root,
                working_dir=working_dir,
                slurm_config=slurm_config,
                auto=slurm_config.get("auto_hpc", True),
            )
        except OSError as exc:
            logger.error(f"Failed to submit {project_name}: {exc}")
            job_map[project_name] = {"job_id": None, "project_path": project_path, "error": str(exc)}
            continue

        job_map[project_name] = {"job_id": job_id, "project_path": project_path}

    job_map_path.write_text(json.dumps(job_map, indent=2))
    logger.info(f"Job map written to {job_map_path}")
    return job_map


def submit_all_throttled(
    project_tests_config: Dict[str, Any],
    aedt_root: Path,
    working_dir: str,
    job_map_path: Path,
    global_slurm_config: Optional[Dict[str, Any]] = None,
    max_parallel: Optional[int] = None,
    poll_interval: int = 30,
) -> Dict[str, Dict[str, Any]]:
    """Like ``submit_all``, but never has more than ``max_parallel`` jobs
    concurrently pending/running (protects shared license pools / queue
    politeness on large test-matrix sweeps). ``max_parallel=None``/``0``
    falls back to submitting everything immediately.
    """
    if not max_parallel:
        return submit_all(project_tests_config, aedt_root, working_dir, job_map_path, global_slurm_config)

    base_slurm_config = dict(default_slurm_settings(), **(global_slurm_config or {}))
    job_map_path.parent.mkdir(parents=True, exist_ok=True)

    job_map: Dict[str, Dict[str, Any]] = {}
    pending_projects = list(project_tests_config.items())
    active_ids: set = set()

    logger.info(f"Throttled submission: {len(pending_projects)} job(s), max {max_parallel} concurrent")

    while pending_projects or active_ids:
        while pending_projects and len(active_ids) < max_parallel:
            project_name, project_config = pending_projects.pop(0)
            slurm_config = dict(base_slurm_config, **project_config.get("slurm", {}))
            cores = project_config["distribution"]["cores"]
            project_path = project_config["path"]

            try:
                job_id = submit_project(
                    project_name=project_name,
                    project_path=project_path,
                    cores=cores,
                    aedt_root=aedt_root,
                    working_dir=working_dir,
                    slurm_config=slurm_config,
                    auto=slurm_config.get("auto_hpc", True),
                )
                job_map[project_name] = {"job_id": job_id, "project_path": project_path}
                active_ids.add(job_id)
            except OSError as exc:
                logger.error(f"Failed to submit {project_name}: {exc}")
                job_map[project_name] = {"job_id": None, "project_path": project_path, "error": str(exc)}

        job_map_path.write_text(json.dumps(job_map, indent=2))

        if not active_ids:
            break

        sleep(poll_interval)
        try:
            states = get_job_states(list(active_ids))
        except OSError as exc:
            logger.error(f"Failed to query sacct while throttling submissions: {exc}")
            continue

        for job_id in list(active_ids):
            if states.get(job_id) in TERMINAL_STATES:
                active_ids.discard(job_id)

    job_map_path.write_text(json.dumps(job_map, indent=2))
    logger.info(f"Job map written to {job_map_path}")
    return job_map


def discover_projects_from_folder(models_folder: Path, cores: int = 32) -> Dict[str, Any]:
    """Build a minimal project config dict by scanning a folder for ``.aedt`` files.

    Use this when there is no TOML configuration - just a folder of models to
    submit as-is, all with the same core count.

    Parameters
    ----------
    models_folder : Path
        Folder to (recursively) scan for ``.aedt`` files.
    cores : int
        Core count to request for every discovered project.

    Returns
    -------
    dict
        Same shape as ``aedttest.aedt_test_runner.read_configs()`` output,
        keyed by project name (file stem).

    """
    project_tests_config: Dict[str, Any] = {}
    for aedt_file in sorted(models_folder.rglob("*.aedt")):
        project_name = aedt_file.stem
        project_tests_config[project_name] = {
            "path": str(aedt_file.resolve()),
            "distribution": {"cores": cores, "single_node": True},
        }

    if not project_tests_config:
        raise ValueError(f"No .aedt files found under {models_folder}")

    return project_tests_config
