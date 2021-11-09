import argparse
import json
import os
import re
import shlex
import sys

from pyaedt import get_pyaedt_app  # noqa: E402
from pyaedt.desktop import Desktop  # noqa: E402


def parse_args():
    arg_string = ScriptArgument  # noqa: F821
    parser = argparse.ArgumentParser(description="Argparse Test script")
    parser.add_argument("--path1")
    args = parser.parse_args(shlex.split(arg_string))
    return args.path1


try:
    pyaedt_path = parse_args()
    sys.path.append(pyaedt_path)
except NameError:
    pass

project_dict = {"error_exception": []}


class AedtTestException(Exception):
    """Base class for exceptions in this module."""


def parse_mesh_stats(mesh_stats_file, design, setup):

    with open(mesh_stats_file) as fid:
        lines = fid.readlines()

    for line in lines:
        if "Total number of mesh elements" in line:
            return int(line.strip().split(":")[1])
    else:
        project_dict["error_exception"].append("{} {} has no total number of mesh".format(design, setup))


def parse_profile_file(profile_file, design, setup):
    elapsed_time = ""
    with open(profile_file) as file:
        for line in file:
            if "Elapsed time" in line:
                elapsed_time = line

    if elapsed_time:
        simulation_time = re.findall(r"[0-9]*:[0-9][0-9]:[0-9][0-9]", elapsed_time)[2]
        return simulation_time
    else:
        project_dict["error_exception"].append(("{} {} no elapsed time in file".format(design, setup)))


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


def extract_data(project_dir, project_name, design_names):
    designs_dict = {}

    for design in design_names:
        app = get_pyaedt_app(design_name=design)
        design_dict = extract_setup_data(app, design, project_dir, project_name)
        designs_dict.update(design_dict)
        report_names = app.post.all_report_names
        reports_dict = extract_reports_data(app, design, design_dict, project_name, project_dir, report_names)
        designs_dict[design].update(reports_dict)
    return designs_dict


def extract_reports_data(app, design, design_dict, project_name, project_dir, report_names):
    reports_dict = {"report": {}}
    if not report_names:
        project_dict["error_exception"].append("{} has no report".format(design))
        design_dict[design]["report"] = {}
    else:
        for report in report_names:
            reports_dict["report"][report] = {}
            app.post.export_report_to_file(project_dir=project_dir, plot_name=report, extension=".txt")
            report_file = os.path.join(project_dir, r"{}.txt".format(report))
            single_report = parse_report(txt_file=report_file)
            # reports_dict["report"]
            reports_dict["report"][report].update(single_report)

    return reports_dict


def extract_setup_data(app, design, project_dir, project_name):
    """
    extract mesh data and simulation time from setups
    Args:
        app:
        design:
        design_dict:
        project_dir:
        project_name:

    Returns:

    """
    design_dict = {design: {}}
    setups_names = app.get_setups()
    if not setups_names:
        project_dict["error_exception"].append("{} has no setups".format(design))
    else:
        for setup in setups_names:
            design_dict[design][setup] = {}
            success = app.analyze_setup(name=setup)
            if not success:
                project_dict["error_exception"].append("{} {} Analyze failed".format(design, setup))
                continue

            mesh_stats_file = os.path.join(project_dir, "{}_{}_{}.mstat".format(project_name, design, setup))
            app.export_mesh_stats(setup_name=setup, variation_string="", mesh_path=mesh_stats_file)
            mesh_data = parse_mesh_stats(mesh_stats_file=mesh_stats_file, design=design, setup=setup)
            design_dict[design][setup]["mesh_data"] = mesh_data

            profile_file = os.path.join(project_dir, "{}_{}_{}.prof".format(project_name, design, setup))
            app.export_profile(setup_name=setup, variation_string="", file_path=profile_file)
            simulation_time = parse_profile_file(profile_file=profile_file, design=design, setup=setup)
            design_dict[design][setup]["simulation_time"] = simulation_time

        return design_dict


def main():
    specified_version = None
    desktop = Desktop(specified_version=specified_version, non_graphical=False, new_desktop_session=False)

    project_name = desktop.project_list().pop()
    project_dir = desktop.project_path(project_name=project_name)
    design_names = desktop.design_list()

    if design_names:
        designs_dict = extract_data(project_dir, project_name, design_names)
        project_dict.update(designs_dict)
    else:
        project_dict["error_exception"].append("Project has no design")

    out_json = r"{}.json".format(project_name)
    with open(os.path.join(project_dir, out_json), "w") as outfile:
        json.dump(project_dict, outfile, indent=4)


if __name__ == "__main__":
    main()
