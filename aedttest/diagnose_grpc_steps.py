"""Step-by-step gRPC diagnostics for AEDT on Windows.

Runs exactly ONE step per process invocation, so that each gRPC interaction
is isolated from Python-side state left over by previous steps and gets its
own external timeout (via the caller, e.g. a wrapper script or manual runs).

Because the AEDT gRPC server is a persistent, independent OS process, project
and design state (open project, active design, ...) survives across separate
client (Python) processes attaching/detaching, as long as each client uses
``close_on_exit=False`` and never calls ``release_desktop(close_projects=True)``
before the final step.

Steps
-----
T0  - ansysedt starts and stays alive
T1  - TCP port is open (gRPC server accepting connections)
T2  - Desktop attach works
T3  - GetVersion / GetProcessID work
T4  - OpenProject works
T5  - SetActiveProject works
T6  - GetTopDesignList works
T7  - get_pyaedt_app works
T8  - setup_names works
T9  - analyze_setup works
T10 - export functions work (profile, mesh stats, report)
T11 - release_desktop works

Usage
-----
    python diagnose_grpc_steps.py --step 0 --aedt-version 2025.1 ^
        --project-path C:\\path\\to\\project.aedt --status-file status.json

    python diagnose_grpc_steps.py --step 1 --status-file status.json
    python diagnose_grpc_steps.py --step 2 --status-file status.json
    ...
    python diagnose_grpc_steps.py --step 11 --status-file status.json

Each invocation reads/updates ``--status-file`` (JSON) with the port, pid and
the outcome (ok/fail + duration + error) of every step executed so far, so you
end up with a full T0..T11 table even though each step ran in its own process.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
from time import monotonic
from time import sleep

MODULE_DIR_PARENT = os.path.dirname(os.path.dirname(__file__))
sys.path.append(MODULE_DIR_PARENT)

STEP_NAMES = {
    0: "T0 ansysedt starts and stays alive",
    1: "T1 TCP port is open",
    2: "T2 Desktop attach",
    3: "T3 GetVersion/GetProcessID",
    4: "T4 OpenProject",
    5: "T5 SetActiveProject",
    6: "T6 GetTopDesignList",
    7: "T7 get_pyaedt_app",
    8: "T8 setup_names",
    9: "T9 analyze_setup",
    10: "T10 export functions",
    11: "T11 release_desktop",
}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--step", type=int, required=True, choices=sorted(STEP_NAMES), help="Which step to run")
    parser.add_argument("--status-file", required=True, help="JSON file used to persist state/results across steps")
    parser.add_argument("--aedt-version", default=None, help="e.g. '2025.1' (required for step 0)")
    parser.add_argument("--project-path", default=None, help="Full path to the .aedt project (required from step 4)")
    parser.add_argument("--design-name", default=None, help="Design name (required from step 7)")
    parser.add_argument("--machine", default="localhost")
    parser.add_argument("--host-timeout", type=int, default=120, help="Timeout for this single step [s]")
    parser.add_argument("--cores", type=int, default=1)
    parser.add_argument("--tasks", type=int, default=1)
    return parser.parse_args()


def load_status(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"port": None, "pid": None, "results": {}}


def save_status(path, status):
    with open(path, "w") as f:
        json.dump(status, f, indent=2)


def record(status, status_file, step, ok, duration, error=None, extra=None):
    entry = {"name": STEP_NAMES[step], "status": "ok" if ok else "fail", "duration_s": round(duration, 2)}
    if error:
        entry["error"] = str(error)
    if extra is not None:
        entry["result"] = extra
    status["results"][str(step)] = entry
    save_status(status_file, status)
    print(
        "[{}] {} -> {}{}".format(STEP_NAMES[step], "OK" if ok else "FAIL", entry["status"], "" if ok else f" ({error})")
    )


def get_aedt_executable_path(version):
    aedt_env = "ANSYSEM_ROOT{}".format(version)
    aedt_path = os.environ.get(aedt_env)
    if not aedt_path:
        raise ValueError("Environment variable {} is not set.".format(aedt_env))
    executable = "ansysedt.exe" if sys.platform == "win32" else "ansysedt"
    return os.path.join(aedt_path, executable)


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def step_0_start_aedt(args, status, status_file):
    """Start ansysedt.exe as a bare gRPC server, keep it detached (independent process)."""
    if not args.aedt_version:
        raise ValueError("--aedt-version is required for step 0")

    # aedt_version_arg is expected in short form e.g. "251" to resolve ANSYSEM_ROOT251
    version_key = args.aedt_version.replace(".", "")
    if len(version_key) == 3:
        pass  # already short form, e.g. "251"
    exe = get_aedt_executable_path(version_key)
    port = find_free_port()
    command = [exe, "-ng", "-grpcsrv", str(port)]
    print("Starting: {}".format(subprocess.list2cmdline(command)))

    # Detach so the AEDT process survives this script exiting:
    # - Windows: DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP
    # - Linux/macOS: start_new_session=True (setsid), the POSIX equivalent
    popen_kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    process = subprocess.Popen(command, **popen_kwargs)

    sleep(3)
    if process.poll() is not None:
        raise RuntimeError("{} exited immediately with code {}".format(os.path.basename(exe), process.returncode))

    status["port"] = port
    status["pid"] = process.pid
    status["aedt_version"] = args.aedt_version
    if args.project_path:
        status["project_path"] = os.path.abspath(args.project_path)
    return {"pid": process.pid, "port": port}


def step_1_tcp_port_open(args, status, status_file):
    port = status.get("port")
    if not port:
        raise RuntimeError("No port recorded in status file, run --step 0 first")

    connect_host = "127.0.0.1" if args.machine in ("localhost", "") else args.machine
    deadline = monotonic() + args.host_timeout
    while monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(2)
            if probe.connect_ex((connect_host, port)) == 0:
                return {"port": port}
        sleep(2)
    raise TimeoutError("Port {} on {} did not open within {}s".format(port, connect_host, args.host_timeout))


def _connect_desktop(args, status):
    from ansys.aedt.core import Desktop

    port = status.get("port")
    if not port:
        raise RuntimeError("No port recorded in status file, run --step 0 first")

    kwargs = {
        "version": status.get("aedt_version") or args.aedt_version,
        "port": port,
        "non_graphical": True,
        "new_desktop": False,
        "close_on_exit": False,
    }
    if args.machine not in ("", "localhost", "127.0.0.1"):
        kwargs["machine"] = args.machine
    return Desktop(**kwargs)


def step_2_desktop_attach(args, status, status_file):
    desktop = _connect_desktop(args, status)
    try:
        return {"attached": True}
    finally:
        desktop.release_desktop(close_projects=False, close_on_exit=False)


def step_3_get_version_pid(args, status, status_file):
    desktop = _connect_desktop(args, status)
    try:
        version = desktop.odesktop.GetVersion()
        pid = desktop.odesktop.GetProcessID()
        return {"version": version, "pid": pid}
    finally:
        desktop.release_desktop(close_projects=False, close_on_exit=False)


def step_4_open_project(args, status, status_file):
    project_path = status.get("project_path") or args.project_path
    if not project_path:
        raise ValueError("--project-path is required from step 4 onward (or pass it already at --step 0)")
    project_path = os.path.abspath(project_path)
    status["project_path"] = project_path
    desktop = _connect_desktop(args, status)
    try:
        project_name = os.path.splitext(os.path.basename(project_path))[0]
        if project_name not in list(desktop.project_list):
            desktop.load_project(project_path)
        return {"project_list": list(desktop.project_list)}
    finally:
        desktop.release_desktop(close_projects=False, close_on_exit=False)


def step_5_set_active_project(args, status, status_file):
    project_path = status.get("project_path") or args.project_path
    project_name = os.path.splitext(os.path.basename(project_path))[0]
    desktop = _connect_desktop(args, status)
    try:
        desktop.odesktop.SetActiveProject(project_name)
        oproject = desktop.odesktop.GetActiveProject()
        return {"active_project": oproject.GetName()}
    finally:
        desktop.release_desktop(close_projects=False, close_on_exit=False)


def step_6_get_top_design_list(args, status, status_file):
    project_path = status.get("project_path") or args.project_path
    project_name = os.path.splitext(os.path.basename(project_path))[0]
    desktop = _connect_desktop(args, status)
    try:
        desktop.odesktop.SetActiveProject(project_name)
        oproject = desktop.odesktop.GetActiveProject()
        design_names = list(oproject.GetTopDesignList())
        status["design_names"] = design_names
        return {"design_names": design_names}
    finally:
        desktop.release_desktop(close_projects=False, close_on_exit=False)


def _pick_design_name(args, status):
    if args.design_name:
        return args.design_name
    design_names = status.get("design_names")
    if design_names:
        return design_names[0]
    raise ValueError("--design-name not given and no design_names recorded, run --step 6 first")


def step_7_get_pyaedt_app(args, status, status_file):
    from ansys.aedt.core import get_pyaedt_app

    project_path = status.get("project_path") or args.project_path
    project_name = os.path.splitext(os.path.basename(project_path))[0]
    design_name = _pick_design_name(args, status)
    status["design_name"] = design_name

    desktop = _connect_desktop(args, status)
    try:
        desktop.odesktop.SetActiveProject(project_name)
        oproject = desktop.odesktop.GetActiveProject()
        oproject.SetActiveDesign(design_name)
        app = get_pyaedt_app(project_name=project_name, design_name=design_name, desktop=desktop)
        return {"design_type": app.design_type}
    finally:
        desktop.release_desktop(close_projects=False, close_on_exit=False)


def step_8_setup_names(args, status, status_file):
    from ansys.aedt.core import get_pyaedt_app

    project_path = status.get("project_path") or args.project_path
    project_name = os.path.splitext(os.path.basename(project_path))[0]
    design_name = status.get("design_name") or _pick_design_name(args, status)

    desktop = _connect_desktop(args, status)
    try:
        desktop.odesktop.SetActiveProject(project_name)
        oproject = desktop.odesktop.GetActiveProject()
        oproject.SetActiveDesign(design_name)
        app = get_pyaedt_app(project_name=project_name, design_name=design_name, desktop=desktop)
        setups = app.setup_names
        status["setup_names"] = setups
        return {"setup_names": setups}
    finally:
        desktop.release_desktop(close_projects=False, close_on_exit=False)


def step_9_analyze_setup(args, status, status_file):
    from ansys.aedt.core import get_pyaedt_app

    project_path = status.get("project_path") or args.project_path
    project_name = os.path.splitext(os.path.basename(project_path))[0]
    design_name = status.get("design_name") or _pick_design_name(args, status)

    desktop = _connect_desktop(args, status)
    try:
        desktop.odesktop.SetActiveProject(project_name)
        oproject = desktop.odesktop.GetActiveProject()
        oproject.SetActiveDesign(design_name)
        app = get_pyaedt_app(project_name=project_name, design_name=design_name, desktop=desktop)
        success = app.analyze_setup(
            name=None,
            cores=args.cores,
            tasks=args.tasks,
            use_auto_settings=True,
            blocking=True,
        )
        return {"analyze_success": bool(success)}
    finally:
        desktop.release_desktop(close_projects=False, close_on_exit=False)


def step_10_export_functions(args, status, status_file):
    from ansys.aedt.core import get_pyaedt_app
    from ansys.aedt.core.generic.file_utils import generate_unique_name

    project_path = status.get("project_path") or args.project_path
    project_dir = os.path.dirname(project_path)
    project_name = os.path.splitext(os.path.basename(project_path))[0]
    design_name = status.get("design_name") or _pick_design_name(args, status)

    desktop = _connect_desktop(args, status)
    try:
        desktop.odesktop.SetActiveProject(project_name)
        oproject = desktop.odesktop.GetActiveProject()
        oproject.SetActiveDesign(design_name)
        app = get_pyaedt_app(project_name=project_name, design_name=design_name, desktop=desktop)
        setups = app.setup_names
        if not setups:
            raise RuntimeError("No setups found on design {}".format(design_name))
        setup = setups[0]

        profile_file = os.path.join(project_dir, generate_unique_name("") + ".prof")
        profile_file = app.export_profile(setup, "", profile_file)

        report_names = app.post.all_report_names
        exported_report = None
        if report_names:
            exported_report = app.post.export_report_to_file(
                output_dir=project_dir, plot_name=report_names[0], extension=".rdat", unique_file=True
            )

        return {"profile_file": profile_file, "report_names": report_names, "exported_report": exported_report}
    finally:
        desktop.release_desktop(close_projects=False, close_on_exit=False)


def step_11_release_desktop(args, status, status_file):
    desktop = _connect_desktop(args, status)
    desktop.release_desktop(close_projects=True, close_on_exit=True)
    return {"released": True}


STEP_FUNCS = {
    0: step_0_start_aedt,
    1: step_1_tcp_port_open,
    2: step_2_desktop_attach,
    3: step_3_get_version_pid,
    4: step_4_open_project,
    5: step_5_set_active_project,
    6: step_6_get_top_design_list,
    7: step_7_get_pyaedt_app,
    8: step_8_setup_names,
    9: step_9_analyze_setup,
    10: step_10_export_functions,
    11: step_11_release_desktop,
}


def main():
    args = parse_args()
    status = load_status(args.status_file)

    start = monotonic()
    try:
        result = STEP_FUNCS[args.step](args, status, args.status_file)
        record(status, args.status_file, args.step, True, monotonic() - start, extra=result)
    except Exception as exc:
        record(status, args.status_file, args.step, False, monotonic() - start, error=exc)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
