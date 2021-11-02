import argparse
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from time import sleep
from typing import Dict
from typing import Iterable
from typing import Optional

from clusters.job_hosts import get_job_machines
from django import setup as django_setup
from django.conf import settings as django_settings
from django.template.loader import get_template

__authors__ = "Maksim Beliaev, Bo Yang"


ROOT_DIR = Path(__file__).resolve().parent.parent
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
HTML_TEMPLATE = get_template("static/main.html")


class ElectronicsDesktopTester:
    def __init__(
        self, version: str, max_cores: int, max_tasks: int, config_file: str, out_dir: str, save_projects: bool
    ) -> None:
        self.version = version
        self.max_cores = max_cores
        self.max_tasks = max_tasks
        self.out_dir = Path(out_dir) if out_dir else CWD_DIR
        self.results_path = self.out_dir / "results"
        self.proj_dir = self.out_dir if save_projects else None

        self.script = str(MODULE_DIR / "dummy.py")  # todo replace with script from Bo
        self.script_args = None

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

        with mkdtemp_persistent(persistent=(self.proj_dir is not None), dir=self.proj_dir) as tmp_dir:
            for project_name, allocated_machines in self.allocator():
                project_config = self.project_tests_config[project_name]

                print(f"Add project {project_name}")
                project_path = resolve_project_path(project_name, project_config)

                shutil.copy2(project_path, tmp_dir)
                tmp_proj = os.path.join(tmp_dir, project_path.name)

                thread_kwargs = {
                    "project_path": tmp_proj,
                    "allocated_machines": allocated_machines,
                    "project_config": project_config,
                    "project_name": project_name,
                }
                thread = threading.Thread(target=self.task_runner, daemon=True, kwargs=thread_kwargs)
                thread.start()

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
        if self.results_path.exists():
            shutil.rmtree(self.results_path)
        shutil.copytree(MODULE_DIR / "static", self.results_path)

        for project_name, project_config in self.project_tests_config.items():
            self.report_data.append(
                {
                    "name": project_name,
                    "cores": project_config["distribution"]["cores"],
                    "status": "queued",
                    "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
        self.render_html(status="queued")

    def render_html(self, status: str, project_name: Optional[str] = None) -> None:
        if project_name:
            for proj in self.report_data:
                if proj["name"] == project_name:
                    proj["status"] = status
                    proj["time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    break

        data = HTML_TEMPLATE.render(context={"projects": self.report_data})
        with open(self.results_path / "main.html", "w") as file:
            file.write(data)

    def task_runner(self, project_name: str, project_path: str, project_config: dict, allocated_machines: dict) -> None:
        self.render_html(status="running", project_name=project_name)

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

        self.render_html(status="success", project_name=project_name)

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
                    print("Waiting for resources. Cores left per machine:")
                    for machine, cores in self.machines_dict.items():
                        print(f"{machine} has {cores} cores free")

                    sleep(5)

            if allocated_machines:
                for machine in allocated_machines:
                    self.machines_dict[machine] -= allocated_machines[machine]["cores"]

                sorted_by_cores_desc.remove(proj_name)
                yield proj_name, allocated_machines


def allocate_task(
    distribution_config: Dict[str, int], machines_dict: Dict[str, int]
) -> Optional[Dict[str, Dict[str, int]]]:
    """
    Allocate task on one or more nodes. Will use MPI and split the job
    If multiple parametric tasks are defined, distribute uniform
    Args:
        distribution_config:
        machines_dict:

    Returns:
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
        print("Not enough resources to split job")
        return

    return allocated_machines


def allocate_task_within_node(
    distribution_config: Dict[str, int], machines_dict: Dict[str, int]
) -> Dict[str, Dict[str, str]]:
    """
    Try to fit a task in a node without splitting
    Args:
        distribution_config:
        machines_dict:

    Returns:
    """

    for machine, cores in machines_dict.items():
        if cores - distribution_config["cores"] >= 0:
            return {
                machine: {
                    "cores": distribution_config["cores"],
                    "tasks": distribution_config.get("parametric_tasks", 1),
                }
            }


def resolve_project_path(project_name: str, project_config: Dict[str, str]) -> Path:
    if "path" in project_config:
        project_path = project_config["path"].replace("\\", "/")
        project_path = Path(project_path)
        if not project_path.is_absolute():
            project_path = CWD_DIR / project_path
    else:
        project_path = ROOT_DIR / (project_name + ".aedt")

    if not project_path.exists():
        raise FileExistsError(f"Project {project_path} doesn't exist")

    return project_path.resolve()


def mkdtemp_persistent(*args, persistent=True, **kwargs):
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

    print(f"Execute {subprocess.list2cmdline(command)}")
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


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--aedt-version", "-av", required=True, help="Electronics Desktop version to test, e.g. 221")
    parser.add_argument("--config-file", "-cf", required=True, help="Project config file path")
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

    if not (cli_args.max_cores or cli_args.max_tasks):
        print("No limits are specified for current job. This may lead to failure if you lack of license or resources")

    aedt_version_pattern = re.compile(r"\d\d\d$")
    if not aedt_version_pattern.match(cli_args.aedt_version):
        raise ValueError("Electronics Desktop version value is invalid. Valid format example: 221")

    if not os.path.isfile(cli_args.config_file):
        raise ValueError(f"Configuration file does not exist: {cli_args.config_file}")

    if cli_args.save_sim_data and not cli_args.out_dir:
        raise ValueError("Saving of simulation data was requested but output directory is not provided")

    return cli_args


if __name__ == "__main__":
    args_cli = parse_arguments()
    aedt_tester = ElectronicsDesktopTester(
        version=args_cli.aedt_version,
        max_cores=args_cli.max_cores,
        max_tasks=args_cli.max_tasks,
        config_file=args_cli.config_file,
        out_dir=args_cli.out_dir,
        save_projects=args_cli.save_sim_data,
    )
    aedt_tester.run()
