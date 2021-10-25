import argparse
import os
import platform
import re
import shutil
import subprocess
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

__authors__ = "Maksim Beliaev, Bo Yang"

ROOT_DIR = Path(__file__).resolve().parent.parent
MODULE_DIR = Path(__file__).resolve().parent


def run(version: str) -> None:
    """
    Main function to start test sweet
    Returns: None
    """

    script = str(MODULE_DIR / "dummy.py")
    script_args = ""
    project_paths = [ROOT_DIR / "input" / "just_winding.aedt", ROOT_DIR / "input" / "just_winding2.aedt"]
    with TemporaryDirectory() as temp_dir:
        threads = []
        for project_path in project_paths:
            shutil.copy2(project_path, temp_dir)

            tmp_proj = os.path.join(temp_dir, project_path.name)
            thread_args = (version, script, script_args, tmp_proj)
            threads.append(threading.Thread(target=execute_aedt, daemon=True, args=thread_args))

        for thread in threads:
            thread.start()
            thread.join()


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
    args = parser.parse_args()

    aedt_version_pattern = re.compile(r"\d\d\d$")
    if not aedt_version_pattern.match(args.aedt_version):
        raise ValueError("Electronics Desktop version value is invalid. Valid format example: 221")

    run(version=args.aedt_version)
