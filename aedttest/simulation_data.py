import argparse
import decimal
import json
import logging
import os
import re
import sys

MODULE_DIR_PARENT = os.path.dirname(os.path.dirname(__file__))
sys.path.append(MODULE_DIR_PARENT)

from aedttest.logger import logger  # noqa: E402
from aedttest.logger import set_logger  # noqa: E402


def parse_args():
    """Parse command-line arguments when simulation_data.py is executed via CPython.

    The script is launched directly by aedt_test_runner.py as a CPython
    subprocess and connects to an already running AEDT gRPC session.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--logfile-path", default=None)
    parser.add_argument(
        "--aedt-version",
        default=None,
        help="AEDT version to connect to (e.g. '2025.1'), must match the running AEDT session",
    )
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--port", type=int, default=0, help="gRPC port of the running AEDT session (passed by aedt_test_runner.py)"
    )
    parser.add_argument(
        "--machine",
        default="localhost",
        help="Host on which the AEDT gRPC session is running",
    )
    parser.add_argument("--project-path", default=None, help="Full path to the .aedt project file being tested")
    parser.add_argument(
        "--design-names",
        default=None,
        help="Optional comma-separated design names; parsed from the .aedt file when omitted",
    )
    parser.add_argument("--cores", type=int, default=None, help="Total number of cores for the analysis")
    parser.add_argument("--tasks", type=int, default=None, help="Total number of simulation tasks (NumEngines)")
    parser.add_argument(
        "--num-variations", type=int, default=None, help="Number of variations to distribute (parametric_tasks)"
    )
    parser.add_argument(
        "--distribution-types",
        default="Variations",
        help="Comma-separated list of allowed distribution types",
    )
    parser.add_argument(
        "--use-auto-settings",
        action="store_true",
        help="Use AEDT auto HPC settings (maps to distribution_config['auto'] in the TOML config)",
    )
    args = parser.parse_args()
    logfile_path = args.logfile_path or os.path.join(MODULE_DIR_PARENT, "aedt_test_framework.log")
    return (
        logfile_path,
        args.debug,
        args.port,
        args.project_path,
        args.design_names,
        args.aedt_version,
        args.machine,
        args.cores,
        args.tasks,
        args.num_variations,
        [t.strip() for t in args.distribution_types.split(",") if t.strip()],
        args.use_auto_settings,
    )


log_level = logging.DEBUG
(
    logfile_path,
    debug,
    grpc_port,
    project_path_arg,
    design_names_arg,
    specified_version,
    grpc_machine,
    cores_arg,
    tasks_arg,
    num_variations_arg,
    distribution_types_arg,
    use_auto_settings_arg,
) = parse_args()
# specified_version must match the AEDT version of the running session,
# since we connect to it instead of starting a new one (new_desktop=False).
if not debug:
    log_level = logging.INFO

try:
    # NEW:
    import ansys.aedt.core as pyaedt
    from ansys.aedt.core import Desktop
    from ansys.aedt.core import get_pyaedt_app
    from ansys.aedt.core.generic.file_utils import generate_unique_name
    from ansys.aedt.core.visualization.advanced.misc import parse_rdat_file
except Exception as exc:
    set_logger(logging_file=logfile_path, level=log_level, pyaedt_module=None)
    logger.exception(str(exc))
    raise


set_logger(logging_file=logfile_path, level=log_level, pyaedt_module=pyaedt)

PROJECT_DICT = {"error_exception": [], "designs": {}}


class AedtTestException(Exception):
    """Base class for exceptions in this module."""


def parse_mesh_stats(mesh_stats_file, design_name, variation, setup_name):
    """Get the mesh element number.

    Parameters
    ----------
    mesh_stats_file : str
        Path of the mesh stats ``.mstat`` file.
    design_name : str
        Name of the design.
    variation : str
        Variation string.
    setup_name : str
        Name of the setup.

    Returns
    -------
    int
        Number of mesh elements.
    """

    with open(mesh_stats_file) as fid:
        lines = fid.readlines()

    for line in lines:
        if "Total number of mesh elements" in line:
            return int(line.strip().split(":")[1])
    else:
        PROJECT_DICT["error_exception"].append(
            "Design:{} Variation: {} Setup: {} has no mesh stats".format(design_name, variation, setup_name)
        )


def parse_profile_file(profile_file, design_name, variation, setup_name):
    """Get the simulation time.

    Parameters
    ----------
    profile_file : str
        Path of the profile file ``.prof``.
    design_name : str
        Name of the design.
    variation : str
        Variation string.
    setup_name : str
        Name of the setup.

    Returns
    -------
    simulation_time : str
        Elapsed simulation time.
    cell_number: int
        number of cells for Icepak
    """
    elapsed_time = ""
    cell_number = 0
    with open(profile_file) as file:
        for line in file:
            if "elapsed time" in line.lower():
                elapsed_time = line.lower()
            if "cells" in line.lower():
                val = re.match(r".*cells: (\d+)", line.lower()) or re.match(r".* (\d+) cells", line.lower())
                cell_number = int(val.group(1))

    if elapsed_time:
        split_line = elapsed_time.split("elapsed time")[1]

        simulation_time = re.findall(r"[0-9]*:[0-9][0-9]:[0-9][0-9]", split_line)[0]
        return simulation_time, cell_number
    else:
        PROJECT_DICT["error_exception"].append(
            ("Design:{} Variation:{} Setup:{} no elapsed time in file".format(design_name, variation, setup_name))
        )


def parse_value_with_unit(string):
    """Get the number and unit of a variation string.

    The number is truncated to 9 digits with scientific notation.

    Parameters
    ----------
    string : str
        Variation string which includes number and unit.

    Returns
    -------
    number : str
        String of number in format of 0.9e.
    unit : str
        Unit of the value.
    """
    units = ""
    precision = -9
    origin_string = string

    while string:
        try:
            number = float(string)
            d = decimal.Decimal(string)
            decimal_places = d.as_tuple().exponent
            if decimal_places < precision:
                return "{:0.9e}".format(number), units.strip()
            else:
                return string, units.strip()

        except ValueError:
            units = string[-1:] + units
            string = string[:-1]
    else:
        return origin_string, ""


def extract_data(desktop, project_dir, project_name, design_names):
    """Extract designs' data for a project.

    Parameters
    ----------
    desktop : pyaedt.desktop.Desktop
        ``pyaedt`` ``Desktop`` object.
    project_dir : str
        Path to the project.
    project_name : str
        Name of the project
    design_names : list
        List of design names.

    Returns
    -------
    designs_dict : dict
        Dictionary includes data of all listed designs.

    """

    designs_dict = {}

    oproject = desktop.odesktop.GetActiveProject()

    for design_name in design_names:
        design_dict = {
            design_name: {"mesh": {}, "simulation_time": {}, "report": {}, "profile_name": {}, "mesh_name": {}}
        }
        # Make sure the design we are about to query/analyze is the active one.
        # On a bare gRPC session (no -RunScriptAndExit) nothing is active by
        # default, and pyaedt calls below hang indefinitely on the gRPC channel
        # if there is no active design.
        try:
            oproject.SetActiveDesign(design_name)
        except Exception as exc:
            logger.warning("SetActiveDesign({}) failed: {}".format(design_name, exc))

        app = get_pyaedt_app(project_name=project_name, design_name=design_name, desktop=desktop)
        setups_names = app.setup_names
        if not setups_names:
            PROJECT_DICT["error_exception"].append("Design {} has no setups".format(design_name))
            designs_dict.update(design_dict)
            continue

        sweeps = app.existing_analysis_sweeps
        setup_dict = {}
        for setups in setups_names:
            for sweep in sweeps:
                if setups in sweep:
                    setup_dict[setups] = sweep
                    break

        logger.info("START ANALYZE: {}".format(design_name))
        try:
            # Use PyAEDT's own supported HPC configuration mechanism instead of
            # AEDT-side CLI batch-solve flags (-auto/-distributed/-machinelist).
            # analyze_setup() internally writes/applies an ACF registry file via
            # oDesktop.SetRegistryFromFile() - the same mechanism used by AEDT's
            # own HPC options dialog - which is the officially supported way to
            # configure distribution while driving a live scripting/gRPC session.
            analyze_success = app.analyze_setup(
                name=None,  # analyze all setups of this design
                cores=cores_arg,
                tasks=tasks_arg,
                use_auto_settings=use_auto_settings_arg,
                num_variations_to_distribute=num_variations_arg,
                allowed_distribution_types=distribution_types_arg,
                blocking=True,
            )
        except Exception as exc:
            # analyze_setup is wrapped by pyaedt_function_handler which may re-raise
            # instead of returning False. Treat any exception as a failed analysis
            # so we still capture messages and write partial results instead of
            # crashing the whole script.
            logger.error("design {} 'analyze_setup' raised exception: {}".format(design_name, exc))
            analyze_success = False
        logger.info("END ANALYZE: {}".format(design_name))

        if not analyze_success:
            logger.error("design {} 'analyze_setup' failed".format(design_name))
            try:
                error_messages = app.logger.get_messages(project_name, design_name, level=1, aedt_messages=True)
                messages = error_messages.design_level + error_messages.project_level + error_messages.global_level
            except Exception as exc:
                # get_messages() can itself crash (e.g. internal desktop handle is None
                # after a failed analyze_all). Don't let that abort the whole script.
                logger.error("get_messages() also failed: {}".format(exc))
                messages = []
            for message in messages:
                log_message = "{}: {}".format(design_name, message)
                logger.error(log_message)
                PROJECT_DICT["error_exception"].append(log_message)
        else:
            logger.info("design {} 'analyze_all' success".format(design_name))

        design_dict = extract_design_data(
            app=app,
            design_name=design_name,
            setup_dict=setup_dict,
            project_dir=project_dir,
            design_dict=design_dict,
        )

        try:
            report_names = app.post.all_report_names
        except Exception as exc:
            # pyaedt's app.post lazy-initializes a PostProcessor, which
            # internally accesses self.design_name -> self._design_name.
            # For some Icepak designs without variables this internal cache
            # ends up None, crashing on `";" in self._design_name`
            # (TypeError: argument of type 'NoneType' is not iterable).
            # Treat this the same as "no reports available" instead of
            # crashing the whole extraction for this design.
            msg = "Design:{} app.post.all_report_names failed: {}".format(design_name, exc)
            logger.warning(msg)
            PROJECT_DICT["error_exception"].append(msg)
            report_names = []

        reports_dict = extract_reports_data(
            app=app, design_name=design_name, project_dir=project_dir, report_names=report_names
        )

        design_dict[design_name]["report"] = reports_dict

        designs_dict.update(design_dict)

    return designs_dict


def _flatten_variation_to_string(variation_string):
    """Flatten a (possibly nested) list of variation tokens into a single string.

    pyaedt 1.3.0's available_variations.variations() may return each variation
    as a nested list of "var='val'" tokens instead of a single space-joined
    string (older API behavior, e.g. "Ia='30'A"). This recursively flattens
    any nested list/tuple structure and joins everything with spaces.
    """
    if isinstance(variation_string, (list, tuple)):
        flat_parts = []
        for item in variation_string:
            flat_parts.append(_flatten_variation_to_string(item))
        return " ".join(flat_parts)
    return str(variation_string) if variation_string is not None else ""


def extract_design_data(app, design_name, setup_dict, project_dir, design_dict):
    """Extract single design data.

    Parameters
    ----------
    app : pyaedt.application.AedtObjects
        ``pyaedt`` Electronics Desktop application object.
    design_name : str
        Name of the design
    setup_dict : dict
        Dictionary of the setups. key: setup name, value: sweeps.
    project_dir : str
        Path to the project folder
    design_dict : dict
        Dictionary {design_name: {"mesh": {}, "simulation_time": {}, "report": {}}}.

    Returns
    -------
    design_dict : dict
        Dictionary with values of mesh elements, simulation_time and report data.

    """

    for setup, sweep in setup_dict.items():
        if app.design_type == "HFSS 3D Layout Design":
            variation_strings = app.list_of_variations(setup, sweep.lstrip(setup + " : "))
        else:
            try:
                variation_strings = app.available_variations.variations(setup_sweep=sweep)
            except Exception as exc:
                # pyaedt's available_variations.variations() raises
                # "Error in splitting the variation variable." when the design
                # has no parametric/design variables at all (e.g. a typical
                # single-point Icepak SteadyState setup). In that case there is
                # only the nominal variation, so fall back to a single empty
                # variation string instead of crashing the whole extraction.
                logger.warning(
                    "available_variations.variations() failed for design {} setup {}: {}. "
                    "Falling back to nominal variation only.".format(design_name, setup, exc)
                )
                variation_strings = [""]
        if not variation_strings:
            continue
        for variation_string in variation_strings:
            # pyaedt 1.3.0's available_variations.variations() may return each
            # variation as a (possibly nested) list of "var='val'" tokens instead
            # of a single space-joined string (older API behavior). Flatten and
            # join to a string so downstream code (compose_variation_string,
            # export_profile, export_mesh_stats) keeps working unchanged.
            variation_string = _flatten_variation_to_string(variation_string)

            variation_name = "nominal" if not variation_string else compose_variation_string(variation_string)

            if variation_name not in design_dict[design_name]["mesh"]:
                design_dict[design_name]["mesh"][variation_name] = {}
            if variation_name not in design_dict[design_name]["simulation_time"]:
                design_dict[design_name]["simulation_time"][variation_name] = {}
            if variation_name not in design_dict[design_name]["profile_name"]:
                design_dict[design_name]["profile_name"][variation_name] = {}
            if variation_name not in design_dict[design_name]["mesh_name"]:
                design_dict[design_name]["mesh_name"][variation_name] = {}

            try:
                profile_file = generate_unique_file_path(project_dir, ".prof")
                profile_file = app.export_profile(setup, variation_string, profile_file)
            except Exception as exc:
                # pyaedt's export_profile() -> nominal_variation() ->
                # variable_manager.variables crashes with
                # "'NoneType' object is not iterable" when the design/project
                # has NO variables at all (GetVariables() COM call returns
                # None instead of an empty list, and pyaedt does not guard
                # against that). This typically happens together with the
                # available_variations.variations() bug above, for fully
                # non-parametric designs (single nominal operating point).
                # Log and skip this variation instead of crashing the script.
                msg = "Design:{} Variation:{} Setup:{} export_profile failed: {}".format(
                    design_name, variation_name, setup, exc
                )
                logger.warning(msg)
                PROJECT_DICT["error_exception"].append(msg)
                design_dict[design_name]["simulation_time"][variation_name][setup] = None
                design_dict[design_name]["mesh"][variation_name][setup] = None
                design_dict[design_name]["profile_name"][variation_name][setup] = None
                design_dict[design_name]["mesh_name"][variation_name][setup] = None
                continue

            simulation_time, cell_number = parse_profile_file(profile_file, design_name, variation_name, setup)
            design_dict[design_name]["simulation_time"][variation_name][setup] = simulation_time

            if app.design_type == "Icepak":
                design_dict[design_name]["mesh"][variation_name][setup] = cell_number
                mesh_stats_file = profile_file
            else:
                try:
                    mesh_stats_file = generate_unique_file_path(project_dir, ".mstat")
                    app.export_mesh_stats(setup, variation_string, mesh_stats_file)
                    mesh_data = parse_mesh_stats(mesh_stats_file, design_name, variation_name, setup)
                except Exception as exc:
                    # Same class of pyaedt bug as export_profile() above can
                    # also hit export_mesh_stats() for variable-less designs.
                    msg = "Design:{} Variation:{} Setup:{} export_mesh_stats failed: {}".format(
                        design_name, variation_name, setup, exc
                    )
                    logger.warning(msg)
                    PROJECT_DICT["error_exception"].append(msg)
                    mesh_stats_file = None
                    mesh_data = None
                design_dict[design_name]["mesh"][variation_name][setup] = mesh_data

            design_dict[design_name]["profile_name"][variation_name][setup] = profile_file
            design_dict[design_name]["mesh_name"][variation_name][setup] = mesh_stats_file

    return design_dict


def compose_variation_string(variation_string):
    """Format the variation string.

    Parameters
    ----------
    variation_string : str
        Variation string from electronics desktop.

    Returns
    -------
    variation_name : str
        Formatted variation string

    """
    strings = variation_string.split(" ")
    variation_name = ""
    for string in strings:
        if "=" in string:  # smith chart curve key is real and imag
            var, val = string.split("=")
            val = val.replace("'", "")
            val = val.replace('"', "")
            val, unit = parse_value_with_unit(val)
            variation_name += "{}={}{} ".format(var, val, unit)
        else:
            variation_name = string
    variation_name = variation_name.strip()
    return variation_name


def extract_reports_data(app, design_name, project_dir, report_names):
    """Get the report data form .rdat file.

    Parameters
    ----------
    app : pyaedt.application.AedtObjects
        Any ``pyaedt`` Electronics Desktop application object.
    design_name : str
        Name of the design.
    project_dir : str
        Path to the project.
    report_names : list
        List of report names.

    Returns
    -------
    report_dict : dictionary
        Dictionary includes all data from report.
    """
    report_dict = {}

    if not report_names:
        PROJECT_DICT["error_exception"].append("{} has no report".format(design_name))
    else:
        for report in report_names:
            report_file = app.post.export_report_to_file(
                output_dir=project_dir, plot_name=report, extension=".rdat", unique_file=True
            )
            data_dict = parse_rdat_file(report_file)
            data_dict = compose_curve_keys(data_dict)
            data_dict = check_nan(data_dict)
            report_dict.update(data_dict)

    return report_dict


def compose_curve_keys(data_dict):
    """Format the curve keys' number to 0.9e.

    Parameters
    ----------
    data_dict : dict
        Report data dictionary.

    Returns
    -------
    data_dict : dict
        Report data dictionary with formatted keys.

    """
    for plot_name in data_dict.keys():
        for trace_name in data_dict[plot_name].keys():
            curves_dict = data_dict[plot_name][trace_name]["curves"]
            for curve_name in list(curves_dict.keys()):
                if not curve_name:
                    curve_name_composed = "nominal"
                else:
                    curve_name_composed = compose_variation_string(curve_name)

                curves_dict[curve_name_composed] = curves_dict.pop(curve_name)
    return data_dict


def check_nan(data_dict):
    """Remove the curve if ``nan`` is in ``x`` or ``y`` data.

    Parameters
    ----------
    data_dict : dict
        Report data dictionary.

    Returns
    -------
    data_dict : dict
        Checked report data dictionary.

    """

    for plot_name in data_dict.keys():
        for trace_name in data_dict[plot_name].keys():
            curves_dict = data_dict[plot_name][trace_name]["curves"]
            for curve_name in list(curves_dict.keys()):

                if any(not isinstance(x, (float, int)) for x in curves_dict[curve_name]["x_data"]) or any(
                    not isinstance(x, (float, int)) for x in curves_dict[curve_name]["y_data"]
                ):
                    curves_dict.pop(curve_name)

    return data_dict


def generate_unique_file_path(project_dir, extension):
    """Generate a unique file path.

    Parameters
    ----------
    project_dir : str
        Path to the project dir.
    extension : str
        Specified file extension.

    Returns
    -------
    file_path : str
        Unique path for the file.

    """
    file_name = generate_unique_name("")
    file_path = os.path.join(project_dir, file_name + extension)

    while os.path.exists(file_path):
        file_name = generate_unique_name(file_name)
        file_path = os.path.join(project_dir, file_name + extension)

    return file_path


def main():
    """Open the project and extract data.

    If ``--port`` is given, attach to an already-running AEDT gRPC session
    (in-line mode, used right after a solve started by ``aedt_test_runner.py``).
    Otherwise, launch a fresh standalone AEDT instance (``new_desktop=True``),
    used by the decoupled "collect" step that loops over already-solved
    projects on disk, one at a time, with no live session to attach to.

    Straight-line flow, no fallbacks:
    Attach/launch -> open project (if needed) -> activate project -> read
    design names from AEDT itself -> extract/analyze -> dump JSON -> release.
    """
    if not project_path_arg:
        raise RuntimeError("--project-path argument is required but was not provided")

    project_path = os.path.abspath(project_path_arg)
    project_name = os.path.splitext(os.path.basename(project_path))[0]
    project_dir = os.path.dirname(project_path)

    if grpc_port:
        desktop_kwargs = {
            "version": specified_version,
            "port": grpc_port,
            "non_graphical": True,
            "new_desktop": False,
            "close_on_exit": False,
        }
        if grpc_machine not in ("", "localhost", "127.0.0.1"):
            desktop_kwargs["machine"] = grpc_machine
        logger.info(
            "CONNECTING to AEDT gRPC session (port={}, machine={})".format(grpc_port, grpc_machine or "localhost")
        )
    else:
        desktop_kwargs = {
            "version": specified_version,
            "non_graphical": True,
            "new_desktop": True,
            "close_on_exit": True,
        }
        logger.info("LAUNCHING standalone AEDT instance (no --port given)")

    desktop = Desktop(**desktop_kwargs)
    logger.info("CONNECTED")

    try:
        logger.info("PROJECTS: {}".format(list(desktop.project_list)))

        if project_name not in list(desktop.project_list):
            logger.info("Project not open yet, loading: {}".format(project_path))
            desktop.load_project(project_path)

        desktop.odesktop.SetActiveProject(project_name)
        oproject = desktop.odesktop.GetActiveProject()

        if design_names_arg:
            design_names = [d.strip() for d in design_names_arg.split(",") if d.strip()]
        else:
            design_names = list(oproject.GetTopDesignList())

        if not design_names:
            raise RuntimeError("No designs found after opening {}".format(project_path))

        logger.info("DESIGNS: {}".format(design_names))

        designs_dict = extract_data(desktop, project_dir, project_name, design_names)
        PROJECT_DICT["designs"].update(designs_dict)

        logger.info("Finished extraction for {}".format(project_path))

        results_json = os.path.join(project_dir, project_name + ".json")
        with open(results_json, "w") as outfile:
            json.dump(PROJECT_DICT, outfile, indent=4)

        logger.debug("JSON dumped to {}".format(results_json))

    finally:
        # Shut the session down so that the caller (aedt_test_runner.py or the
        # collect step) can reap the process and free the allocated cores.
        logger.info("RELEASING desktop")
        try:
            desktop.release_desktop(close_projects=True, close_on_exit=True)
        except Exception as exc:
            logger.warning("release_desktop() failed: {}".format(exc))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception(str(exc))
        raise
