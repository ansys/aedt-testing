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
from django.conf import settings
from django.template.loader import get_template

__authors__ = "Maksim Beliaev, Bo Yang"


ROOT_DIR = Path(__file__).resolve().parent.parent
MODULE_DIR = Path(__file__).resolve().parent
CWD_DIR = Path.cwd()

# configure Django templates
settings.configure(
    TEMPLATES=[
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [MODULE_DIR],  # if you want the templates from a file
        },
    ]
)
django_setup()
HTML_TEMPLATE = get_template("static/main.html")

thread_tuple = namedtuple("thread", ["thread", "project_name", "cores", "ram"])


def run(
    version: str, max_cores: int, max_ram: int, max_tasks: int, config_file: str, out_dir: str, save_projects: bool
) -> None:
    """
    Main function to start test sweet
    Returns: None
    """

    job_machines = get_job_machines()
    print(job_machines)

    with open(config_file) as file:
        tests_config = json.load(file)

    script = str(MODULE_DIR / "dummy.py")
    script_args = None

    out_dir = Path(out_dir) if out_dir else CWD_DIR
    proj_dir = Path(out_dir) if save_projects else None

    report_data = initialize_results(tests_config, out_dir=out_dir)

    with mkdtemp_persistent(persistent=save_projects, dir=proj_dir) as temp_dir:
        active_threads = []

        for project_name, project_config in tests_config.items():
            distribution_config = project_config["distribution"]
            job_cores = int(distribution_config["cores"])
            job_ram = int(distribution_config["RAM"])

            # todo add allocation for tasks and cores per available machine
            # todo add check that cores per project does not exceed total cores on machines
            # todo flag single_node should place job on single node
            lock_execution(
                project_name, active_threads, job_cores, job_ram, max_cores, max_ram, max_tasks, report_data, out_dir
            )

            print(f"Add project {project_name}")
            project_path = resolve_project_path(project_name, project_config)

            shutil.copy2(project_path, temp_dir)
            tmp_proj = os.path.join(temp_dir, project_path.name)

            thread_args = (version, script, script_args, tmp_proj, job_machines, distribution_config)
            thread = threading.Thread(target=execute_aedt, daemon=True, args=thread_args)
            thread.start()

            render_html(report_data, project_name, "running", results_path=out_dir)

            active_threads.append(thread_tuple(thread, project_name, job_cores, job_ram))

        while active_threads:
            sleep(2)
            for i, th in enumerate(active_threads):
                if not th.thread.is_alive():
                    render_html(report_data, th.project_name, "success", results_path=out_dir)
                    active_threads.pop(i)
                    break


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
                "ram": project_config["distribution"]["RAM"],
                "status": "queued",
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    render_html(report_data, results_path=results_path)
    return report_data


def render_html(report_data, project_name=None, status=None, results_path=None):
    if project_name:
        for proj in report_data:
            if proj["name"] == project_name:
                proj["status"] = status
                proj["time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break

    data = HTML_TEMPLATE.render(context={"projects": report_data})
    with open(results_path / "main.html", "w") as file:
        file.write(data)


def lock_execution(
    project_name, active_threads, job_cores, job_ram, max_cores, max_ram, max_tasks, report_data, results_path
):
    active_cores = sum((th.cores for th in active_threads))
    active_ram = sum((th.ram for th in active_threads))
    next_active_cores = active_cores + job_cores
    next_active_ram = active_ram + job_ram
    while (
        (max_cores and next_active_cores > max_cores)
        or (max_ram and next_active_ram > max_ram)
        or (max_tasks and len(active_threads) >= max_tasks)
    ):

        print(
            f"{project_name} is waiting for resources.\n"
            f"Active cores: {active_cores}, RAM: {active_ram} GB, tasks: {len(active_threads)}\n"
            f"Limits are cores: {max_cores}, RAM: {max_ram} GB, tasks: {max_tasks}\n"
            f"Next job requires cores: {job_cores}, RAM: {job_ram} GB, tasks: 1\n"
        )

        for i, th in enumerate(active_threads):
            if not th.thread.is_alive():
                next_active_cores -= th.cores
                next_active_ram -= th.ram
                render_html(report_data, th.project_name, "success", results_path=results_path)

                active_threads.pop(i)
                break  # break since pop thread item
        else:
            # sleep only if no thread was finished
            sleep(5)


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
        host_list = "list=" + ",".join([f"{machine}:{machine['tasks']}:{machine['cores']}:90%" for machine in machines])
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
        "--max-ram",
        "-r",
        type=int,
        help="total RAM limit [GB]",
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

    if not (cli_args.max_ram or cli_args.max_cores or cli_args.max_tasks):
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
        max_ram=args_cli.max_ram,
        max_tasks=args_cli.max_tasks,
        config_file=args_cli.config_file,
        out_dir=args_cli.out_dir,
        save_projects=args_cli.save_sim_data,
    )
