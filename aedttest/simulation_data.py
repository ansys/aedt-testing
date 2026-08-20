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

    This replaces the old IronPython-specific ScriptArgument-based parsing.
    The script is now always launched by launcher.py via subprocess, so standard
    sys.argv argument parsing is used.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--logfile-path", default=None)
    parser.add_argument("--aedt-version", default=None,
                        help="AEDT version to connect to (e.g. '2025.1'), must match the outer -RunScriptAndExit process")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--port", type=int, default=0,
                        help="gRPC port of the running AEDT session (passed by launcher.py)")
    parser.add_argument("--project-path", default=None,
                        help="Full path to the .aedt project file being tested")
    parser.add_argument("--design-names", default=None,
                        help="Comma-separated design names extracted by launcher.py via IronPython oDesktop")
    args = parser.parse_args()
    logfile_path = args.logfile_path or os.path.join(MODULE_DIR_PARENT, "aedt_test_framework.log")
    return logfile_path, args.debug, args.port, args.project_path, args.design_names, args.aedt_version


log_level = logging.DEBUG
logfile_path, debug, grpc_port, project_path_arg, design_names_arg, specified_version = parse_args()
# specified_version must match the AEDT version of the outer -RunScriptAndExit
# process (passed by launcher.py via --aedt-version), since we connect to an
# already running session (new_desktop=False).
if not debug:
    log_level = logging.INFO

try:
    # NEW:
    import ansys.aedt.core as pyaedt
    from ansys.aedt.core import get_pyaedt_app
    from ansys.aedt.core import Desktop
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

    for design_name in design_names:
        design_dict = {
            design_name: {"mesh": {}, "simulation_time": {}, "report": {}, "profile_name": {}, "mesh_name": {}}
        }
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
            analyze_success = desktop.analyze_all(design=design_name)
        except Exception as exc:
            # analyze_all is wrapped by pyaedt_function_handler which may re-raise
            # instead of returning False. Treat any exception as a failed analysis
            # so we still capture messages and write partial results instead of
            # crashing the whole script.
            logger.error("design {} 'analyze_all' raised exception: {}".format(design_name, exc))
            analyze_success = False
        logger.info("END ANALYZE: {}".format(design_name))

        if not analyze_success:
            logger.error("design {} 'analyze_all' failed".format(design_name))
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

        report_names = app.post.all_report_names
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
            variation_strings = app.available_variations.variations(setup_sweep=sweep)
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

            profile_file = generate_unique_file_path(project_dir, ".prof")
            profile_file = app.export_profile(setup, variation_string, profile_file)
            simulation_time, cell_number = parse_profile_file(profile_file, design_name, variation_name, setup)
            design_dict[design_name]["simulation_time"][variation_name][setup] = simulation_time

            if app.design_type == "Icepak":
                design_dict[design_name]["mesh"][variation_name][setup] = cell_number
                mesh_stats_file = profile_file
            else:
                mesh_stats_file = generate_unique_file_path(project_dir, ".mstat")
                app.export_mesh_stats(setup, variation_string, mesh_stats_file)
                mesh_data = parse_mesh_stats(mesh_stats_file, design_name, variation_name, setup)
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


def parse_design_names_from_aedt_file(aedt_file_path):
    """Extract design names by parsing the .aedt project file directly.

    Needed because in -auto -machinelist batch mode, neither IronPython's
    oDesktop.GetActiveProject() nor gRPC's desktop.design_list() can see
    the project (confirmed empty in both cases).

    Parameters
    ----------
    aedt_file_path : str
        Path to the .aedt project file.

    Returns
    -------
    design_names : list
        List of design names found in the project file.
    """
    design_names = []
    pattern = re.compile(r"DesignName\s*=\s*'([^']+)'", re.IGNORECASE)
    try:
        with open(aedt_file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for m in pattern.finditer(content):
            if m.group(1) not in design_names:
                design_names.append(m.group(1))
    except Exception as e:
        logger.warning("Could not parse .aedt file for design names: {}".format(e))
    return design_names


def main():
    # Derive project name and dir directly from the --project-path argument.
    # This avoids querying desktop.project_list which is unreliable in
    # non-graphical -RunScriptAndExit mode with pyaedt 1.3.0 gRPC.
    if project_path_arg:
        project_path = project_path_arg
        project_name = os.path.splitext(os.path.basename(project_path))[0]
        project_dir = os.path.dirname(project_path)
    else:
        raise RuntimeError("--project-path argument is required but was not provided")

    logger.info("Start extraction for {}".format(project_path))

    # Connect to the running AEDT session via gRPC.
    desktop = Desktop(
        version=specified_version,
        machine="localhost",
        port=grpc_port,
        non_graphical=False,
        new_desktop=False,
    )

    # design_names_arg is passed from launcher.py via IronPython oDesktop.GetActiveProject()
    # This is the only reliable source in -RunScriptAndExit batch mode.
    # gRPC-based design_list() always returns [] in this mode.
    if design_names_arg:
        design_names = [d.strip() for d in design_names_arg.split(",") if d.strip()]
        logger.info("design_names from launcher.py (IronPython): {}".format(design_names))
    else:
        # Fallback: try gRPC (may return [] in RunScriptAndExit mode)
        design_names = desktop.design_list()
        logger.info("design_names via gRPC fallback: {}".format(design_names))

    if not design_names:
        # Last resort: parse the .aedt project file directly (no AEDT API needed at all)
        design_names = parse_design_names_from_aedt_file(project_path)
        logger.info("design_names from .aedt file parsing: {}".format(design_names))

    # The AEDT session started via -RunScriptAndExit does NOT automatically open
    # the project passed on the command line before the script executes (confirmed:
    # oDesktop.GetActiveProject() and desktop.project_list are both empty at this point).
    # Explicitly open the project ourselves using the native AEDT scripting API.
    if project_name not in list(desktop.project_list):
        logger.info("Project not open yet, opening explicitly: {}".format(project_path))
        desktop.odesktop.OpenProject(project_path)
        logger.info("project_list after OpenProject: {}".format(list(desktop.project_list)))

    if design_names:
        designs_dict = extract_data(desktop, project_dir, project_name, design_names)
        PROJECT_DICT["designs"].update(designs_dict)
    else:
        PROJECT_DICT["error_exception"].append("Project has no design")

    logger.info("Finished extraction for {}".format(project_path))

    results_json = os.path.join(project_dir, project_name + ".json")
    with open(results_json, "w") as outfile:
        json.dump(PROJECT_DICT, outfile, indent=4)

    logger.debug("JSON dumped to {}".format(results_json))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception(str(exc))
        raise
