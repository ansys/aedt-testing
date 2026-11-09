"""   aedt_test_pipeline --site site.toml --config-folder ./configs \
        --reference-version 261 --test-version 242

Runs the full submit->collect->report cycle once for --reference-version
(always creating a fresh baseline for this comparison), then again for
--test-version (comparing against that fresh baseline). Omit --test-version
to just create/refresh a reference baseline with no comparison.

Test-matrix expansion (cores/nodes/etc, see aedttest.clusters.matrix) always
runs within each version's cycle - it does not sweep across versions itself,
that's what --reference-version/--test-version are for.
"""

import argparse
from pathlib import Path
from typing import Optional

from aedttest.aedt_test_runner import LOGFILE_PATH
from aedttest.aedt_test_runner import LOGFOLDER_PATH
from aedttest.aedt_test_runner import ElectronicsDesktopTester
from aedttest.aedt_test_runner import aedt_version_to_pyaedt
from aedttest.aedt_test_runner import read_configs
from aedttest.aedt_test_runner import time_now
from aedttest.clusters.matrix import expand_matrix
from aedttest.clusters.scheduler import get_scheduler
from aedttest.clusters.slurm_collect import collect_all
from aedttest.clusters.slurm_collect import poll_until_done
from aedttest.clusters.slurm_collect import save_job_map
from aedttest.clusters.slurm_submit import submit_all_throttled
from aedttest.logger import logger
from aedttest.logger import set_logger
from aedttest.site_config import apply_version_env
from aedttest.site_config import load_site_config
from aedttest.submit_runner import copy_project_for_run


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", required=True, help="Path to site.toml")
    parser.add_argument(
        "--reference-version",
        required=True,
        help="AEDT version used to (re)create the reference baseline, "
        "must match a [site.aedt_versions.<version>] entry",
    )
    parser.add_argument(
        "--test-version",
        default=None,
        help="If given, also run this version and compare it against --reference-version's fresh baseline",
    )
    parser.add_argument("--config-folder", required=True, help="Path to project TOML folder")
    parser.add_argument("--save-sim-data", "-s", action="store_true", help="Save simulation data under output dir")
    parser.add_argument("--debug", action="store_true", help="Adds additional DEBUG logs")

    args = parser.parse_args()
    args.site = Path(args.site)
    if not args.site.is_file():
        raise ValueError(f"site.toml not found: {args.site}")

    args.config_folder = Path(args.config_folder)
    if not args.config_folder.is_dir():
        raise ValueError(f"Configuration folder does not exist: {args.config_folder}")

    return args


def check_submissions(job_map: dict, version: str) -> None:
    """Log failed submissions and stop if none of the jobs could be submitted."""
    failed_submit = [name for name, info in job_map.items() if info.get("job_id") is None]
    if failed_submit:
        logger.error(f"[{version}] Failed to submit: {', '.join(failed_submit)}")
    if job_map and len(failed_submit) == len(job_map):
        raise RuntimeError(
            f"[{version}] All {len(job_map)} job submission(s) failed, nothing to poll or collect. "
            "See the 'Failed to submit' errors above (scheduler available? partition/aedt root correct?)."
        )


def run_version(
    version: str,
    site_config: dict,
    config_folder: Path,
    reference_folder: Optional[Path],
    only_reference: bool,
    save_sim_data: bool,
    debug: bool,
) -> Path:
    """Run one full submit -> poll -> collect -> report cycle for one AEDT version.

    Returns the reference_folder this run produced (its own fresh reference
    output), so the caller can pass it into a subsequent comparison run.
    """
    version_config = site_config["aedt_versions"].get(version)
    if not version_config:
        raise ValueError(
            f"No [site.aedt_versions.{version}] entry in site.toml. "
            f"Available: {sorted(site_config['aedt_versions'])}"
        )
    apply_version_env(site_config, version)
    scheduler = get_scheduler(site_config)

    out_dir = Path(site_config["out_dir"]) if site_config["out_dir"] else Path(site_config["working_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    job_map_path = out_dir / f"job_map_{version}_{time_now(posix=True)}.json"

    project_tests_config = read_configs(config_folder)
    for project_config in project_tests_config.values():
        project_path = Path(project_config["path"])
        if not project_path.is_absolute():
            project_path = (config_folder / project_path).resolve()
        project_config["path"] = str(project_path)

    before = len(project_tests_config)
    project_tests_config = expand_matrix(project_tests_config)
    if len(project_tests_config) != before:
        logger.info(f"Test matrix expansion: {before} project(s) -> {len(project_tests_config)} job(s)")

    run_dir = out_dir / "runs" / f"{version}_{time_now(posix=True)}"
    for project_name, project_config in project_tests_config.items():
        project_config["path"] = str(copy_project_for_run(Path(project_config["path"]), run_dir, project_name))
    logger.info(f"[{version}] Isolated run folder: {run_dir}")

    logger.info(f"[{version}] Pipeline: {len(project_tests_config)} job(s)")

    job_map = submit_all_throttled(
        project_tests_config=project_tests_config,
        aedt_root=version_config["root"],
        working_dir=site_config["working_dir"],
        job_map_path=job_map_path,
        global_slurm_config=site_config["slurm"],
        max_parallel=site_config["max_parallel_jobs"],
        poll_interval=site_config["poll_interval"],
        scheduler=scheduler,
    )

    check_submissions(job_map, version)

    job_map = poll_until_done(
        job_map, poll_interval=site_config["poll_interval"], timeout=site_config["poll_timeout"], scheduler=scheduler
    )
    save_job_map(job_map, job_map_path)

    pyaedt_version = aedt_version_to_pyaedt(version)
    job_map = collect_all(job_map, version=pyaedt_version, log_dir=LOGFOLDER_PATH, debug=debug, scheduler=scheduler)
    save_job_map(job_map, job_map_path)

    tester = ElectronicsDesktopTester(
        version=version,
        max_cores=99999,
        max_parallel_projects=99999,
        config_folder=config_folder,
        out_dir=str(out_dir),
        save_projects=save_sim_data,
        only_reference=only_reference,
        reference_folder=reference_folder,
        debug=debug,
    )
    tester.project_tests_config = project_tests_config
    tester.initialize_results()

    for project_name, info in sorted(job_map.items()):
        state = info.get("state")
        errors = None
        if state and not scheduler.is_success(state):
            errors = f"Job did not complete successfully (state={state})"
        elif info.get("collect_error"):
            errors = info["collect_error"]
        tester.finalize_project_report(project_name, info["project_path"], errors=errors)

    tester.render_main_html(finished=True)

    logger.info(
        f"[{version}] Done. Job map: {job_map_path}\n" f"[{version}] Report: {tester.results_path / 'main.html'}"
    )
    return tester.reference_folder


def main() -> None:
    args = parse_arguments()

    if not LOGFOLDER_PATH.exists():
        LOGFOLDER_PATH.mkdir()
    set_logger(logging_file=LOGFILE_PATH, level=10 if args.debug else 20, pyaedt_module=None)

    site_config = load_site_config(args.site)

    logger.info(f"=== Reference run: version {args.reference_version} ===")
    reference_folder = run_version(
        version=args.reference_version,
        site_config=site_config,
        config_folder=args.config_folder,
        reference_folder=None,
        only_reference=True,
        save_sim_data=args.save_sim_data,
        debug=args.debug,
    )

    if args.test_version:
        logger.info(f"=== Test run: version {args.test_version}, comparing against {args.reference_version} ===")
        run_version(
            version=args.test_version,
            site_config=site_config,
            config_folder=args.config_folder,
            reference_folder=reference_folder,
            only_reference=False,
            save_sim_data=args.save_sim_data,
            debug=args.debug,
        )


if __name__ == "__main__":
    main()
