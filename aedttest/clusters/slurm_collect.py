"""Poll job state and collect results once solves finish.

This is the "collect" half of the split submit/collect architecture:

* ``poll_until_done()`` repeatedly queries the scheduler for the job IDs in a
  ``job_map.json`` (written by ``aedttest.clusters.slurm_submit``) until
  every job reaches a terminal state (completed, failed, cancelled, ...).
* ``collect_all()`` then loops through every project that solved
  successfully and runs the existing data-extraction logic
  (``aedttest.simulation_data``) in standalone NG mode (``new_desktop=True``,
  no gRPC attach) - one project at a time, exactly as the solved files are
  dumped to disk.

Nothing here talks to AEDT during polling, and nothing here knows scheduler
specific state names: a ``Scheduler`` decides what is terminal or successful.
"""

import json
import subprocess
import sys
from pathlib import Path
from time import monotonic
from time import sleep
from typing import Any
from typing import Dict
from typing import Optional

from aedttest.clusters.scheduler import SUBMIT_FAILED
from aedttest.clusters.scheduler import Scheduler
from aedttest.clusters.scheduler import get_scheduler
from aedttest.clusters.slurm_scheduler import SACCT_TIMEOUT  # noqa: F401
from aedttest.clusters.slurm_scheduler import SUCCESS_STATES  # noqa: F401
from aedttest.clusters.slurm_scheduler import TERMINAL_STATES  # noqa: F401
from aedttest.clusters.slurm_scheduler import get_job_states  # noqa: F401
from aedttest.logger import logger


def poll_until_done(
    job_map: Dict[str, Dict[str, Any]],
    poll_interval: int = 30,
    timeout: Optional[int] = None,
    scheduler: Optional[Scheduler] = None,
) -> Dict[str, Dict[str, Any]]:
    """Poll the scheduler until every submitted job reaches a terminal state.

    Mutates and returns ``job_map``, adding/updating a ``"state"`` key per
    project (``"SUBMIT_FAILED"`` for projects that failed to submit in the
    first place). The raw scheduler state string is stored.

    Parameters
    ----------
    job_map : dict
        ``project_name -> {"job_id": ..., "project_path": ...}``, as written
        by ``aedttest.clusters.slurm_submit.submit_all``.
    poll_interval : int
        Seconds to sleep between scheduler queries.
    timeout : int, optional
        Give up waiting after this many seconds (jobs still pending/running
        are left with whatever state was last observed). ``None`` waits
        indefinitely.
    scheduler : Scheduler, optional
        Scheduler to query; defaults to ``get_scheduler()`` (SLURM).

    Returns
    -------
    dict
        The same ``job_map``, with a ``"state"`` key added per project.

    """
    scheduler = scheduler or get_scheduler()
    id_to_project = {info["job_id"]: project_name for project_name, info in job_map.items() if info.get("job_id")}
    for project_name, info in job_map.items():
        if not info.get("job_id"):
            info["state"] = SUBMIT_FAILED

    pending_ids = set(id_to_project)
    deadline = monotonic() + timeout if timeout else None

    logger.info(f"Polling scheduler for {len(pending_ids)} job(s) every {poll_interval}s")

    while pending_ids:
        if deadline and monotonic() > deadline:
            logger.warning(f"Timed out waiting for jobs: {sorted(pending_ids)}")
            break

        try:
            states = scheduler.get_states(list(pending_ids))
        except OSError as exc:
            logger.error(f"Failed to query job states, retrying in {poll_interval}s: {exc}")
            sleep(poll_interval)
            continue

        for job_id in list(pending_ids):
            state = states.get(job_id)
            if state is None:
                continue  # not visible yet
            project_name = id_to_project[job_id]
            job_map[project_name]["state"] = state
            if scheduler.is_terminal(state):
                logger.info(f"Job {job_id} ({project_name}) finished with state {state}")
                pending_ids.discard(job_id)

        if pending_ids:
            sleep(poll_interval)

    return job_map


def collect_project(
    project_path: str,
    version: str,
    extraction_script: Optional[str] = None,
    log_file: Optional[str] = None,
    debug: bool = False,
    timeout: int = 3600 * 2,
) -> Optional[str]:
    """Run standalone data extraction (fresh AEDT instance, NG mode) for one solved project.

    Parameters
    ----------
    project_path : str
        Path to the solved ``.aedt`` project file.
    version : str
        AEDT version in PyAEDT notation, e.g. ``"2026.1"``.
    extraction_script : str, optional
        Path to ``simulation_data.py``. Defaults to the copy bundled with this package.
    log_file : str, optional
        Path to the framework log file for this project.
    debug : bool
        Enable DEBUG logging in the extraction script.
    timeout : int
        Maximum time to wait for the extraction subprocess.

    Returns
    -------
    str or None
        Any error message (also logged), or ``None`` on success.

    """
    if extraction_script is None:
        extraction_script = str(Path(__file__).resolve().parent.parent / "simulation_data.py")

    command = [
        sys.executable,
        extraction_script,
        "--project-path",
        project_path,
        "--aedt-version",
        version,
    ]
    if log_file:
        command += ["--logfile-path", log_file]
    if debug:
        command.append("--debug")

    logger.info(f"Collecting data for {project_path}")
    logger.debug(f"Execute {subprocess.list2cmdline(command)}")

    try:
        subprocess.run(command, timeout=timeout, check=True)
    except subprocess.TimeoutExpired:
        error = f"Data extraction did not finish within {timeout}s for {project_path}"
        logger.error(error)
        return error
    except subprocess.CalledProcessError as exc:
        error = f"Data extraction failed for {project_path} (exit {exc.returncode})"
        logger.error(error)
        return error

    return None


def collect_all(
    job_map: Dict[str, Dict[str, Any]],
    version: str,
    log_dir: Optional[Path] = None,
    debug: bool = False,
    scheduler: Optional[Scheduler] = None,
) -> Dict[str, Dict[str, Any]]:
    """Loop through every successfully-solved project and extract its data.

    Skips projects whose job did not finish successfully according to the scheduler.

    Parameters
    ----------
    job_map : dict
        Job map with ``"state"`` populated per project, see ``poll_until_done``.
    version : str
        AEDT version in PyAEDT notation, e.g. ``"2026.1"``.
    log_dir : Path, optional
        Directory for per-project framework logs during collection.
    debug : bool
        Enable DEBUG logging in the extraction script.
    scheduler : Scheduler, optional
        Scheduler that interprets the stored states; defaults to ``get_scheduler()`` (SLURM).

    Returns
    -------
    dict
        The same ``job_map``, with a ``"collect_error"`` key added per
        project that failed extraction (absent/``None`` on success).

    """
    scheduler = scheduler or get_scheduler()
    for project_name, info in job_map.items():
        state = info.get("state")
        if not scheduler.is_success(state):
            logger.info(f"Skipping collection for {project_name}: job state was {state}")
            info["collect_error"] = f"Skipped, job state was {state}"
            continue

        log_file = str(log_dir / f"framework_{project_name}.log") if log_dir else None
        error = collect_project(
            project_path=info["project_path"],
            version=version,
            log_file=log_file,
            debug=debug,
        )
        info["collect_error"] = error

    return job_map


def load_job_map(job_map_path: Path) -> Dict[str, Dict[str, Any]]:
    """Load a ``job_map.json`` written by ``aedttest.clusters.slurm_submit``."""
    return json.loads(job_map_path.read_text())


def save_job_map(job_map: Dict[str, Dict[str, Any]], job_map_path: Path) -> None:
    """Persist an updated job map (with state/collect_error) back to disk."""
    job_map_path.write_text(json.dumps(job_map, indent=2))
