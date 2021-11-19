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
from typing import List
from typing import Optional
from typing import Union

from django import setup as django_setup
from django.conf import settings as django_settings
from django.template.loader import get_template

from aedttest.clusters.job_hosts import get_job_machines
from aedttest.logger import logger
from aedttest.logger import set_logger

from pyaedt import __file__ as _py_aedt_path  # noreorder


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
    """
    Main function that is executed by 'flit' CLI script and by executing this python file
    Returns:
        None
    """
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

        aedt_tester.run()
    except Exception as exc:
        logger.exception(str(exc))


class ElectronicsDesktopTester:
    def __init__(
        self,
        version: str,
        max_cores: Optional[int],
        max_tasks: Optional[int],
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

        self.report_data = {}

        self.machines_dict = {machine.hostname: machine.cores for machine in get_job_machines()}

        with open(config_file) as file:
            self.project_tests_config = json.load(file)

    def validate_config(self):
        """
        Make quick validation of --config-file [and --reference-file if present]
        Checks that distribution is specified correctly and that projects in
        reference identical to configuration

        Returns:
            None
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
        """
        Main function to start test sweet
        Returns: None
        """
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

            [th.join() for th in threads_list]  # wait for all threads to finish before delete folder

            self.render_main_html(finished=True)  # make thread-safe render
            combined_report_path = self.create_combined_report()
            msg = f"Job is completed.\nReference result file is stored under {combined_report_path}"

            if not self.only_reference:
                msg += f"\nYou can view report by opening in web browser: {self.results_path / 'main.html'}"

            logger.info(msg)

    def create_combined_report(self) -> Path:
        combined_report_path = self.results_path / "reference_results.json"
        combined_data = {"error_exception": [], "aedt_version": self.version, "projects": {}}

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
        """
        Validate that we have enough hardware resources to run requested configuration
        Returns:
            None
        """

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
        """
        Copy static web parts (HTML, CSS, JS).
        Set all projects status to be 'Queued'

        Returns:
            None
        """
        if self.results_path.exists():
            remove_tree(str(self.results_path))
        copy_path_to(str(MODULE_DIR / "static" / "css"), str(self.results_path))
        copy_path_to(str(MODULE_DIR / "static" / "js"), str(self.results_path))

        for project_name, project_config in self.project_tests_config.items():
            self.report_data[project_name] = {
                "cores": project_config["distribution"]["cores"],
                "status": "queued",
                "link": None,
                "time": time_now(),
            }

        self.render_main_html()

    def render_main_html(self, finished: bool = False) -> None:
        """
        Renders main report page.
        Using self.report_data updates django template with the data.

        Returns:
            None
        """
        data = MAIN_PAGE_TEMPLATE.render(context={"projects": self.report_data, "finished": finished})
        with open(self.results_path / "main.html", "w") as file:
            file.write(data)

    def render_project_html(self, project_name: str, project_report: dict):
        """
        Renders project report page. Creates new page if none exists
        Updates django template with XY plots, mesh, etc data.

        Args:
            project_name: name of the project to render
            project_report: (dict) data to render on plots

        Returns:
            None
        """
        page_ctx = {
            "plots": project_report["plots"],
            "project_name": project_name,
            "errors": project_report["error_exception"],
            "mesh": project_report["mesh"],
            "sim_time": project_report["simulation_time"],
            "slider_limit": project_report["slider_limit"],
        }
        data = PROJECT_PAGE_TEMPLATE.render(context=page_ctx)
        with open(self.results_path / f"{project_name}.html", "w") as file:
            file.write(data)

    def task_runner(self, project_name: str, project_path: str, project_config: dict, allocated_machines: dict) -> None:
        """
        Task runner that is called by each thread.
        Calls update of HTML pages status, starts AEDT process

        Args:
            project_name: (str) name of the project to start
            project_path: (str) path to the project
            project_config: (dict) configuration of project, distribution, etc
            allocated_machines: (dict) machines and cores that were allocated for this task

        Returns:
            None
        """
        self.report_data[project_name]["time"] = time_now()
        self.report_data[project_name]["status"] = "running"
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

        status = "success" if not project_report["error_exception"] else "fail"
        if not self.only_reference:
            self.render_project_html(project_name, project_report)
            self.report_data[project_name]["link"] = f"{project_name}.html"

        self.report_data[project_name]["time"] = time_now()
        self.report_data[project_name]["status"] = status

        self.render_main_html()
        self.active_tasks -= 1

    def prepare_project_report(self, project_name, project_path):
        report_file = Path(project_path).parent / f"{project_name}.json"
        project_report = {"plots": [], "error_exception": [], "mesh": [], "simulation_time": [], "slider_limit": 0}
        if not report_file.exists():
            project_report["error_exception"].append(f"Project report for {project_name} does not exist")
        else:
            copy_path_to(str(report_file), str(self.results_path / "reference_folder"))
            if self.only_reference:
                return project_report
            with open(report_file) as file:
                project_data = json.load(file)

            # todo handle if some reference data does not exist
            # todo handle if current report misses something from reference data
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

        return project_report

    def extract_curve_data(self, design_data, design_name, project_name, project_report):
        for report_name, report_data in design_data["report"].items():
            for trace_name, trace_data in report_data.items():
                for curve_name, curve_data in trace_data["curves"].items():
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

                    project_report["slider_limit"] = max(project_report["slider_limit"], max_delta_perc)

                    project_report["plots"].append(
                        {
                            "name": f"{design_name}:{report_name}:{trace_name}:{curve_name}",
                            "id": unique_id(),
                            "x_label": f'"{trace_data["x_name"]} [{trace_data["x_unit"]}]"',
                            "y_label": f'"[{trace_data["y_unit"]}]"',
                            "x_axis": curve_data["x_data"],
                            "version_ref": self.reference_data["aedt_version"],
                            "y_axis_ref": y_ref_data,
                            "version_now": str(self.version),
                            "y_axis_now": curve_data["y_data"],
                            "diff": difference,
                            "delta": max_delta_perc,
                        }
                    )

    def extract_mesh_or_time_data(self, key_name, design_data, design_name, project_name, project_report):
        for variation_name, variation_data in design_data[key_name].items():
            for setup_name, current_stat in variation_data.items():
                reference_dict = self.reference_data["projects"][project_name]["designs"][design_name][key_name]
                if variation_name not in reference_dict:
                    project_report["error_exception"].append(
                        f"Variation ({variation_name}) was not found in reference results for design: {design_name}"
                    )
                    continue

                reference = reference_dict[variation_name][setup_name]
                project_report[key_name].append(
                    {
                        "name": f"{design_name}:{setup_name}:{variation_name}",
                        "ref": reference,
                        "current": current_stat,
                    }
                )

    def allocator(self) -> Iterable:
        """
        Generator that yields resources. Waits until resources are available

        Yields:
            (str, dict) (project name to run, allocated machines)
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
    """
    Allocate task on one or more nodes. Will use MPI and split the job
    If multiple parametric tasks are defined, distribute uniform
    Args:
        distribution_config: (dict) data about required distribution for the project
        machines_dict: (dict) all available machines in pool

    Returns:
        (dict) allocated machines for the project or None if not allocated
    """

    if distribution_config.get("single_node", False):
        return

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
        return

    return allocated_machines


def allocate_task_within_node(
    distribution_config: Dict[str, int], machines_dict: Dict[str, int]
) -> Dict[str, Dict[str, str]]:
    """
    Try to fit a task in a node without splitting
    Args:
        distribution_config: (dict) data about required distribution for the project
        machines_dict: (dict) all available machines in pool

    Returns:
        (dict) allocated machines for the project or None if not allocated
    """

    for machine, cores in machines_dict.items():
        if cores - distribution_config["cores"] >= 0:
            return {
                machine: {
                    "cores": distribution_config["cores"],
                    "tasks": distribution_config.get("parametric_tasks", 1),
                }
            }


def copy_proj(project_name: str, project_config: Dict[str, Any], dst: str) -> str:
    """
    Copy project to run location, temp by default
    Args:
        project_name: (str) name of the project to start
        project_config: (dict) configuration of project, distribution, etc
        dst: (str) path where to copy

    Returns:
        (str) location where it was copied
    """
    src = project_config.get("path", project_name + ".aedt")
    return copy_path_to(src, dst)


def copy_dependencies(project_config: Dict[str, Any], dst: str) -> None:
    """
    Copies project dependencies to run location
    Args:
        project_config: (dict) configuration of project, distribution, etc
        dst: (str) path where to copy

    Returns:
        None
    """
    deps = project_config.get("dependencies", None)

    if isinstance(deps, list):
        for dep in deps:
            copy_path_to(dep, dst)
    elif isinstance(deps, str):
        copy_path_to(deps, dst)


def copy_path_to(src: str, dst: str) -> Union[str, List[str]]:
    """
    Copy path from src to dst
    If src is a relative path, preserves relative folder tree
    Args:
        src: (str) path with copy target, relative or absolute
        dst: (str) path where to copy

    Returns:
        (str) path to copied file or (list) with paths if folder is copied

    """
    src = Path(src.replace("\\", "/"))
    if not src.is_absolute() and len(src.parents) > 1:
        unpack_dst = Path(dst) / src.parents[0]
        if not src.is_file():
            unpack_dst /= src.name
    elif not src.is_file():
        unpack_dst = Path(dst) / src.name
    else:
        unpack_dst = Path(dst)

    unpack_dst = str(unpack_dst)
    mkpath(unpack_dst)
    src = src.expanduser().resolve()

    if not src.exists():
        raise FileExistsError(f"File {src} doesn't exist")

    if src.is_file():
        file_path = copy_file(str(src), unpack_dst)
        return file_path[0]
    else:
        return copy_tree(str(src), unpack_dst)


def mkdtemp_persistent(*args, persistent=True, **kwargs):
    """
    Provides a context manager to create a temporary/permanent directory depending on 'persistent' argument

    Args:
        *args: TemporaryDirectory args
        persistent: (bool) if True, create permanent directory
        **kwargs: TemporaryDirectory kwargs

    Returns:
        Context manager with temp directory from 'tempfile' module
    """
    if persistent:

        @contextmanager
        def normal_mkdtemp():
            yield tempfile.mkdtemp(*args, **kwargs)

        return normal_mkdtemp()
    else:
        return tempfile.TemporaryDirectory(*args, **kwargs)


def generator_unique_id():
    i = 1
    while True:
        yield f"a{i}"
        i += 1


id_generator = generator_unique_id()


def unique_id():
    return next(id_generator)


def execute_aedt(
    version: str,
    script: str = None,
    script_args: str = None,
    project_path: str = None,
    machines: dict = None,
    distribution_config: dict = None,
) -> None:
    """
    Execute single instance of Electronics Desktop

    Args:
        version: version to run
        script: path to the script
        script_args: arguments to the script
        project_path: path to the project
        machines: (dict) machine specification for current job
        distribution_config: (dict) distribution configuration for the job

    Returns: None
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

    if distribution_config.get("distribution_types", None):
        command.append("-distributed")
        dist_type_str = ",".join([dist_type for dist_type in distribution_config["distribution_types"]])
        command.append(f"includetypes={dist_type_str}")

        if distribution_config.get("multilevel_distribution_tasks", 0) > 0:
            command.append("maxlevels=2")
            command.append(f"numlevel1={distribution_config['multilevel_distribution_tasks']}")

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
        command.append(project_path)

    logger.debug(f"Execute {subprocess.list2cmdline(command)}")
    subprocess.call(command)


def get_aedt_executable_path(version: str) -> str:
    """
    Get platform specific Electronics Desktop executable path

    Args:
        version: (str) version of Electronics Desktop

    Returns:
        (str) path to Electronics Desktop executable
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


def time_now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_arguments() -> argparse.Namespace:
    """
    Parse CLI arguments

    Returns:
        (argparse.Namespace) validated arguments
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
    set_logger(logging_file=CWD_DIR / "aedt_test_framework.log", level=log_level)

    if not cli_args.only_reference and not cli_args.reference_file:
        raise ValueError("Either set --only-reference flag or provide path via --reference-file")

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
