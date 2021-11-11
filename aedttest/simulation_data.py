import argparse
import json
import os
import re
import shlex
import sys

from pyaedt import get_pyaedt_app  # noqa: E402
from pyaedt.desktop import Desktop  # noqa: E402
from pyaedt.generic.general_methods import generate_unique_name
from pyaedt.generic.report_file_parser import parse_rdat_file

DEBUG = False if "oDesktop" in dir() else True


def parse_args():
    arg_string = ScriptArgument  # noqa: F821
    parser = argparse.ArgumentParser()
    parser.add_argument("--pyaedt-path")
    args = parser.parse_args(shlex.split(arg_string))
    return args.pyaedt_path


if not DEBUG:
    pyaedt_path = parse_args()
    sys.path.append(pyaedt_path)
    specified_version = None
else:
    parser = argparse.ArgumentParser()
    parser.add_argument("--desktop-version", default="2021.1")
    args = parser.parse_args()
    specified_version = args.desktop_version

project_dict = {"error_exception": [], "designs": {}}


class AedtTestException(Exception):
    """Base class for exceptions in this module."""


def parse_mesh_stats(mesh_stats_file, design, variation, setup):

    with open(mesh_stats_file) as fid:
        lines = fid.readlines()

    for line in lines:
        if "Total number of mesh elements" in line:
            return int(line.strip().split(":")[1])
    else:
        project_dict["error_exception"].append(
            "Design:{} Variation: {} Setup: {} has no mesh stats".format(design, variation, setup)
        )


def parse_profile_file(profile_file, design, variation, setup):
    elapsed_time = ""
    with open(profile_file) as file:
        for line in file:
            if "Elapsed time" in line:
                elapsed_time = line

    if elapsed_time:
        split_line = elapsed_time.split("Elapsed time")[1]

        simulation_time = re.findall(r"[0-9]*:[0-9][0-9]:[0-9][0-9]", split_line)[0]
        return simulation_time
    else:
        project_dict["error_exception"].append(
            ("Design:{} Variation:{} Setup:{} no elapsed time in file".format(design, variation, setup))
        )


def extract_data(desktop, project_dir, design_names):
    designs_dict = {}

    for design_name in design_names:
        design_dict = {design_name: {"mesh": {}, "simulation_time": {}, "report": {}}}
        app = get_pyaedt_app(design_name=design_name)
        setups_names = app.setup_names
        if not setups_names:
            project_dict["error_exception"].append("Design {} has no setups".format(design_name))
            designs_dict.update(design_dict)
            continue

        sweeps = app.existing_analysis_sweeps
        setup_dict = dict(zip(setups_names, sweeps))
        design_dict = extract_design_data(
            desktop=desktop,
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


def extract_design_data(desktop, app, design_name, setup_dict, project_dir, design_dict):

    success = desktop.analyze_all(design=design_name)
    if success:
        for setup, sweep in setup_dict.items():
            variation_strings = app.available_variations.get_variation_strings(sweep)
            for variation_string in variation_strings:
                variation_name = "nominal" if not variation_string else variation_string

                if variation_name not in design_dict[design_name]["mesh"]:
                    design_dict[design_name]["mesh"][variation_name] = {}
                if variation_name not in design_dict[design_name]["simulation_time"]:
                    design_dict[design_name]["simulation_time"][variation_name] = {}

                mesh_stats_file = generate_unique_file_path(project_dir, ".mstat")
                app.export_mesh_stats(setup, variation_string, mesh_stats_file)
                mesh_data = parse_mesh_stats(mesh_stats_file, design_name, variation_string, setup)
                design_dict[design_name]["mesh"][variation_name][setup] = mesh_data

                profile_file = generate_unique_file_path(project_dir, ".prof")
                app.export_profile(setup, variation_string, profile_file)
                simulation_time = parse_profile_file(profile_file, design_name, variation_string, setup)
                design_dict[design_name]["simulation_time"][variation_name][setup] = simulation_time

        return design_dict
    else:
        project_dict["error_exception"].append("{} analyze_all failed".format(design_name))


def extract_reports_data(app, design_name, project_dir, report_names):
    report_dict = {}
    if not report_names:
        project_dict["error_exception"].append("{} has no report".format(design_name))
    else:
        for report in report_names:
            report_file = app.post.export_report_to_file(
                output_dir=project_dir, plot_name=report, extension=".rdat", unique_file=True
            )
            data_dict = parse_rdat_file(report_file)
            check_nan(data_dict)
            report_dict.update(data_dict)

    return report_dict


def check_nan(data_dict):
    for plot_name, traces_dict in data_dict.items():
        for trace_name in traces_dict:
            curves_dict = traces_dict[trace_name]["curves"]
            for curve_name in curves_dict:
                if any(not isinstance(x, (float, int)) for x in curves_dict[curve_name]["x_data"]):
                    project_dict["error_exception"].append(
                        "Plot:{} Trace:{} Curve:{} X value not int or float".format(plot_name, trace_name, curve_name)
                    )

                if any(not isinstance(x, (float, int)) for x in curves_dict[curve_name]["y_data"]):
                    project_dict["error_exception"].append(
                        "Plot:{} Trace:{} Curve:{} Y value not int or float".format(plot_name, trace_name, curve_name)
                    )


def generate_unique_file_path(project_dir, extension):
    file_name = generate_unique_name("")

    file_path = os.path.join(project_dir, file_name + extension)

    while os.path.exists(file_path):
        file_name = generate_unique_name(file_name)
        file_path = os.path.join(project_dir, file_name + extension)

    return file_path


def main():

    desktop = Desktop(specified_version=specified_version, non_graphical=False, new_desktop_session=False)

    project_name = desktop.project_list().pop()
    project_dir = desktop.project_path(project_name=project_name)
    design_names = desktop.design_list()

    if design_names:
        designs_dict = extract_data(desktop, project_dir, design_names)
        project_dict["designs"].update(designs_dict)
    else:
        project_dict["error_exception"].append("Project has no design")

    out_json = r"{}.json".format(project_name)
    with open(os.path.join(project_dir, out_json), "w") as outfile:
        json.dump(project_dict, outfile, indent=4)


if __name__ == "__main__":
    main()
