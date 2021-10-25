import argparse
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

__authors__ = "Maksim Beliaev, Bo Yang"

from time import sleep

ROOT_DIR = Path(__file__).resolve().parent.parent
MODULE_DIR = Path(__file__).resolve().parent


thread_tuple = namedtuple("thread", ["thread", "cores", "ram"])


def run(version: str, max_cores: int, max_ram: int, max_tasks: int, config_file: str) -> None:
    """
    Main function to start test sweet
    Returns: None
    """

    with open(config_file) as file:
        tests_config = json.load(file)

    script = str(MODULE_DIR / "dummy.py")
    script_args = ""
    with TemporaryDirectory() as temp_dir:
        active_threads = []
        active_cores = 0
        active_ram = 0

        for project_name, project_config in tests_config.items():
            print(f"Add project {project_name}")
            project_path = resolve_project_path(project_name, project_config)

            shutil.copy2(project_path, temp_dir)
            tmp_proj = os.path.join(temp_dir, project_path.name)

            thread_args = (version, script, script_args, tmp_proj)
            thread = threading.Thread(target=execute_aedt, daemon=True, args=thread_args)
            thread.start()
            active_threads.append(thread_tuple(thread, int(project_config["cores"]), int(project_config["RAM"])))

            active_cores += int(project_config["cores"])
            active_ram += int(project_config["RAM"])
            while (
                (max_cores and active_cores > max_cores)
                or (max_ram and active_ram > max_ram)
                or (max_tasks and len(active_threads) > max_tasks)
            ):

                print(
                    "Wait for resources. "
                    f"Active cores: {active_cores}, RAM: {active_ram}GB, tasks: {len(active_threads)}"
                )
                sleep(1)
                for i, th in enumerate(active_threads):
                    if not th.thread.is_alive():
                        active_cores -= th.cores
                        active_ram -= th.ram
                        active_threads.pop(i)
                        break  # break since pop thread item

        [th.thread.join() for th in active_threads]


def resolve_project_path(project_name, project_config):
    if "path" in project_config:
        project_path = Path(project_config["path"])
        if not project_path.is_absolute():
            project_path = ROOT_DIR / project_path
    else:
        project_path = ROOT_DIR / project_name + ".aedt"

    if not project_path.exists():
        raise FileExistsError(f"Project {project_path} doesn't exist")

    return project_path.resolve()


def execute_aedt(version: str, script: str, script_args: str, project_path: str) -> None:
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
        "-ng",
        "-features=SF6694_NON_GRAPHICAL_COMMAND_EXECUTION",
        "-RunScriptAndExit",
        script,
        "-ScriptArgs",
        f'"{script_args}"',
        project_path,
    ]
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
