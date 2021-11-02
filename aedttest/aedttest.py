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
from collections import namedtuple
from contextlib import contextmanager
from pathlib import Path
from time import sleep

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

thread_tuple = namedtuple("thread", ["thread", "project_name", "cores"])
machines_dict = {}


def run(version: str, max_cores: int, max_tasks: int, config_file: str, out_dir: str, save_projects: bool) -> None:
    """
    Main function to start test sweet
    Returns: None
    """

    job_machines = get_job_machines()
    for machine in job_machines:
        machines_dict[machine.hostname] = machine.cores

    print(job_machines)

    with open(config_file) as file:
        tests_config = json.load(file)

    validate_hardware(tests_config)

    script = str(MODULE_DIR / "dummy.py")
    script_args = None

    out_dir = Path(out_dir) if out_dir else CWD_DIR
    proj_dir = Path(out_dir) if save_projects else None

    report_data = initialize_results(tests_config, out_dir=out_dir)

    with mkdtemp_persistent(persistent=save_projects, dir=proj_dir) as tmp_dir:
        for project_name, allocated_machines in allocator(tests_config):
            project_config = tests_config[project_name]

            # todo add allocation for tasks and cores per available machine

            print(f"Add project {project_name}")
            project_path = resolve_project_path(project_name, project_config)

            shutil.copy2(project_path, tmp_dir)
            tmp_proj = os.path.join(tmp_dir, project_path.name)

            thread_kwargs = {
                "version": version,
                "script": script,
                "script_args": script_args,
                "project_path": tmp_proj,
                "allocated_machines": allocated_machines,
                "project_config": project_config,
                "out_dir": out_dir,
                "project_name": project_name,
                "report_data": report_data,
            }
            thread = threading.Thread(target=task_runner, daemon=True, kwargs=thread_kwargs)
            thread.start()


def initialize_results(tests_config, out_dir):
    report_data = []
    results_path = out_dir / "results"
    if results_path.exists():
        shutil.rmtree(results_path)
    shutil.copytree(MODULE_DIR / "static", results_path)

    for project_name, project_config in tests_config.items():
        report_data.append(
            {
                "name": project_name,
                "cores": project_config["distribution"]["cores"],
                "status": "queued",
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    render_html(report_data, status="queued", results_path=results_path)
    return report_data


def render_html(report_data, status, project_name=None, results_path=None):
    if project_name:
        for proj in report_data:
            if proj["name"] == project_name:
                proj["status"] = status
                proj["time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break

    data = HTML_TEMPLATE.render(context={"projects": report_data})
    with open(results_path / "main.html", "w") as file:
        file.write(data)


def allocator(tests_config):
    sorted_by_cores_desc = sorted(
        tests_config.keys(), key=lambda x: tests_config[x]["distribution"]["cores"], reverse=True
    )
    while sorted_by_cores_desc:
        for proj in sorted_by_cores_desc:
            allocated_machines = allocate_task(tests_config[proj]["distribution"])
            if allocated_machines:
                for machine in allocated_machines:
                    machines_dict[machine] -= allocated_machines[machine]["cores"]

                sorted_by_cores_desc.remove(proj)
                yield proj, allocated_machines
        else:
            print("Waiting for allocated_machines. Cores left per machine:")
            for machine, cores in machines_dict.items():
                print(f"{machine} has {cores} cores free")

            sleep(5)


def allocate_task(distribution_config):
    if distribution_config.get("single_node", False):
        for machine, cores in machines_dict.items():
            if cores - distribution_config["cores"] >= 0:
                return {
                    machine: {
                        "cores": distribution_config["cores"],
                        "tasks": distribution_config.get("parametric_tasks", 1),
                    }
                }


def validate_hardware(tests_config):
    """
    Validate that we have enough hardware resources to run requested configuration
    Args:
        tests_config: (dict) project run specs

    Returns:

    """
    all_cores = [val for val in machines_dict.values()]
    total_available_cores = sum(all_cores)
    max_machine_cores = max(all_cores)
    for proj in tests_config:
        proj_cores = tests_config[proj]["distribution"]["cores"]
        if proj_cores > total_available_cores or (
            tests_config[proj]["distribution"].get("single_node", False) and proj_cores > max_machine_cores
        ):
            raise ValueError(f"{proj} requires {proj_cores} cores. Not enough resources to run")


def resolve_project_path(project_name, project_config):
    if "path" in project_config:
        project_path = project_config["path"].replace("\\", "/")
        project_path = Path(project_path)
        if not project_path.is_absolute():
            project_path = CWD_DIR / project_path
    else:
        project_path = ROOT_DIR / project_name + ".aedt"

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


def task_runner(
    version: str,
    script: str = None,
    script_args: str = None,
    project_path: str = None,
    allocated_machines: dict = None,
    project_config: dict = None,
    out_dir=None,
    project_name=None,
    report_data=None,
):
    results_path = out_dir / "results"
    render_html(report_data, status="running", project_name=project_name, results_path=results_path)

    execute_aedt(
        version,
        script,
        script_args,
        project_path,
        allocated_machines,
        distribution_config=project_config["distribution"],
    )

    # return cores back
    for machine in allocated_machines:
        machines_dict[machine] += allocated_machines[machine]["cores"]

    render_html(report_data, status="success", project_name=project_name, results_path=results_path)


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

    run(
        version=args_cli.aedt_version,
        max_cores=args_cli.max_cores,
        max_tasks=args_cli.max_tasks,
        config_file=args_cli.config_file,
        out_dir=args_cli.out_dir,
        save_projects=args_cli.save_sim_data,
    )
