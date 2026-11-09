"""CLI entry point for the "collect" half of the split submit/collect workflow.

Reads a ``job_map.json`` written by ``aedt_test_submit``, polls the scheduler
until every job finishes, then loops through every
successfully-solved project and runs data extraction (fresh AEDT instance
per project, NG mode - see ``aedttest.simulation_data``).

Email notifications for job submission/completion are handled natively by
SLURM itself (``sbatch --mail-user=... --mail-type=BEGIN,END,FAIL``, see
``aedttest.submit_runner``) - no custom email code here.
"""

import argparse
from pathlib import Path

from aedttest.aedt_test_runner import LOGFILE_PATH
from aedttest.aedt_test_runner import LOGFOLDER_PATH
from aedttest.aedt_test_runner import aedt_version_to_pyaedt
from aedttest.clusters.scheduler import get_scheduler
from aedttest.clusters.slurm_collect import collect_all
from aedttest.clusters.slurm_collect import load_job_map
from aedttest.clusters.slurm_collect import poll_until_done
from aedttest.clusters.slurm_collect import save_job_map
from aedttest.logger import logger
from aedttest.logger import set_logger


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-map", help="Path to job_map.json (default: job_map_latest.json in current directory)")
    parser.add_argument("--aedt-version", required=True, help="Electronics Desktop version, e.g. 251")
    parser.add_argument("--poll-interval", type=int, default=30, help="Seconds between SLURM state checks")
    parser.add_argument(
        "--poll-timeout", type=int, default=None, help="Give up polling after N seconds (default: never)"
    )
    parser.add_argument("--log-dir", help="Directory for per-project logs during collection (default: ./logs)")

    parser.add_argument("--debug", action="store_true", help="Adds additional DEBUG logs")

    args = parser.parse_args()
    args.job_map = Path(args.job_map) if args.job_map else Path.cwd() / "job_map_latest.json"
    if not args.job_map.is_file():
        raise ValueError(f"Job map file does not exist: {args.job_map}")
    return args


def main() -> None:
    args = parse_arguments()

    if not LOGFOLDER_PATH.exists():
        LOGFOLDER_PATH.mkdir()
    set_logger(logging_file=LOGFILE_PATH, level=10 if args.debug else 20, pyaedt_module=None)

    log_dir = Path(args.log_dir) if args.log_dir else LOGFOLDER_PATH

    scheduler = get_scheduler()
    job_map = load_job_map(args.job_map)
    logger.info(f"Loaded job map with {len(job_map)} project(s) from {args.job_map}")

    job_map = poll_until_done(job_map, poll_interval=args.poll_interval, timeout=args.poll_timeout, scheduler=scheduler)
    save_job_map(job_map, args.job_map)

    solved = sum(1 for info in job_map.values() if scheduler.is_success(info.get("state")))
    logger.info(f"Job polling done: {solved}/{len(job_map)} job(s) completed successfully")

    pyaedt_version = aedt_version_to_pyaedt(args.aedt_version)
    job_map = collect_all(job_map, version=pyaedt_version, log_dir=log_dir, debug=args.debug, scheduler=scheduler)
    save_job_map(job_map, args.job_map)

    collected_ok = sum(
        1 for info in job_map.values() if scheduler.is_success(info.get("state")) and not info.get("collect_error")
    )
    logger.info(f"Data collection done: {collected_ok}/{solved} solved project(s) extracted successfully")


if __name__ == "__main__":
    main()
