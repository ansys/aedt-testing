"""CLI entry point for the "submit" half of the split submit/collect workflow.

Submits AEDT's own ``-batchsolve`` job per project directly via ``sbatch``
(see ``aedttest.clusters.slurm_submit``). Supports two project sources:

* ``--config-folder``: TOML configs (same format as ``aedt_test_runner.py``),
  paths resolved relative to the config folder. Per-project SLURM overrides
  (multi-node, multilevel, extra batch options, etc.) go under
  ``[project.slurm]`` in each project's TOML file.
* ``--models-folder``: a plain folder of ``.aedt`` files, no TOML needed,
  all submitted identically with ``--cores``/``--nodes``.

This process does NOT wait for jobs to finish - it submits and exits.
"""

import argparse
import shutil
from pathlib import Path

from aedttest.aedt_test_runner import LOGFILE_PATH
from aedttest.aedt_test_runner import LOGFOLDER_PATH
from aedttest.aedt_test_runner import read_configs
from aedttest.aedt_test_runner import time_now
from aedttest.clusters.matrix import expand_matrix
from aedttest.clusters.scheduler import get_scheduler
from aedttest.clusters.slurm_submit import discover_projects_from_folder
from aedttest.clusters.slurm_submit import submit_all_throttled
from aedttest.logger import logger
from aedttest.logger import set_logger

CWD_DIR = Path.cwd()


def copy_project_for_run(source_path: Path, run_dir: Path, project_name: str) -> Path:
    """Copy a project (.aedt + companion .aedb results folder, if present) into
    an isolated per-run directory, so re-running never overwrites the golden
    source project or collides with other runs/sweep variants via shared locks.
    """
    project_run_dir = run_dir / project_name
    project_run_dir.mkdir(parents=True, exist_ok=True)

    dest_path = project_run_dir / f"{project_name}{source_path.suffix}"
    shutil.copy2(source_path, dest_path)

    source_aedb = source_path.with_suffix(".aedb")
    if source_aedb.is_dir():
        dest_aedb = dest_path.with_suffix(".aedb")
        if dest_aedb.exists():
            shutil.rmtree(dest_aedb)
        shutil.copytree(source_aedb, dest_aedb)

    return dest_path


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config-folder", help="Path to TOML project configuration folder")
    source.add_argument("--models-folder", help="Path to a plain folder of .aedt files (no TOML needed)")

    parser.add_argument(
        "--aedt-root",
        required=True,
        help="AEDT installation root, e.g. " "/path/to/ansys/v261/AnsysEM",
    )
    parser.add_argument(
        "--enable-matrix",
        action="store_true",
        help="Expand list-valued TOML fields (e.g. cores=[4,8,16]) into a full "
        "test matrix before submitting (default: off, single job per project)",
    )
    parser.add_argument(
        "--working-dir",
        required=True,
        help="Working directory for SLURM jobs (--chdir), e.g. /path/to/work",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Solve directly on the source project (old behavior) instead of copying "
        "into an isolated runs/<timestamp>/<project_name>/ folder (default: copy)",
    )
    parser.add_argument("--out-dir", "-o", help="Where to write job_map.json (defaults to --working-dir)")

    parser.add_argument(
        "--max-parallel", type=int, default=None, help="Cap concurrently pending/running jobs (default: no cap)"
    )

    slurm_group = parser.add_argument_group("SLURM defaults (overridable per-project via [project.slurm])")
    slurm_group.add_argument("--cores", type=int, default=32, help="Cores per job when using --models-folder")
    slurm_group.add_argument("--nodes", type=int, default=1, help="Default SLURM --nodes (>1 for multi-node jobs)")
    slurm_group.add_argument(
        "--ntasks-per-node", type=int, default=None, help="Default SLURM --ntasks-per-node (multi-node jobs)"
    )
    slurm_group.add_argument("--partition", default="", help="Default SLURM partition")
    slurm_group.add_argument("--job-name", default="AEDT", help="Default SLURM --job-name")
    slurm_group.add_argument("--design-name", default="", help="Default AEDT -batchsolve design name (blank = all)")
    slurm_group.add_argument("--mail-user", default="", help="SLURM --mail-user, e.g. you@site.com")
    slurm_group.add_argument(
        "--mail-type", default="", help="SLURM --mail-type, e.g. BEGIN,END,FAIL (requires --mail-user)"
    )

    parser.add_argument("--debug", action="store_true", help="Adds additional DEBUG logs")

    args = parser.parse_args()

    if args.config_folder:
        args.config_folder = Path(args.config_folder)
        if not args.config_folder.is_dir():
            raise ValueError(f"Configuration folder does not exist: {args.config_folder}")
    if args.models_folder:
        args.models_folder = Path(args.models_folder)
        if not args.models_folder.is_dir():
            raise ValueError(f"Models folder does not exist: {args.models_folder}")

    args.aedt_root = Path(args.aedt_root)

    return args


def main() -> None:
    args = parse_arguments()

    if not LOGFOLDER_PATH.exists():
        LOGFOLDER_PATH.mkdir()
    set_logger(logging_file=LOGFILE_PATH, level=10 if args.debug else 20, pyaedt_module=None)

    out_dir = Path(args.out_dir) if args.out_dir else Path(args.working_dir)
    run_id = time_now(posix=True)
    job_map_path = out_dir / f"job_map_{run_id}.json"
    latest_path = out_dir / "job_map_latest.json"
    run_dir = out_dir / "runs" / run_id
    latest_path = out_dir / "job_map_latest.json"

    if args.config_folder:
        project_tests_config = read_configs(args.config_folder)
        for project_config in project_tests_config.values():
            project_path = Path(project_config["path"])
            if not project_path.is_absolute():
                project_path = (args.config_folder / project_path).resolve()
            project_config["path"] = str(project_path)
            project_config["distribution"].setdefault("cores", args.cores)
    else:
        project_tests_config = discover_projects_from_folder(args.models_folder, cores=args.cores)

    if args.enable_matrix:
        before = len(project_tests_config)
        project_tests_config = expand_matrix(project_tests_config)
        logger.info(f"Test matrix expansion: {before} project(s) -> {len(project_tests_config)} job(s)")

    if not args.in_place:
        for project_name, project_config in project_tests_config.items():
            project_path = Path(project_config["path"])
            project_config["path"] = str(copy_project_for_run(project_path, run_dir, project_name))
        logger.info(f"Isolated run folder: {run_dir}")

    logger.info(f"Submitting {len(project_tests_config)} project(s) to SLURM. Working dir: {args.working_dir}")

    job_map = submit_all_throttled(
        project_tests_config=project_tests_config,
        aedt_root=args.aedt_root,
        working_dir=args.working_dir,
        job_map_path=job_map_path,
        global_slurm_config={
            "partition": args.partition,
            "job_name": args.job_name,
            "design_name": args.design_name,
            "nodes": args.nodes,
            "ntasks_per_node": args.ntasks_per_node,
            "mail_user": args.mail_user,
            "mail_type": args.mail_type,
        },
        max_parallel=args.max_parallel,
        poll_interval=30,
        scheduler=get_scheduler(),
    )

    latest_path.write_text(job_map_path.read_text())
    failed = [name for name, info in job_map.items() if info.get("job_id") is None]
    if failed:
        logger.error(f"Failed to submit: {', '.join(failed)}")

    submitted = len(job_map) - len(failed)
    job_id_lines = "\n".join(f"  {name}: job_id={info.get('job_id')}" for name, info in sorted(job_map.items()))
    logger.info(
        f"Submitted {submitted}/{len(job_map)} job(s). Job map: {job_map_path}\n"
        f"Latest pointer: {latest_path}\n"
        f"{job_id_lines}\n"
        "Run the collect step once SLURM jobs complete to gather and compare results."
    )


if __name__ == "__main__":
    main()
