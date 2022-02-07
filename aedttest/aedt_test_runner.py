import argparse
import datetime
import json
import os
import platform
import re
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from distutils.dir_util import copy_tree
from distutils.dir_util import mkpath
from distutils.dir_util import remove_tree
from distutils.file_util import copy_file
from pathlib import Path
from time import sleep
from typing import Any
from typing import Dict
from typing import Iterable
from typing import Iterator
from typing import List
from typing import Optional
from typing import Tuple
from typing import Union

from django import setup as django_setup
from django.conf import settings as django_settings
from django.template.loader import get_template

from aedttest.clusters.job_hosts import get_job_machines
from aedttest.logger import logger
from aedttest.logger import set_logger

from pyaedt import __file__ as _py_aedt_path  # isort: skip

MODULE_DIR = Path(__file__).resolve().parent
CWD_DIR = Path.cwd()

# configure Django templates
django_settings.configure(
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [MODULE_DIR / "static" / "templates"],  # if you want the templates from a file
        },
    ]
)
django_setup()
MAIN_PAGE_TEMPLATE = get_template("main.html")
PROJECT_PAGE_TEMPLATE = get_template("project-report.html")


def main() -> None:
    """Main function that is executed by ``flit`` CLI script and by executing this python file."""
    try:
        cli_args = parse_arguments()
    except ValueError as exc:
        logger.error(str(exc))
        raise SystemExit(1)

    aedt_tester = ElectronicsDesktopTester(
        version=cli_args.aedt_version,
        max_cores=cli_args.max_cores,
        max_tasks=cli_args.max_tasks,
        config_file=cli_args.config_file,
        out_dir=cli_args.out_dir,
        save_projects=cli_args.save_sim_data,
        only_reference=cli_args.only_reference,
        reference_file=cli_args.reference_file,
    )
    try:
        if not cli_args.suppress_validation:
            aedt_tester.validate_config()
            if cli_args.only_validate:
                return

        if len(aedt_tester.machines_dict) > 1 and not cli_args.rsm_is_started:
            if not cli_args.rsm_path or not os.path.isfile(cli_args.rsm_path):
                raise ValueError("Path to RSM service is not provided or wrong")
            if platform.system() == "Windows":
                raise ValueError("You must start RSM service on Windows manually and enable flag --rsm-is-started")

            for machine in aedt_tester.machines_dict:
                out = subprocess.check_output(["ssh", machine, f"{cli_args.rsm_path} start"])
                logger.debug(f"{machine}: {out.decode().strip()}")

        try:
            aedt_tester.run()
        finally:
            if len(aedt_tester.machines_dict) > 1 and not cli_args.rsm_is_started:
                for machine in aedt_tester.machines_dict:
                    out = subprocess.check_output(["ssh", machine, f"{cli_args.rsm_path} stop"])
                    logger.debug(f"{machine}: {out.decode().strip()}")

    except Exception as exc:
        logger.exception(str(exc))


class ElectronicsDesktopTester:
    def __init__(
        self,
        version: str,
        max_cores: int,
        max_tasks: int,
        config_file: Union[str, Path],
        out_dir: Optional[str],
        save_projects: Optional[bool],
        only_reference: Optional[bool],
        reference_file: Union[str, Path],
    ) -> None:
        logger.info(f"Initialize new Electronics Desktop Test run. Configuration file is {config_file}")
        self.version = version
        self.max_cores = max_cores
        self.max_tasks = max_tasks
        self.active_tasks = 0
        self.out_dir = Path(out_dir) if out_dir else CWD_DIR
        self.results_path = self.out_dir / "results"
        self.proj_dir = self.out_dir if save_projects else None
        self.only_reference = only_reference
        self.reference_data = {}
        if not only_reference:
            with open(reference_file) as file:
                self.reference_data = json.load(file)

        self.script = str(MODULE_DIR / "simulation_data.py")
        self.script_args = f"--pyaedt-path={Path(_py_aedt_path).parent.parent}"

        self.report_data: Dict[str, Any] = {}

        self.machines_dict = {machine.hostname: machine.cores for machine in get_job_machines()}

        with open(config_file) as file:
            self.project_tests_config = json.load(file)

    def validate_config(self) -> None:
        """Make quick validation of --config-file [and --reference-file if present].

        Checks that distribution is specified correctly and that projects in
        reference identical to configuration.

        """
        for project_name, config in self.project_tests_config.items():
            distribution_config = config["distribution"]
            if "parametric_tasks" in distribution_config:
                tasks = distribution_config["parametric_tasks"]
                cores = distribution_config["cores"]
                if not isinstance(tasks, int):
                    raise KeyError("'parametric_tasks' key must be integer")

                if tasks < 1:
                    raise KeyError("'parametric_tasks' key must be >= 1")

                if tasks > cores:
                    # implicitly checks that cores >= 1
                    raise KeyError("'parametric_tasks' key must be <= 'cores'")

                if cores % tasks != 0:
                    raise KeyError("'cores' divided by 'parametric_tasks' must be integer")

        if not self.only_reference:
            if "projects" not in self.reference_data:
                raise KeyError("'projects' key is not specified in Reference File")

            not_found_in_conf = set(self.reference_data["projects"]) - set(self.project_tests_config)
            if not_found_in_conf:
                msg = (
                    f"Following projects defined in reference results: {', '.join(list(not_found_in_conf))}"
                    ", but not specified in current configuration file"
                )
                raise KeyError(msg)

            not_found_in_ref = set(self.project_tests_config) - set(self.reference_data["projects"])
            if not_found_in_ref:
                msg = (
                    f"Following projects defined in configuration file: {', '.join(list(not_found_in_ref))}"
                    ", but not found in reference results file"
                )
                raise KeyError(msg)

        logger.info("Configuration validation is successful")

    def run(self) -> None:
        """Main function to start test suite."""
        self.validate_hardware()
        self.initialize_results()

        threads_list = []
        with mkdtemp_persistent(
            persistent=(self.proj_dir is not None), dir=self.proj_dir, prefix=f"{self.version}_"
        ) as tmp_dir:
            for project_name, allocated_machines in self.allocator():
                project_config = self.project_tests_config[project_name]

                logger.info(f"Start project {project_name}")
                copy_dependencies(project_config, tmp_dir)
                project_path = copy_proj(project_name, project_config, tmp_dir)

                thread_kwargs = {
                    "project_path": project_path,
                    "allocated_machines": allocated_machines,
                    "project_config": project_config,
                    "project_name": project_name,
                }
                thread = threading.Thread(target=self.task_runner, daemon=True, kwargs=thread_kwargs)
                thread.start()
                threads_list.append(thread)

            for th in threads_list:
                # wait for all threads to finish before delete folder
                th.join()

            self.render_main_html(finished=True)  # make thread-safe render
            combined_report_path = self.create_combined_report()
            msg = f"Job is completed.\nReference result file is stored under {combined_report_path}"

            if not self.only_reference:
                msg += f"\nYou can view report by opening in web browser: {self.results_path / 'main.html'}"

            logger.info(msg)

    def create_combined_report(self) -> Path:
        """Reads all .json files in ``reference_folder`` and dumps it to single file ``'reference_results.json'``.

        Returns
        -------
        Path
            Path to the combined .json file.

        """
        combined_report_path = self.results_path / "reference_results.json"
        combined_data: Dict[str, Any] = {"error_exception": [], "aedt_version": self.version, "projects": {}}

        reference_folder = self.results_path / "reference_folder"
        if not reference_folder.exists():
            raise RuntimeError("Reference results were not generated. Probably projects failed to run")

        for json_file in reference_folder.iterdir():
            with open(json_file) as file:
                single_data = json.load(file)
                combined_data["projects"][json_file.stem] = single_data

        with open(combined_report_path, "w") as file:
            json.dump(combined_data, file, indent=4)

        return combined_report_path

    def validate_hardware(self) -> None:
        """Validate that we have enough hardware resources to run requested configuration."""
        all_cores = [val for val in self.machines_dict.values()]
        total_available_cores = sum(all_cores)
        max_machine_cores = max(all_cores)
        for proj in self.project_tests_config:
            proj_cores = self.project_tests_config[proj]["distribution"]["cores"]
            if proj_cores > total_available_cores or (
                self.project_tests_config[proj]["distribution"].get("single_node", False)
                and proj_cores > max_machine_cores
            ):
                raise ValueError(f"{proj} requires {proj_cores} cores. Not enough resources to run")

    def initialize_results(self) -> None:
        """Copy static web parts (HTML, CSS, JS).

        Mutate ``self.report_data``. Set all projects status to be ``'Queued'``, default link and delta.

        """
        if self.results_path.exists():
            remove_tree(str(self.results_path))
        copy_path_to(str(MODULE_DIR / "static" / "css"), str(self.results_path))
        copy_path_to(str(MODULE_DIR / "static" / "js"), str(self.results_path))

        self.report_data["all_delta"] = 1 if not self.only_reference else None
        self.report_data["projects"] = {}

        for project_name, project_config in self.project_tests_config.items():
            self.report_data["projects"][project_name] = {
                "cores": project_config["distribution"]["cores"],
                "status": "queued",
                "link": None,
                "delta": None,
                "time": time_now(),
            }

            if not self.only_reference:
                # initialize integer for proper rendering
                self.report_data["projects"][project_name]["delta"] = 0

        self.render_main_html()

    def render_main_html(self, finished: bool = False) -> None:
        """Renders main report page.

        Using ``self.report_data`` updates django template with the data.

        Parameters
        ----------
        finished : bool, default=False
             When True send a context to stop refreshing the HTML page.

        """
        ctx = {
            "projects": self.report_data["projects"],
            "finished": finished,
            "all_delta": self.report_data["all_delta"],
            "has_reference": not self.only_reference,
        }
        data = MAIN_PAGE_TEMPLATE.render(context=ctx)
        with open(self.results_path / "main.html", "w") as file:
            file.write(data)

    def render_project_html(self, project_name: str, project_report: Dict[str, Union[List[Any], int]]) -> None:
        """Renders project report page.

        Creates new page if none exists.
        Updates django template with XY plots, mesh, etc data.

        Parameters
        ----------
        project_name : str
            Name of the project to render.
        project_report : dict
            Data to render on plots.

        """
        page_ctx = {
            "plots": project_report["plots"],
            "project_name": project_name,
            "errors": project_report["error_exception"],
            "mesh": project_report["mesh"],
            "sim_time": project_report["simulation_time"],
            "slider_limit": project_report["slider_limit"],
            "has_reference": not self.only_reference,
        }
        data = PROJECT_PAGE_TEMPLATE.render(context=page_ctx)
        with open(self.results_path / f"{project_name}.html", "w") as file:
            file.write(data)

    def task_runner(
        self, project_name: str, project_path: str, project_config: Dict[str, Any], allocated_machines: Dict[str, Any]
    ) -> None:
        """Task runner that is called by each thread.

        Mutates ``self.report_data["projects"]`` and ``self.machines_dict``
        Calls update of HTML pages status, starts AEDT process, calls render of project_name.html

        Parameters
        ----------
        project_name : str
            Name of the project to start.
        project_path : str
            Path to the project.
        project_config : dict
            Configuration of project, distribution, etc.
        allocated_machines : dict
            Machines and cores that were allocated for this task.
        """
        self.report_data["projects"][project_name]["time"] = time_now()
        self.report_data["projects"][project_name]["status"] = "running"
        self.render_main_html()

        execute_aedt(
            self.version,
            self.script,
            self.script_args,
            project_path,
            allocated_machines,
            distribution_config=project_config["distribution"],
        )
        logger.debug(f"Project {project_name} analyses finished. Prepare report.")

        # return cores back
        for machine in allocated_machines:
            self.machines_dict[machine] += allocated_machines[machine]["cores"]

        project_report = self.prepare_project_report(project_name, project_path)

        self.render_project_html(project_name, project_report)

        status = "success" if not project_report["error_exception"] else "fail"
        self.report_data["projects"][project_name].update(
            {
                "link": f"{project_name}.html",
                "delta": project_report["slider_limit"],
                "time": time_now(),
                "status": status,
            }
        )

        self.render_main_html()
        self.active_tasks -= 1

    def prepare_project_report(self, project_name: str, project_path: str) -> Dict[str, Union[List[Any], int]]:
        """Prepare project report dictionary that is required by ``render_project_html()``.

        Parameters
        ----------
        project_name : str
            Name of the project.
        project_path : str
            Path to the project.

        Returns
        -------
        project_report : dict
            project report dictionary that is required by ``render_project_html()``.

        """
        report_file = Path(project_path).parent / f"{project_name}.json"
        project_report: Dict[str, Union[List[Any], Any]] = {
            "plots": [],
            "error_exception": [],
            "mesh": [],
            "simulation_time": [],
            "slider_limit": 0,
        }
        project_data = self.check_all_results_present(project_report["error_exception"], report_file, project_name)
        if project_report["error_exception"]:
            # some keys are missing
            return project_report

        try:
            copy_path_to(str(report_file), str(self.results_path / "reference_folder"))
            project_report["error_exception"] += project_data["error_exception"]
            for design_name, design_data in project_data["designs"].items():
                # get mesh data
                self.extract_mesh_or_time_data("mesh", design_data, design_name, project_name, project_report)
                # get simulation time
                self.extract_mesh_or_time_data(
                    "simulation_time", design_data, design_name, project_name, project_report
                )
                # extract XY curve data
                self.extract_curve_data(design_data, design_name, project_name, project_report)
        except Exception as exc:
            project_report["error_exception"].append(str(exc))

        return project_report

    def check_all_results_present(
        self, project_exceptions: List[str], report_file: Path, project_name: str
    ) -> Dict[str, Any]:
        """Check that report file exists.

        Check that project report exists in reference data.
        Check that all keys present in the reference data are also in the current run data.
        Check that all keys present in the current run data are also in the reference data.

        Parameters
        ----------
        project_exceptions : list
            List to append with errors.
        report_file : Path
            JSON file path with results.
        project_name : str
            Name of the project.

        Returns
        -------
        project_data : dict
            Dictionary loaded from .json file.

        """
        project_data: Dict[str, Any] = {}
        if not report_file.exists():
            project_exceptions.append(f"Project report for {project_name} does not exist")
            return project_data

        with open(report_file) as file:
            project_data = json.load(file)

        if not self.only_reference:
            if project_name not in self.reference_data["projects"]:
                project_exceptions.append(f"Project report for {project_name} does not exist in reference file")
            else:
                compare_keys(
                    self.reference_data["projects"][project_name],
                    project_data,
                    exceptions_list=project_exceptions,
                    results_type="reference",
                )
                compare_keys(
                    project_data,
                    self.reference_data["projects"][project_name],
                    exceptions_list=project_exceptions,
                    results_type="current",
                )

        return project_data

    def extract_curve_data(
        self,
        design_data: Dict[str, Any],
        design_name: str,
        project_name: str,
        project_report: Dict[str, Union[List[Any], Any]],
    ) -> None:
        """Extract all XY curves for a particular design.

        Mutate ``project_report``.

        Parameters
        ----------
        design_data : dict
            All the data related to a single design in project_name.
        design_name : str
            Name of the design.
        project_name : str
            Name of the project.
        project_report : dict
            Project report dictionary that is required by 'render_project_html()'.

        """
        for report_name, report_data in design_data["report"].items():
            for trace_name, trace_data in report_data.items():
                for curve_name, curve_data in trace_data["curves"].items():
                    plot_data = {
                        "name": f"{design_name}:{report_name}:{trace_name}:{curve_name}",
                        "id": unique_id(),
                        "x_label": f'"{trace_data["x_name"]} [{trace_data["x_unit"]}]"',
                        "y_label": f'"[{trace_data["y_unit"]}]"',
                        "x_axis": curve_data["x_data"],
                        "version_ref": -1,
                        "y_axis_ref": [],
                        "version_now": str(self.version),
                        "y_axis_now": curve_data["y_data"],
                        "diff": [],
                        "delta": -1,
                    }

                    if not self.only_reference:
                        y_ref_data = self.reference_data["projects"][project_name]["designs"][design_name]["report"][
                            report_name
                        ][trace_name]["curves"][curve_name]["y_data"]

                        if len(y_ref_data) != len(curve_data["y_data"]):
                            msg = (
                                f"Number of trace points in reference data [{len(y_ref_data)}] isn't equal to "
                                f"number in current data [{len(curve_data['y_data'])}]"
                            )
                            project_report["error_exception"].append(msg)
                            continue

                        max_delta = 0
                        difference = []
                        for ref, actual in zip(y_ref_data, curve_data["y_data"]):
                            difference.append(ref - actual)
                            if actual != 0:
                                # if 0, just skip, no sense for 'infinite' delta
                                max_delta = max(max_delta, abs(1 - ref / actual))
                        max_delta_perc = round(max_delta * 100, 3)

                        # take always integer since ticks are integers, and +1 to allow to slide
                        project_report["slider_limit"] = max(project_report["slider_limit"], int(max_delta_perc) + 1)
                        plot_data.update(
                            {
                                "version_ref": self.reference_data["aedt_version"],
                                "y_axis_ref": y_ref_data,
                                "diff": difference,
                                "delta": max_delta_perc,
                            }
                        )

                    project_report["plots"].append(plot_data)

    def extract_mesh_or_time_data(
        self,
        key_name: str,
        design_data: Dict[str, Any],
        design_name: str,
        project_name: str,
        project_report: Dict[str, Union[List[Any], Any]],
    ) -> None:
        """Extract mesh or simulation time information.

        Mutate project_report.

        Parameters
        ----------
        key_name : str
            Mesh or simulation_time, depending on what to extract.
        design_data : dict
            All the data related to a single design in ``project_name``.
        design_name : str
            Name of the design.
        project_name : str
            Name of the project.
        project_report : dict
            Project report dictionary that is required by ``render_project_html()``.

        """
        for variation_name, variation_data in design_data[key_name].items():
            for setup_name, current_stat in variation_data.items():
                stat_dict = {
                    "name": f"{design_name}:{setup_name}:{variation_name}",
                    "current": current_stat,
                }
                if not self.only_reference:
                    reference_dict = self.reference_data["projects"][project_name]["designs"][design_name][key_name]
                    if variation_name not in reference_dict:
                        project_report["error_exception"].append(
                            f"Variation ({variation_name}) wasn't found in reference results for design: {design_name}"
                        )
                        continue

                    stat_dict["ref"] = reference_dict[variation_name][setup_name]

                project_report[key_name].append(stat_dict)

    def allocator(self) -> Iterable[Tuple[str, Dict[str, Dict[str, int]]]]:
        """Generator that yields resources.

        Waits until resources are available.

        Yields
        ------
        proj_name : str
            Name of the project.
        allocated_machines : Dict
            Allocated machines.
        """
        sorted_by_cores_desc = sorted(
            self.project_tests_config.keys(),
            key=lambda x: self.project_tests_config[x]["distribution"]["cores"],
            reverse=True,
        )
        proj_name = ""
        while sorted_by_cores_desc:
            if self.active_tasks >= self.max_tasks:
                logger.debug("Number of maximum tasks limit is reached. Wait for job to finish")
                sleep(4)
                continue

            allocated_machines = None
            for proj_name in sorted_by_cores_desc:
                # first try to fit all jobs within a single node for stability, since projects are sorted
                # by cores, this ensures that we have optimized resource utilization
                allocated_machines = allocate_task_within_node(
                    self.project_tests_config[proj_name]["distribution"], self.machines_dict
                )
                if allocated_machines:
                    break
            else:
                for proj_name in sorted_by_cores_desc:
                    # since no more machines to fit the whole project, let's split it across machines
                    allocated_machines = allocate_task(
                        self.project_tests_config[proj_name]["distribution"], self.machines_dict
                    )
                    if allocated_machines:
                        break
                else:
                    msg = "Waiting for resources. Cores left per machine:\n"
                    for machine, cores in self.machines_dict.items():
                        msg += f"{machine} has {cores} core(s) free\n"

                    logger.debug(msg)
                    sleep(5)

            if allocated_machines:
                for machine in allocated_machines:
                    self.machines_dict[machine] -= allocated_machines[machine]["cores"]

                sorted_by_cores_desc.remove(proj_name)
                self.active_tasks += 1
                yield proj_name, allocated_machines


def allocate_task(
    distribution_config: Dict[str, int], machines_dict: Dict[str, int]
) -> Optional[Dict[str, Dict[str, int]]]:
    """Allocate task on one or more nodes.

    Will use MPI and split the job.
    If multiple parametric tasks are defined, distribute uniformly.

    Parameters
    ----------
    distribution_config : dict
        Data about required distribution for the project.
    machines_dict : dict
        All available machines in pool.

    Returns
    -------
    dict
        Allocated machines for the project or ``None`` if not allocated.

    """
    if distribution_config.get("single_node", False):
        return None

    allocated_machines = {}
    tasks = distribution_config.get("parametric_tasks", 1)
    cores_per_task = int(distribution_config["cores"] / tasks)
    to_fill = distribution_config["cores"]

    for machine, cores in machines_dict.items():
        if tasks == 1:
            allocate_cores = cores if to_fill - cores > 0 else to_fill
            allocate_tasks = 1
        else:
            # if tasks are specified, we cannot allocate less cores than in cores_per_task
            if cores < cores_per_task:
                continue

            allocate_tasks = min((cores // cores_per_task, tasks))
            tasks -= allocate_tasks
            allocate_cores = cores_per_task * allocate_tasks

        allocated_machines[machine] = {
            "cores": allocate_cores,
            "tasks": allocate_tasks,
        }
        to_fill -= allocate_cores

        if to_fill <= 0:
            break

    if to_fill > 0:
        # not enough resources
        logger.debug("Not enough resources to split job")
        return None

    return allocated_machines


def allocate_task_within_node(
    distribution_config: Dict[str, int], machines_dict: Dict[str, int]
) -> Dict[str, Dict[str, int]]:
    """Try to fit a task in a node without splitting.

    Parameters
    ----------
    distribution_config : dict
        Data about required distribution for the project.
    machines_dict : dict
        All available machines in pool.

    Returns
    -------
    machines : dict
        Allocated machines for the project or ``None`` if not allocated.

    """
    for machine, cores in machines_dict.items():
        if cores - distribution_config["cores"] >= 0:
            return {
                machine: {
                    "cores": distribution_config["cores"],
                    "tasks": distribution_config.get("parametric_tasks", 1),
                }
            }
    return {}


def copy_proj(project_name: str, project_config: Dict[str, Any], dst: str) -> Union[str, List[str]]:
    """Copy project to run location, temp by default.

    Parameters
    ----------
    project_name : str
        Name of the project to start.
    project_config : dict
        Configuration of project, distribution, etc.
    dst : str
        Path where to copy.

    Returns
    -------
    path : str
        Location where it was copied.

    """
    src = project_config.get("path", project_name + ".aedt")
    return copy_path_to(src, dst)


def copy_dependencies(project_config: Dict[str, Any], dst: str) -> None:
    """Copies project dependencies to run location.

    Parameters
    ----------
    project_config : dict
        Configuration of project, distribution, etc.
    dst : str
        Path where to copy.

    """
    deps = project_config.get("dependencies", None)

    if isinstance(deps, list):
        for dep in deps:
            copy_path_to(dep, dst)
    elif isinstance(deps, str):
        copy_path_to(deps, dst)


def copy_path_to(src: str, dst: str) -> Union[str, List[str]]:
    """Copy path from src to dst.

    If ``src`` is a relative path, preserves relative folder tree.

    Parameters
    ----------
    src : str
        Path with copy target, relative or absolute.
    dst : str
        Path where to copy.

    Returns
    -------
    path: str or list
        Path to copied file or list with paths if folder is copied.

    """
    src_path = Path(src.replace("\\", "/"))
    if not src_path.is_absolute() and len(src_path.parents) > 1:
        unpack_dst = Path(dst) / src_path.parents[0]
        if not src_path.is_file():
            unpack_dst /= src_path.name
    elif not src_path.is_file():
        unpack_dst = Path(dst) / src_path.name
    else:
        unpack_dst = Path(dst)

    src_path = src_path.expanduser().resolve()
    if not src_path.exists():
        raise FileExistsError(f"File {src_path} doesn't exist")

    dst = str(unpack_dst)
    mkpath(dst)

    if src_path.is_file():
        file_path = copy_file(str(src_path), dst)
        return file_path[0]
    else:
        return copy_tree(str(src_path), dst)


def mkdtemp_persistent(*args: Any, persistent: bool = True, **kwargs: Any) -> Any:
    """Provides a context manager to create a temporary/permanent directory depending on 'persistent' argument

    Parameters
    ----------
    *args: Any
        TemporaryDirectory args
    persistent : bool, default=True
         If ``True``, create a permanent directory.
    **kwargs: Any
        TemporaryDirectory keyword arguments.

    Returns
    -------
    tempfile.TemporaryDirectory
        Context manager with temp directory from ``tempfile`` module.

    """
    if persistent:

        @contextmanager
        def normal_mkdtemp() -> Iterator[str]:
            yield tempfile.mkdtemp(*args, **kwargs)

        return normal_mkdtemp()
    else:
        return tempfile.TemporaryDirectory(*args, **kwargs)


def generator_unique_id() -> Iterator[str]:
    """Generator that incrementally yields new IDs."""
    i = 1
    while True:
        yield f"a{i}"
        i += 1


id_generator = generator_unique_id()


def unique_id() -> str:
    """When called runs generator to pick new unique ID.

    Returns
    -------
    id : str
        New ID.

    """
    return next(id_generator)


def execute_aedt(
    version: str,
    script: Optional[str] = None,
    script_args: Optional[str] = None,
    project_path: Optional[str] = None,
    machines: Optional[Dict[str, Any]] = None,
    distribution_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Execute single instance of Electronics Desktop.

    Parameters
    ----------
    version : str
        Version to run.
    script : str, optional
        Path to the script.
    script_args : str, optional
        Arguments to the script.
    project_path : str, optional
        Path to the project.
    machines : dict, optional
        Machine specification for current job.
    distribution_config : dict, optional
        Distribution configuration for the job.

    """
    aedt_path = get_aedt_executable_path(version)

    command = [
        aedt_path,
    ]

    if machines is not None:
        command.append("-machinelist")
        host_list = "list=" + ",".join(
            [f"{name}:{conf['tasks']}:{conf['cores']}:90%" for name, conf in machines.items()]
        )
        command.append(host_list)

    if distribution_config and distribution_config.get("distribution_types", None):
        command.append("-distributed")
        dist_type_str = ",".join([dist_type for dist_type in distribution_config["distribution_types"]])
        command.append(f"includetypes={dist_type_str}")

        tasks = distribution_config.get("multilevel_distribution_tasks", 0)
        if tasks > 0:
            command.append("maxlevels=2")
            command.append(f"numlevel1={tasks}")

    if script is not None:
        command += [
            "-ng",
            "-features=SF6694_NON_GRAPHICAL_COMMAND_EXECUTION",
            "-RunScriptAndExit",
            script,
        ]
        if script_args is not None:
            command += [
                "-ScriptArgs",
                f'"{script_args}"',
            ]

    if project_path is not None:
        log_path = os.path.splitext(project_path)[0] + ".log"
        command += [
            "-LogFile",
            log_path,
            project_path,
        ]

    logger.debug(f"Execute {subprocess.list2cmdline(command)}")
    # filter variable to avoid AEDT thinking it was submitted by scheduler
    env = {}
    filtered = []
    for key, val in os.environ.items():
        if (
            "sge" not in key.lower()
            and "slurm" not in key.lower()
            and "lsf" not in key.lower()
            and "lsb" not in key.lower()
            and "pbs" not in key.lower()
            and "PE_HOSTFILE" not in key.lower()
        ):
            env[key] = val
        else:
            filtered.append(key)

    logger.debug(f"Variables filtered: {','.join(filtered)}")
    logger.debug(f"Variables applied: {env}")

    output = subprocess.check_output(command, env=env)
    logger.debug(output.decode())


def get_aedt_executable_path(version: str) -> str:
    """Get platform specific Electronics Desktop executable path.

    Parameters
    ----------
    version : str
        Version of Electronics Desktop.

    Returns
    -------
    path : str
        Path to Electronics Desktop executable.

    """
    aedt_env = f"ANSYSEM_ROOT{version}"
    aedt_path = os.environ.get(aedt_env, None)
    if not aedt_path:
        raise ValueError(f"Environment variable {aedt_env} is not set.")

    if platform.system() == "Windows":
        executable = "ansysedt.exe"
    elif platform.system() == "Linux":
        executable = "ansysedt"
    else:
        raise SystemError("Platform is neither Windows nor Linux")

    aedt_path = os.path.join(aedt_path, executable)

    return aedt_path


def time_now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def compare_keys(
    dict_1: Dict[Any, Any],
    dict_2: Dict[Any, Any],
    exceptions_list: List[str],
    *,
    dict_path: str = "",
    results_type: str = "reference",
) -> None:
    """Compare that keys from ``dict_1`` are present in ``dict_2`` recursively.

    Mutates ``exceptions_list`` and appends errors if key is not present.

    """
    if dict_path:
        dict_path += "->"

    for key, val in dict_1.items():
        if key not in dict_2:
            exceptions_list.append(f"Key '{dict_path}{key}' does not exist in {results_type} results")
            continue
        if isinstance(val, dict):
            compare_keys(val, dict_2[key], exceptions_list, dict_path=f"{dict_path}{key}", results_type="reference")


def parse_arguments() -> argparse.Namespace:
    """Parse CLI arguments.

    Returns
    -------
    args : argparse.Namespace
        Validated arguments.

    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--aedt-version", required=True, help="Electronics Desktop version to test, e.g. 221")
    parser.add_argument("--config-file", required=True, help="Project config file path")
    parser.add_argument("--reference-file", help="Reference results file path")
    parser.add_argument("--only-reference", action="store_true", help="Only create reference results")
    parser.add_argument(
        "--only-validate", action="store_true", help="Only validate current --config-file [and --reference-file]"
    )
    parser.add_argument(
        "--suppress-validation",
        action="store_true",
        help="Suppress validation of config file and reference file (DANGEROUS)",
    )

    parser.add_argument(
        "--rsm-is-started",
        action="store_true",
        help="When job uses multiple nodes and user started RSM service on each node manually",
    )

    parser.add_argument(
        "--rsm-path",
        help="When job uses multiple nodes tool requires RSM, RSM will be auto-started from provided path",
    )

    parser.add_argument(
        "--out-dir", "-o", help="Output directory for reports and project files (if --save-sim-data set)"
    )
    parser.add_argument(
        "--save-sim-data", "-s", action="store_true", help="Save simulation data under output dir (--out-dir flag)"
    )
    parser.add_argument("--max-cores", "-c", type=int, help="total number of cores limit", default=99999)
    parser.add_argument("--max-tasks", "-t", type=int, help="total number of parallel tasks limit", default=99999)

    parser.add_argument("--debug", action="store_true", help="Adds additional DEBUG logs")
    cli_args = parser.parse_args()

    log_level = 10 if cli_args.debug else 20
    set_logger(logging_file=CWD_DIR / "aedt_test_framework.log", level=log_level, pyaedt_module=None)

    if not cli_args.only_reference and not cli_args.reference_file:
        raise ValueError("Either set --only-reference flag or provide path via --reference-file")

    if cli_args.rsm_path and cli_args.rsm_is_started:
        raise ValueError("--rsm-is-started and --rsm-path are mutually exclusive")

    if cli_args.suppress_validation and cli_args.only_validate:
        raise ValueError("--only-validate and --suppress-validation are mutually exclusive")

    if not (cli_args.max_cores or cli_args.max_tasks):
        logger.warning(
            "No limits are specified for current job. This may lead to failure if you lack of license or resources"
        )

    aedt_version_pattern = re.compile(r"\d\d\d$")
    if not aedt_version_pattern.match(cli_args.aedt_version):
        raise ValueError("Electronics Desktop version value is invalid. Valid format example: 221")

    if not os.path.isfile(cli_args.config_file):
        raise ValueError(f"Configuration file does not exist: {cli_args.config_file}")

    if cli_args.save_sim_data and not cli_args.out_dir:
        raise ValueError("Saving of simulation data was requested but output directory is not provided")

    return cli_args


if __name__ == "__main__":
    main()
