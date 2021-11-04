import json
import os
import re

from pyaedt.desktop import Desktop

project_dict = {"error_exception": []}


class AedtTestException(Exception):
    """Base class for exceptions in this module."""


def get_single_setup_mesh_data(oDesign, var, setup, mesh_stats_file):
    oDesign.GenerateMesh(setup)
    oDesign.ExportMeshStats(setup, var, mesh_stats_file)

    with open(mesh_stats_file) as fid:
        lines = fid.readlines()

    line = [x for x in lines if "Total number of mesh elements" in x]

    mesh_data = int(line[0].strip().split(":")[1])

    return mesh_data


def parse_profile_file(profile_file, design, setup):
    elapsed_time = ""
    with open(profile_file) as file:
        for line in file:
            if "Elapsed time" in line:
                elapsed_time = line

    if not elapsed_time:
        project_dict["error_exception"].append(("{} {} no elapsed time in file".format(design, setup)))
        return None
    else:
        simulation_time = re.findall(r"[0-9]*:[0-9][0-9]:[0-9][0-9]", elapsed_time)[2]
        return simulation_time


def get_single_setup_simu_data(oDesign, var, setup, profile_file):

    try:
        oDesign.Analyze(setup)
    except Exception:
        raise AedtTestException("Failed to analyze {}".format(setup))

    oDesign.ExportProfile(setup, var, profile_file)


def parse_report(txt_file):
    report_dict = {}

    with open(txt_file) as file:
        lines = file.readlines()[5:]

    traces = lines.pop(0).strip()
    traces = re.split(r"\s{2,}", traces)
    report_dict["x_label"] = traces.pop(0)
    report_dict["x_data"] = []

    variations = lines.pop(0).strip()

    if not variations:
        variations = ["nominal"]
    else:
        variations = re.split(r"\s{2,}", variations)

    for line in lines:
        xy_values = [float(x) for x in line.strip().split()]  # todo nan
        report_dict["x_data"].append(xy_values[0])

        for variation, trace, value in zip(variations, traces, xy_values[1:]):
            if variation not in report_dict:
                report_dict[variation] = {}

            if trace not in report_dict[variation]:
                report_dict[variation][trace] = []

            report_dict[variation][trace].append(value)

    return report_dict


def get_report_data(oDesign, design, project_dir, design_dict):

    oModule = oDesign.GetModule("ReportSetup")
    report_names = oModule.GetAllReportNames()
    report_dict = {"report": {}}
    if not report_names:
        raise AedtTestException("no report defined")

    for report in report_names:
        report_dict["report"][report] = {}

        txt_file = os.path.join(project_dir, "{}.txt".format(report))
        oModule.ExportToFile(report, txt_file, False)

        single_report = parse_report(txt_file=txt_file)
        report_dict["report"][report].update(single_report)

    design_dict[design].update(report_dict)

    return design_dict


def get_all_setup_data(oDesign, design, design_dict, project_dir, project_name):
    oModule = oDesign.GetModule("AnalysisSetup")
    setups = oModule.GetSetups()

    if not setups:
        raise AedtTestException("Design {} has no setup".format(design))
    for setup in setups:
        design_dict[design][setup] = {}
        mesh_stats_file = r"{}_{}_{}.mstat".format(project_name, design, setup)
        profile_file = r"{}_{}_{}.prof".format(project_name, design, setup)
        mesh_data = get_single_setup_mesh_data(
            oDesign=oDesign,
            var="",
            setup=setup,
            mesh_stats_file=os.path.join(project_dir, mesh_stats_file),
        )
        design_dict[design][setup]["mesh_data"] = mesh_data

        simulation_time = get_single_setup_simu_data(
            oDesign=oDesign,
            var="",
            setup=setup,
            profile_file=os.path.join(project_dir, profile_file),
        )

        design_dict[design][setup]["simulation_time"] = simulation_time

        return design_dict


def extract_data(oProject, project_dir, project_name, design_names):
    design_dict = {}

    for design in design_names:
        design_dict[design] = {}
        try:
            oDesign = oProject.SetActiveDesign(design)
            design_dict = get_all_setup_data(oDesign, design, design_dict, project_dir, project_name)
            design_dict = get_report_data(oDesign, design, project_dir, design_dict)

        except AedtTestException as e:
            project_dict["error_exception"].append(str(e))
    return design_dict


def main():
    desktop = Desktop("2021.1", False, False)
    project_name = desktop.project_list().pop()
    design_names = desktop.design_list()

    oDesktop = desktop._main.oDesktop
    oProject = oDesktop.GetActiveProject()
    project_dir = oProject.GetPath()

    if design_names:
        try:
            design_dict = extract_data(oProject, project_dir, project_name, design_names)
            project_dict.update(design_dict)
        except AedtTestException as e:
            project_dict["error_exception"].append(str(e))
    else:
        project_dict["error_exception"].append("Project has no design")

    out_json = r"{}.json".format(project_name)
    with open(os.path.join(project_dir, out_json), "w") as outfile:
        json.dump(project_dict, outfile, indent=4)


if __name__ == "__main__":
    main()
