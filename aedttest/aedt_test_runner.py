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
from pyaedt import __file__ as _py_aedt_path

from aedttest.clusters.job_hosts import get_job_machines
from aedttest.logger import logger
from aedttest.logger import set_logger

__authors__ = "Maksim Beliaev, Bo Yang"

MODULE_DIR = Path(__file__).resolve().parent
CWD_DIR = Path.cwd()

# configure Django templates
django_settings.configure(
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [MODULE_DIR],  # if you want the templates from a file
        },
    ]
)
django_setup()
MAIN_PAGE_TEMPLATE = get_template("static/main.html")
PROJECT_PAGE_TEMPLATE = get_template("static/project-report.html")


class ElectronicsDesktopTester:
    def __init__(
        self,
        version: str,
        max_cores: int,
        max_tasks: int,
        config_file: str,
        out_dir: str,
        save_projects: bool,
        only_reference: bool,
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

        self.script = str(MODULE_DIR / "simulation_data.py")
        self.script_args = f"--pyaedt-path={Path(_py_aedt_path).parent.parent}"

        self.report_data = []

        self.machines_dict = {machine.hostname: machine.cores for machine in get_job_machines()}

        with open(config_file) as file:
            self.project_tests_config = json.load(file)

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

                logger.info(f"Add project {project_name}")
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

            if self.only_reference:
                combined_report_path = self.create_combined_report()
                msg = f"Reference result file is stored under {combined_report_path}"
            else:
                msg = (
                    "Job is completed.\n"
                    f"You can view output by opening in web browser: {self.results_path / 'main.html'}"
                )

            logger.info(msg)

    def create_combined_report(self) -> Path:
        combined_report_path = self.results_path / "reference_results.json"
        combined_data = {"error_exception": []}
        for json_file in (self.results_path / "reference_folder").iterdir():
            with open(json_file) as file:
                single_data = json.load(file)
                combined_data.update(single_data["designs"])
                combined_data["error_exception"] += single_data["error_exception"]
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
        copy_tree(str(MODULE_DIR / "static"), str(self.results_path))

        for project_name, project_config in self.project_tests_config.items():
            self.report_data.append(
                {
                    "name": project_name,
                    "cores": project_config["distribution"]["cores"],
                    "status": "queued",
                    "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        self.render_main_html(status="queued")

    def render_main_html(self, status: str, project_name: Optional[str] = None) -> None:
        """
        Renders main report page.
        Using self.report_data updates django template with the data.

        Args:
            status: status of the project to update, if project_name is specified
            project_name: name of the project to update status

        Returns:
            None
        """
        if project_name:
            for proj in self.report_data:
                if proj["name"] == project_name:
                    proj["status"] = status
                    proj["time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    break

        data = MAIN_PAGE_TEMPLATE.render(context={"projects": self.report_data})
        with open(self.results_path / "main.html", "w") as file:
            file.write(data)

    def render_project_html(self, project_name: str, project_report: dict):
        """
        Renders project report page. Creates new page if none exists
        Updates django template with XY plots, mesh, etc data.

        Args:
            project_name: name of the project to render

        Returns:
            None
        """
        report = [
            {
                "name": "my_xy_plot",
                "id": "a12",
                "x_label": '"Time [ns]"',
                "x_axis": [2010, 2011, 2012, 2013, 2014, 2015, 2016],
                "version_1": "194",
                "y_axis_1": [0, 30, 10, 120, 50, 63, 10],
                "version_2": "221",
                "y_axis_2": [0, 50, 40, 80, 40, 79, 120],
            },
            {
                "name": "xy_plot",
                "id": "a13",
                "x_label": '"Time [ns]"',
                "x_axis": [2010, 2011, 2012, 2013, 2014, 2015, 2016],
                "version_1": "194",
                "y_axis_1": [0, 30, 10, 120, 50, 63, 10],
                "version_2": "221",
                "y_axis_2": [0, 50, 40, 80, 40, 79, 120],
            },
            {
                "name": "Torque",
                "id": "a15",
                "x_label": '"Time [ns]"',
                "x_axis": [2010, 2011, 2012, 2013, 2014, 2015, 2016],
                "version_1": "194",
                "y_axis_1": [0, 30, 10, 120, 50, 63, 10],
                "version_2": "221",
                "y_axis_2": [0, 50, 40, 80, 40, 79, 120],
            },
        ]

        data = PROJECT_PAGE_TEMPLATE.render(context={"plots": report, "project_name": project_name})
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
        self.render_main_html(status="running", project_name=project_name)

        execute_aedt(
            self.version,
            self.script,
            self.script_args,
            project_path,
            allocated_machines,
            distribution_config=project_config["distribution"],
        )

        # return cores back
        for machine in allocated_machines:
            self.machines_dict[machine] += allocated_machines[machine]["cores"]

        project_report = self.prepare_project_report(project_name, project_path)

        if not self.only_reference:
            self.render_project_html(project_name, project_report)

        self.render_main_html(status="success", project_name=project_name)
        self.active_tasks -= 1

    def prepare_project_report(self, project_name, project_path):
        report_file = Path(project_path).parent / f"{project_name}.json"
        if not report_file.exists():
            project_report = {"error_exception": [f"Project report for {project_name} does not exist"]}
        else:
            copy_path(str(report_file), str(self.results_path / "reference_folder"))
            with open(report_file) as file:
                project_report = json.load(file)

        return project_report

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
                logger.info("Number of maximum tasks limit is reached. Wait for job to finish")
                sleep(4)

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
                        msg += f"{machine} has {cores} cores free\n"

                    logger.info(msg)
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
    return copy_path(src, dst)


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
            copy_path(dep, dst)
    elif isinstance(deps, str):
        copy_path(deps, dst)


def copy_path(src: str, dst: str) -> Union[str, List[str]]:
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
        unpack_dst = str(Path(dst) / src.parents[0])
    else:
        unpack_dst = str(Path(dst))

    mkpath(unpack_dst)
    src = src.expanduser().resolve()

    if not src.exists():
        raise FileExistsError(f"File {src} doesn't exist")

    if src.is_file():
        file_path = copy_file(str(src), unpack_dst)
        return file_path[0]
    else:
        return copy_tree(str(src.parent), unpack_dst)


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


def parse_arguments() -> argparse.Namespace:
    """
    Parse CLI arguments

    Returns:
        (argparse.Namespace) validated arguments
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--aedt-version", "-av", required=True, help="Electronics Desktop version to test, e.g. 221")
    parser.add_argument("--config-file", "-cf", required=True, help="Project config file path")
    parser.add_argument("--reference-file", help="Reference results file path")
    parser.add_argument("--only-reference", action="store_true", help="Only create reference results")

    parser.add_argument(
        "--out-dir", "-o", help="Output directory for reports and project files (if --save-sim-data set)"
    )
    parser.add_argument(
        "--save-sim-data", "-s", action="store_true", help="Save simulation data under output dir (--out-dir flag)"
    )
    parser.add_argument(
        "--max-cores",
        "-c",
        type=int,
        help="total number of cores limit",
    )
    parser.add_argument(
        "--max-tasks",
        "-t",
        type=int,
        help="total number of parallel tasks limit",
    )
    cli_args = parser.parse_args()

    if not cli_args.only_reference and not cli_args.reference_file:
        raise ValueError("Either set --only-reference flag or provide path via --reference-file")

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
    set_logger(logging_file=CWD_DIR / "aedt_test_framework.log")
    args_cli = parse_arguments()
    aedt_tester = ElectronicsDesktopTester(
        version=args_cli.aedt_version,
        max_cores=args_cli.max_cores,
        max_tasks=args_cli.max_tasks,
        config_file=args_cli.config_file,
        out_dir=args_cli.out_dir,
        save_projects=args_cli.save_sim_data,
        only_reference=args_cli.only_reference,
    )
    aedt_tester.run()
