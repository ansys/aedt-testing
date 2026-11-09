"""SLURM implementation of the scheduler interface.

Everything that is specific to SLURM lives here: building and submitting the
``sbatch`` command, querying ``sacct`` and mapping SLURM states to ``JobState``.

The ``sbatch`` command mirrors the one AEDT's own HPC/Job-Scheduler integration
produces (as captured from a real submission on this site), e.g. for a
single-node job:

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

import subprocess
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from aedttest.clusters.scheduler import TERMINAL_JOB_STATES
from aedttest.clusters.scheduler import JobSpec
from aedttest.clusters.scheduler import JobState
from aedttest.clusters.scheduler import Scheduler
from aedttest.logger import logger

# Submission itself must be near-instant; if `sbatch` hangs this long,
# something is wrong with the scheduler / login node, fail fast instead of
# blocking the whole submission loop.
SBATCH_SUBMIT_TIMEOUT = 30
SACCT_TIMEOUT = 30

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


def get_job_states(job_ids: List[str]) -> Dict[str, str]:
    """Query ``sacct`` once for the current state of a batch of job IDs.

    Parameters
    ----------
    job_ids : list of str
        SLURM job IDs to query.

    Returns
    -------
    dict
        ``job_id -> state`` (e.g. ``"RUNNING"``, ``"COMPLETED"``). Job IDs
        not found in ``sacct`` output are omitted (not yet visible to the
        accounting DB, or the id was invalid).

    """
    if not job_ids:
        return {}

    command = [
        "sacct",
        "-j",
        ",".join(job_ids),
        "--format=JobID,State",
        "--noheader",
        "--parsable2",
        "-X",  # exclude job steps, only the main allocation line
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=SACCT_TIMEOUT,
            check=True,
        )
    except FileNotFoundError as exc:
        raise OSError("sacct executable not found - is SLURM available on this node?") from exc
    except subprocess.TimeoutExpired as exc:
        raise OSError(f"sacct did not respond within {SACCT_TIMEOUT}s") from exc
    except subprocess.CalledProcessError as exc:
        raise OSError(f"sacct query failed (exit {exc.returncode}): {exc.stderr}") from exc

    states: Dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line.strip() or "|" not in line:
            continue
        job_id, state = line.split("|", 1)
        # state can look like "CANCELLED by 1234" - keep only the first word
        states[job_id.strip()] = state.strip().split()[0]

    return states


_STATE_MAP = {
    "PENDING": JobState.PENDING,
    "CONFIGURING": JobState.PENDING,
    "REQUEUED": JobState.PENDING,
    "RESV_DEL_HOLD": JobState.PENDING,
    "REQUEUE_FED": JobState.PENDING,
    "REQUEUE_HOLD": JobState.PENDING,
    "RUNNING": JobState.RUNNING,
    "COMPLETING": JobState.RUNNING,
    "SUSPENDED": JobState.RUNNING,
    "STOPPED": JobState.RUNNING,
    "RESIZING": JobState.RUNNING,
    "SIGNALING": JobState.RUNNING,
    "STAGE_OUT": JobState.RUNNING,
    "COMPLETED": JobState.SUCCEEDED,
    "CANCELLED": JobState.CANCELLED,
    "REVOKED": JobState.CANCELLED,
    "FAILED": JobState.FAILED,
    "TIMEOUT": JobState.FAILED,
    "NODE_FAIL": JobState.FAILED,
    "OUT_OF_MEMORY": JobState.FAILED,
    "BOOT_FAIL": JobState.FAILED,
    "DEADLINE": JobState.FAILED,
    "PREEMPTED": JobState.FAILED,
    "SPECIAL_EXIT": JobState.FAILED,
}

# Derived from the state map above, kept for backwards compatibility.
TERMINAL_STATES = {state for state, job_state in _STATE_MAP.items() if job_state in TERMINAL_JOB_STATES}
SUCCESS_STATES = {state for state, job_state in _STATE_MAP.items() if job_state is JobState.SUCCEEDED}


class SlurmScheduler(Scheduler):
    def submit(self, spec: JobSpec) -> str:
        return submit_project(
            project_name=spec.name,
            project_path=spec.project_path,
            cores=spec.cores,
            aedt_root=spec.aedt_root,
            working_dir=spec.working_dir,
            slurm_config=spec.options,
            auto=spec.options.get("auto_hpc", True),
        )

    def get_states(self, job_ids: List[str]) -> Dict[str, str]:
        return get_job_states(job_ids)

    def normalize(self, raw_state: Optional[str]) -> JobState:
        if not raw_state:
            return JobState.UNKNOWN
        return _STATE_MAP.get(raw_state.split()[0].upper(), JobState.UNKNOWN)
