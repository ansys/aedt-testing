"""Build and submit SLURM (sbatch) jobs that run AEDT's own batch solver.

This mirrors the command AEDT's own HPC/Job-Scheduler integration produces
(as captured from a real submission on this site), e.g. for a single-node job:

    sbatch --export=NONE --chdir='<working_dir>' --job-name=AEDT \\
        --partition=<partition> --nodes=1 --cpus-per-task=1 --ntasks=<cores> \\
        --exclusive <aedt_root>/schedulers/scripts/utils/ansysedt_launcher.sh \\
        /usr/bin/env \\
        ANSYSEM_GENERIC_MPI_WRAPPER=<aedt_root>/schedulers/scripts/utils/slurm_srun_wrapper.sh \\
        ANSYSEM_COMMON_PREFIX=<aedt_root>/common \\
        ANSOFT_PASS_DEBUG_ENV_TO_REMOTE_ENGINES=1 \\
        srun --overcommit --export=ALL -n 1 -N 1 --cpu-bind=none --mem-per-cpu=0 --overlap \\
        <aedt_root>/ansysedt -distributed -machinelist numcores=<cores> -auto -monitor -ng \\
        -batchoptions " 'HFSS/RAMLimitPercent'='90'" -batchsolve <design_name> <project_path>

No Python worker script is launched on the compute node - AEDT's own
launcher/wrapper scripts handle the SLURM/srun integration, and
``-batchsolve`` runs the solve standalone (no gRPC, no live session).

Every building block below is deliberately overridable, because the exact
command "shape" changes for multi-node and multilevel-domain-decomposition
jobs (extra ``-machinelist``/``-batchoptions`` tokens, extra ``#SBATCH``
flags such as ``--nodes`` > 1 / ``--ntasks-per-node``, extra env vars, etc.).
Rather than guessing every site's exact multi-node syntax, each layer
accepts ``extra_*`` overrides sourced from ``[project.slurm]`` in the TOML
config (or the submit CLI's global defaults), so you can adapt the command
per-project without touching this code.

Data extraction (opening solved projects in NG mode, pulling mesh/curve
data via ``simulation_data.py``) is a fully separate, later "collect" step -
see ``aedttest.aedt_test_runner`` / ``simulation_data.py`` - and is not part
of this module.
"""

import json
import subprocess
from pathlib import Path
from time import sleep
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from aedttest.clusters.slurm_collect import TERMINAL_STATES
from aedttest.clusters.slurm_collect import get_job_states
from aedttest.logger import logger

# Submission itself must be near-instant; if `sbatch` hangs this long,
# something is wrong with the scheduler / login node, fail fast instead of
# blocking the whole submission loop.
SBATCH_SUBMIT_TIMEOUT = 30

LAUNCHER_RELATIVE_PATH = "schedulers/scripts/utils/ansysedt_launcher.sh"
SRUN_WRAPPER_RELATIVE_PATH = "schedulers/scripts/utils/slurm_srun_wrapper.sh"
COMMON_PREFIX_RELATIVE_PATH = "common"

# Default srun flags between "srun" and the ansysedt argv. Single-node jobs
# only ever launch ONE ansysedt head process (AEDT manages its own
# MPI/HPC distribution internally via -distributed/-machinelist), hence
# "-n 1 -N 1" even when --ntasks/--nodes on the outer sbatch request many
# cores. For multi-node jobs this is usually still "-n 1 -N 1" for the same
# reason, but override via slurm_config["srun_args"] if your site differs
# (e.g. one head process launched per node).
DEFAULT_SRUN_ARGS = [
    "--overcommit",
    "--export=ALL",
    "-n",
    "1",
    "-N",
    "1",
    "--cpu-bind=none",
    "--mem-per-cpu=0",
    "--overlap",
]


def default_slurm_settings() -> Dict[str, Any]:
    """Default SLURM/AEDT-batch settings, overridable per-project via TOML ``[project.slurm]``.

    Multi-node / multilevel jobs typically only need to override:

    * ``nodes`` (> 1) and optionally ``ntasks_per_node``
    * ``machinelist_args`` - extra ``key=value`` tokens appended to
      ``-machinelist numcores=<cores>,...``, e.g. ``["numnodes=2"]`` or
      whatever your AEDT version's multilevel domain-decomposition syntax
      requires (check ``ansysedt -help`` / HPC docs for your version).
    * ``batch_options`` - extra ``-batchoptions`` key/value pairs, e.g.
      multilevel-specific HPC registry keys.
    * ``extra_sbatch_args`` - raw extra ``sbatch`` flags, e.g.
      ``["--ntasks-per-node=16", "--distribution=block:block"]``.
    * ``extra_env`` - extra environment variables set on the launcher line.
    """
    return {
        "partition": "",
        "job_name": "AEDT",
        "export": "NONE",
        "nodes": 1,
        "ntasks_per_node": None,
        "cpus_per_task": 1,
        "exclusive": True,
        # AEDT -batchsolve target design name; empty string solves all valid designs.
        "design_name": "",
        # -batchoptions key/value pairs, rendered as " 'key'='value' 'key2'='value2'"
        "batch_options": {"HFSS/RAMLimitPercent": "90"},
        # Extra tokens appended (comma-joined) after "numcores=<cores>" in -machinelist,
        # e.g. ["numnodes=2"] for multi-node, or multilevel-specific tokens.
        "machinelist_args": [],
        # Raw extra "--flag=value" strings appended to the sbatch call, e.g.
        # ["--ntasks-per-node=16", "--nodelist=node[01-02]"].
        "extra_sbatch_args": [],
        # Extra "KEY=value" env assignments appended to the launcher/env line,
        # e.g. {"ANSYSEM_MULTILEVEL_MODE": "1"}.
        "extra_env": {},
        # Override the srun flags between "srun" and the ansysedt argv,
        # see DEFAULT_SRUN_ARGS above.
        "srun_args": None,
        # Native SLURM email notifications (no custom email code needed):
        # sbatch --mail-user=<mail_user> --mail-type=<mail_type>
        "mail_user": "",
        "mail_type": "",
        "auto_hpc": True,
    }


def build_batchoptions_string(options: Dict[str, str]) -> str:
    """Render batch options the way AEDT's own scheduler integration does.

    Parameters
    ----------
    options : dict
        Mapping of AEDT registry-style option name to value,
        e.g. ``{"HFSS/RAMLimitPercent": "90"}``.

    Returns
    -------
    str
        A single string like ``" 'HFSS/RAMLimitPercent'='90'"``, passed as
        ONE argv element to ``-batchoptions`` (no shell involved, so no
        extra escaping is needed beyond AEDT's own expected quoting).

    """
    return "".join(f" '{key}'='{value}'" for key, value in options.items())


def build_ansysedt_argv(
    aedt_root: Path,
    project_path: str,
    cores: int,
    design_name: str = "",
    batch_options: Optional[Dict[str, str]] = None,
    machinelist_args: Optional[List[str]] = None,
    auto: bool = True,
) -> List[str]:
    """Build the raw ``ansysedt -batchsolve ...`` argv (no scheduler wrapping).

    ``auto=False`` skips ``-distributed``/``-machinelist``/``-auto`` entirely -
    required for design types that don't support automatic HPC settings
    (e.g. Icepak: "Icepak design type does not support automatic settings.
    When using -auto, all design types being solved must support automatic
    settings."). AEDT also enforces "-machinelist numcores=... requires
    -auto", so these three flags only ever appear together, never partially.
    """
    argv = [str(aedt_root / "ansysedt")]

    if auto:
        machinelist_value = ",".join([f"numcores={cores}", *(machinelist_args or [])])
        argv += ["-distributed", "-machinelist", machinelist_value, "-auto"]

    argv += [
        "-monitor",
        "-ng",
        "-batchoptions",
        build_batchoptions_string(batch_options or {"HFSS/RAMLimitPercent": "90"}),
        "-batchsolve",
    ]
    if design_name:
        argv.append(design_name)
    argv.append(project_path)
    return argv


def build_launcher_argv(
    aedt_root: Path,
    ansysedt_argv: List[str],
    extra_env: Optional[Dict[str, str]] = None,
    srun_args: Optional[List[str]] = None,
) -> List[str]:
    """Wrap the raw ansysedt argv with AEDT's own SLURM launcher/srun wrapper scripts.

    ``extra_env`` and ``srun_args`` are override points for multi-node/multilevel
    jobs whose site-specific launcher requires additional environment
    variables or different ``srun`` flags than the single-node default.
    """
    launcher = str(aedt_root / LAUNCHER_RELATIVE_PATH)
    srun_wrapper = str(aedt_root / SRUN_WRAPPER_RELATIVE_PATH)
    common_prefix = str(aedt_root / COMMON_PREFIX_RELATIVE_PATH)

    env_assignments = [
        f"ANSYSEM_GENERIC_MPI_WRAPPER={srun_wrapper}",
        f"ANSYSEM_COMMON_PREFIX={common_prefix}",
        "ANSOFT_PASS_DEBUG_ENV_TO_REMOTE_ENGINES=1",
    ]
    for key, value in (extra_env or {}).items():
        env_assignments.append(f"{key}={value}")

    return [
        launcher,
        "/usr/bin/env",
        *env_assignments,
        "srun",
        *(srun_args if srun_args is not None else DEFAULT_SRUN_ARGS),
        *ansysedt_argv,
    ]


def build_sbatch_argv(
    working_dir: str,
    job_name: str,
    partition: str,
    cores: int,
    launcher_argv: List[str],
    nodes: int = 1,
    ntasks_per_node: Optional[int] = None,
    cpus_per_task: int = 1,
    exclusive: bool = True,
    export: str = "NONE",
    extra_sbatch_args: Optional[List[str]] = None,
    mail_user: str = "",
    mail_type: str = "",
) -> List[str]:
    """Build the full ``sbatch ...`` argv that submits ``launcher_argv`` as the job command.

    ``nodes`` > 1 and/or ``ntasks_per_node`` cover the multi-node case;
    ``extra_sbatch_args`` is a free-form escape hatch for anything else
    a particular site/partition needs (e.g. ``--nodelist``, ``--distribution``).
    ``mail_user``/``mail_type`` use SLURM's own native email notifications
    (e.g. ``mail_type="BEGIN,END,FAIL"``) - no custom email code required.
    """
    argv = [
        "sbatch",
        "--parsable",
        f"--export={export}",
        f"--chdir={working_dir}",
        f"--job-name={job_name}",
    ]
    if partition:
        argv.append(f"--partition={partition}")
    argv += [
        f"--nodes={nodes}",
        f"--cpus-per-task={cpus_per_task}",
        f"--ntasks={cores}",
    ]
    if ntasks_per_node:
        argv.append(f"--ntasks-per-node={ntasks_per_node}")
    if exclusive:
        argv.append("--exclusive")
    if mail_user:
        argv.append(f"--mail-user={mail_user}")
    if mail_type:
        argv.append(f"--mail-type={mail_type}")
    argv += extra_sbatch_args or []
    argv += launcher_argv
    return argv


def submit_job(sbatch_argv: List[str]) -> str:
    """Submit an already-built ``sbatch`` argv and return the SLURM job ID.

    Parameters
    ----------
    sbatch_argv : list
        Full argv starting with ``"sbatch"`` (see ``build_sbatch_argv``).

    Returns
    -------
    str
        The SLURM job ID as returned by ``sbatch --parsable``.

    Raises
    ------
    OSError
        If ``sbatch`` isn't found, times out, fails, or returns unparsable output.

    """
    logger.debug(f"Execute {subprocess.list2cmdline(sbatch_argv)}")

    try:
        result = subprocess.run(
            sbatch_argv,
            capture_output=True,
            text=True,
            timeout=SBATCH_SUBMIT_TIMEOUT,
            check=True,
        )
    except FileNotFoundError as exc:
        raise OSError("sbatch executable not found - is SLURM available on this node?") from exc
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"sbatch did not respond within {SBATCH_SUBMIT_TIMEOUT}s") from exc
    except subprocess.CalledProcessError as exc:
        raise OSError(f"sbatch submission failed (exit {exc.returncode}): {exc.stderr}") from exc

    # --parsable may print "job_id" or "job_id;cluster_name"
    job_id = result.stdout.strip().split(";")[0]
    if not job_id.isdigit():
        raise OSError(f"Could not parse job ID from sbatch output: {result.stdout!r}")

    logger.info(f"Submitted SLURM job {job_id}")
    return job_id


def submit_project(
    project_name: str,
    project_path: str,
    cores: int,
    aedt_root: Path,
    working_dir: str,
    slurm_config: Dict[str, Any],
    auto: bool = True,
) -> str:
    """Build and submit the full sbatch command for a single project.

    Parameters
    ----------
    project_name : str
        Name of the project (used for logging only).
    project_path : str
        Absolute path to the ``.aedt`` file to solve.
    cores : int
        Number of cores to request (``--ntasks`` / ``-machinelist numcores=``).
    aedt_root : Path
        Path to the AEDT installation root, e.g.
        ``/path/to/ansys/v261/AnsysEM``.
    working_dir : str
        Working directory for the SLURM job (``--chdir``).
    slurm_config : dict
        SLURM/AEDT-batch settings, see ``default_slurm_settings()``.

    Returns
    -------
    str
        The SLURM job ID.

    """
    ansysedt_argv = build_ansysedt_argv(
        aedt_root=aedt_root,
        project_path=project_path,
        cores=cores,
        design_name=slurm_config.get("design_name", ""),
        batch_options=slurm_config.get("batch_options"),
        machinelist_args=slurm_config.get("machinelist_args"),
        auto=auto,
    )
    launcher_argv = build_launcher_argv(
        aedt_root=aedt_root,
        ansysedt_argv=ansysedt_argv,
        extra_env=slurm_config.get("extra_env"),
        srun_args=slurm_config.get("srun_args"),
    )
    sbatch_argv = build_sbatch_argv(
        working_dir=working_dir,
        job_name=slurm_config.get("job_name", "AEDT"),
        partition=slurm_config.get("partition", ""),
        cores=cores,
        launcher_argv=launcher_argv,
        nodes=slurm_config.get("nodes", 1),
        ntasks_per_node=slurm_config.get("ntasks_per_node"),
        cpus_per_task=slurm_config.get("cpus_per_task", 1),
        exclusive=slurm_config.get("exclusive", True),
        export=slurm_config.get("export", "NONE"),
        extra_sbatch_args=slurm_config.get("extra_sbatch_args"),
        mail_user=slurm_config.get("mail_user", ""),
        mail_type=slurm_config.get("mail_type", ""),
    )
    logger.info(
        f"Submitting {project_name} ({cores} cores, {slurm_config.get('nodes', 1)} node(s)) from {project_path}"
    )
    return submit_job(sbatch_argv)


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
