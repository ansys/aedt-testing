import argparse
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import threading
from collections import namedtuple
from pathlib import Path
from tempfile import TemporaryDirectory

from django import setup as django_setup
from django.conf import settings
from django.template.loader import get_template

__authors__ = "Maksim Beliaev, Bo Yang"

from time import sleep

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


def run(version: str, max_cores: int, max_ram: int, max_tasks: int, config_file: str) -> None:
    """
    Main function to start test sweet
    Returns: None
    """

    execute_aedt(version, script=str(MODULE_DIR / "get_cluster_hosts.py"))
    with open("host_info.json") as file:
        all_machines = json.load(file)

    with open(config_file) as file:
        tests_config = json.load(file)

    script = str(MODULE_DIR / "dummy.py")
    script_args = None

    report_data = initialize_results(tests_config)

    with TemporaryDirectory() as temp_dir:
        active_threads = []

        for project_name, project_config in tests_config.items():
            job_cores = int(project_config["cores"])
            job_ram = int(project_config["RAM"])

            lock_execution(project_name, active_threads, job_cores, job_ram, max_cores, max_ram, max_tasks, report_data)

            print(f"Add project {project_name}")
            project_path = resolve_project_path(project_name, project_config)

            shutil.copy2(project_path, temp_dir)
            tmp_proj = os.path.join(temp_dir, project_path.name)

            thread_args = (version, script, script_args, tmp_proj, all_machines)
            thread = threading.Thread(target=execute_aedt, daemon=True, args=thread_args)
            thread.start()

            render_html(report_data, project_name, "running")

            active_threads.append(thread_tuple(thread, project_name, job_cores, job_ram))

        while active_threads:
            sleep(2)
            for i, th in enumerate(active_threads):
                if not th.thread.is_alive():
                    render_html(report_data, th.project_name, "success")
                    active_threads.pop(i)
                    break


def initialize_results(tests_config):
    report_data = []
    if (CWD_DIR / "results").exists():
        shutil.rmtree(CWD_DIR / "results")
    shutil.copytree(MODULE_DIR / "static", CWD_DIR / "results")
    for project_name, project_config in tests_config.items():
        report_data.append(
            {
                "name": project_name,
                "cores": project_config["cores"],
                "ram": project_config["RAM"],
                "status": "queued",
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    render_html(report_data)
    return report_data


def render_html(report_data, project_name=None, status=None):
    if project_name:
        for proj in report_data:
            if proj["name"] == project_name:
                proj["status"] = status
                proj["time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                break

    data = HTML_TEMPLATE.render(context={"projects": report_data})
    with open(Path.cwd() / "results" / "main.html", "w") as file:
        file.write(data)


def lock_execution(project_name, active_threads, job_cores, job_ram, max_cores, max_ram, max_tasks, report_data):
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
        sleep(5)
        for i, th in enumerate(active_threads):
            if not th.thread.is_alive():
                next_active_cores -= th.cores
                next_active_ram -= th.ram
                render_html(report_data, th.project_name, "success")

                active_threads.pop(i)
                break  # break since pop thread item


def resolve_project_path(project_name, project_config):
    if "path" in project_config:
        project_path = Path(project_config["path"])
        if not project_path.is_absolute():
            project_path = CWD_DIR / project_path
    else:
        project_path = ROOT_DIR / project_name + ".aedt"

    if not project_path.exists():
        raise FileExistsError(f"Project {project_path} doesn't exist")

    return project_path.resolve()


def execute_aedt(
    version: str, script: str = None, script_args: str = None, project_path: str = None, machines: dict = None
) -> None:
    """
    Execute single instance of Electronics Desktop

    Args:
        version: version to run
        script: path to the script
        script_args: arguments to the script
        project_path: path to the project

    Returns: None
    """

    aedt_path = get_aedt_executable_path(version)

    command = [
        aedt_path,
    ]

    if machines is not None:
        command.append("-machinelist")
        host_list = "list=" + ",".join([f"{machine}:1:{cores}:90%" for machine, cores in machines.items()])
        command.append(host_list)

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    # Add long and short argument
    parser.add_argument("--aedt-version", "-av", help="Electronics Desktop version to test, e.g. 221", required=True)
    parser.add_argument("--config-file", "-cf", help="config file path", required=True)
    parser.add_argument("--max-ram", "-r", help="total RAM limit [GB]", type=int)
    parser.add_argument("--max-cores", "-c", help="total number of cores limit", type=int)
    parser.add_argument("--max-tasks", "-t", help="total number of parallel tasks limit", type=int)
    args = parser.parse_args()

    if not (args.max_ram or args.max_cores or args.max_tasks):
        print("No limits are specified for current job. This may lead to failure if you lack of license or resources")

    aedt_version_pattern = re.compile(r"\d\d\d$")
    if not aedt_version_pattern.match(args.aedt_version):
        raise ValueError("Electronics Desktop version value is invalid. Valid format example: 221")

    if not os.path.isfile(args.config_file):
        raise ValueError(f"Configuration file does not exist: {args.config_file}")

    run(
        version=args.aedt_version,
        max_cores=args.max_cores,
        max_ram=args.max_ram,
        max_tasks=args.max_tasks,
        config_file=args.config_file,
    )
