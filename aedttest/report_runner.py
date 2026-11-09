"""CLI entry point for the "report" step: build the comparison HTML report
from results already solved on the cluster and collected (extracted) via
``aedt_test_collect``.

This reuses the exact same ``ElectronicsDesktopTester`` reporting pipeline
(``prepare_project_report``, ``render_project_html``, ``render_main_html``)
as the original monolithic ``aedt_test_runner`` - only the "solve" part is
skipped, since it already happened via SLURM. The output is byte-for-byte
the same report structure/HTML you had before the split.
"""

import argparse
import json
from pathlib import Path

from aedttest.aedt_test_runner import LOGFILE_PATH
from aedttest.aedt_test_runner import LOGFOLDER_PATH
from aedttest.aedt_test_runner import ElectronicsDesktopTester
from aedttest.clusters.scheduler import get_scheduler
from aedttest.logger import logger
from aedttest.logger import set_logger


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aedt-version", required=True, help="Electronics Desktop version to test, e.g. 251")
    parser.add_argument("--config-folder", required=True, help="Path to project configuration folder")
    parser.add_argument("--job-map", help="Path to job_map.json (default: job_map_latest.json in current directory)")
    parser.add_argument("--reference-folder", help="Reference results folder path")
    parser.add_argument("--only-reference", action="store_true", help="Only create reference results")
    parser.add_argument("--out-dir", "-o", help="Output directory for the HTML report")
    parser.add_argument(
        "--save-sim-data", "-s", action="store_true", help="Save simulation data under output dir (--out-dir flag)"
    )
    parser.add_argument("--debug", action="store_true", help="Adds additional DEBUG logs")

    args = parser.parse_args()
    args.config_folder = Path(args.config_folder)
    if not args.config_folder.is_dir():
        raise ValueError(f"Configuration folder does not exist: {args.config_folder}")

    args.job_map = Path(args.job_map) if args.job_map else Path.cwd() / "job_map_latest.json"
    if not args.job_map.is_file():
        raise ValueError(f"Job map file does not exist: {args.job_map}")

    if not args.only_reference:
        if not args.reference_folder:
            raise ValueError("Either set --only-reference flag or provide path via --reference-folder")
        args.reference_folder = Path(args.reference_folder)
        if not args.reference_folder.is_dir():
            raise ValueError(f"Reference folder does not exist: {args.reference_folder}")

    return args


def main() -> None:
    args = parse_arguments()

    if not LOGFOLDER_PATH.exists():
        LOGFOLDER_PATH.mkdir()
    set_logger(logging_file=LOGFILE_PATH, level=10 if args.debug else 20, pyaedt_module=None)

    job_map = json.loads(args.job_map.read_text())

    tester = ElectronicsDesktopTester(
        version=args.aedt_version,
        max_cores=99999,
        max_parallel_projects=99999,
        config_folder=args.config_folder,
        out_dir=args.out_dir,
        save_projects=args.save_sim_data,
        only_reference=args.only_reference,
        reference_folder=args.reference_folder,
        debug=args.debug,
    )
    tester.initialize_results()

    scheduler = get_scheduler()
    for project_name, info in sorted(job_map.items()):
        project_path = info["project_path"]
        state = info.get("state")
        errors = None
        if state and not scheduler.is_success(state):
            errors = f"Job did not complete successfully (state={state})"
        elif info.get("collect_error"):
            errors = info["collect_error"]

        logger.info(f"Building report for {project_name} (state={state})")
        tester.finalize_project_report(project_name, project_path, errors=errors)

    tester.render_main_html(finished=True)

    msg = (
        f"Report is completed.\nReference result folder is stored under {tester.reference_folder}"
        f"\nYou can view report by opening in web browser: {tester.results_path / 'main.html'}"
    )
    logger.info(msg)


if __name__ == "__main__":
    main()
