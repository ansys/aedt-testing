import argparse
import json
import os
import re
import shlex
import sys

from pyaedt import get_pyaedt_app  # noqa: E402
from pyaedt.desktop import Desktop  # noqa: E402
from pyaedt.generic.general_methods import generate_unique_name
from pyaedt.generic.report_file_parser import parse_file

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

project_dict = {"error_exception": []}


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
        simulation_time = re.findall(r"[0-9]*:[0-9][0-9]:[0-9][0-9]", elapsed_time)[2]
        return simulation_time
    else:
        project_dict["error_exception"].append(
            ("Design:{} Variation:{} Setup:{} no elapsed time in file".format(design, variation, setup))
        )


def parse_report(txt_file):
    report_dict = {"variation": {}}

    with open(txt_file) as file:
        lines = file.readlines()[5:]

    traces = lines.pop(0).strip()
    traces = re.split(r"\s{2,}", traces)
    report_dict["x_label"] = traces.pop(0)
    report_dict["x_data"] = []

    variations = lines.pop(0).strip()

    if not variations:
        variations = ["nominal"] * len(traces)
    else:
        variations = re.split(r"\s{2,}", variations)

    # increment of duplicated traces under each variation
    trace_duplicate_count = {}
    new_traces = []
    for variation, trace in zip(variations, traces):
        if variation not in report_dict["variation"]:
            report_dict["variation"][variation] = {}
            trace_duplicate_count[variation] = {}

        if trace in report_dict["variation"][variation]:
            trace_duplicate_count[variation][trace] += 1
            trace += "(" + str(trace_duplicate_count[variation][trace]) + ")"
        else:
            trace_duplicate_count[variation][trace] = 0

        report_dict["variation"][variation][trace] = []
        new_traces.append(trace)

    for line in lines:
        xy_values = [float(x) for x in line.strip().split()]  # todo nan
        report_dict["x_data"].append(xy_values[0])

        for variation, trace, value in zip(variations, new_traces, xy_values[1:]):
            report_dict["variation"][variation][trace].append(value)

    return report_dict


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

        designs_dict.update(design_dict)

        report_names = app.post.all_report_names

        if not report_names:
            project_dict["error_exception"].append("Design {} has no reports".format(design_name))
            continue

        for report in report_names:
            report_file_path = app.post.export_report_to_file(
                output_dir=project_dir, plot_name=report, extension=".rdat", unique_file=True
            )

            report_dict = parse_file(report_file_path)

            design_dict[design_name]["report"].update(report_dict)

    return designs_dict


def extract_design_data(desktop, app, design_name, setup_dict, project_dir, design_dict):

    success = desktop.analyze_all(design=design_name)
    if success:
        for setup, sweep in setup_dict.items():
            variation_strings = app.available_variations.get_variation_strings(sweep)
            if not variation_strings[0]:
                mesh_stats_file = generate_unique_file_path(project_dir, ".mstat")
                app.export_mesh_stats(setup, "", mesh_stats_file)
                mesh_data = parse_mesh_stats(mesh_stats_file, design_name, "nominal", setup)
                design_dict[design_name]["mesh"] = {"nominal": {setup: mesh_data}}

                profile_file = generate_unique_file_path(project_dir, ".prof")
                app.export_profile(setup, "", profile_file)
                simulation_time = parse_profile_file(profile_file, design_name, "nominal", setup)
                design_dict[design_name]["simulation_time"] = {"nominal": {setup: simulation_time}}
                continue

            for variation_string in variation_strings:
                design_dict[design_name]["mesh"][variation_string] = {}
                design_dict[design_name]["simulation_time"][variation_string] = {}

                mesh_stats_file = generate_unique_file_path(project_dir, ".mstat")
                app.export_mesh_stats(setup, variation_string, mesh_stats_file)
                mesh_data = parse_mesh_stats(mesh_stats_file, design_name, variation_string, setup)
                design_dict[design_name]["mesh"][variation_string][setup] = mesh_data

                profile_file = generate_unique_file_path(project_dir, ".prof")
                app.export_profile(setup, variation_string, profile_file)
                simulation_time = parse_profile_file(profile_file, design_name, variation_string, setup)
                design_dict[design_name]["simulation_time"][variation_string][setup] = simulation_time

        # todo add report
        return design_dict
    else:
        project_dict["error_exception"].append("{} analyze_all failed".format(design_name))


def extract_reports_data(app, design_name, design_dict, project_name, project_dir, report_names):
    reports_dict = {"report": {}}
    if not report_names:
        project_dict["error_exception"].append("{} has no report".format(design_name))
        design_dict[design_name]["report"] = {}
    else:
        for report in report_names:
            reports_dict["report"][report] = {}

            report_file = app.post.export_report_to_file(
                output_dir=project_dir, plot_name=report, extension=".rdat", unique_file=True
            )

            single_report = parse_file(report_file)

            reports_dict["report"][report].update(single_report)

    return reports_dict


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
        project_dict.update(designs_dict)
    else:
        project_dict["error_exception"].append("Project has no design")

    out_json = r"{}.json".format(project_name)
    with open(os.path.join(project_dir, out_json), "w") as outfile:
        json.dump(project_dict, outfile, indent=4)


if __name__ == "__main__":
    main()
