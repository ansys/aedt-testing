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


def get_single_setup_simu_data(oDesign, var, setup, profile_file):
    oDesign.Analyze(setup)
    oDesign.ExportProfile(setup, var, profile_file)

    elapsed_time = ""
    with open(profile_file) as file:
        for line in file:
            if "Elapsed time" in line:
                elapsed_time = line

    if not elapsed_time:
        raise AedtTestException("no elapsed time in file")

    simulation_time = re.findall(r"[0-9]*:[0-9][0-9]:[0-9][0-9]", elapsed_time)[2]

    return simulation_time


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

        simulation_time = get_single_setup_simu_data(
            oDesign=oDesign,
            var="",
            setup=setup,
            profile_file=os.path.join(project_dir, profile_file),
        )

        design_dict[design][setup]["mesh_data"] = mesh_data
        design_dict[design][setup]["simulation_time"] = simulation_time


def extract_data(oProject, project_dir, project_name, design_names):
    design_dict = {}

    for design in design_names:
        design_dict[design] = {}
        try:
            oDesign = oProject.SetActiveDesign(design)
            get_all_setup_data(oDesign, design, design_dict, project_dir, project_name)
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
