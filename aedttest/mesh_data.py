import json
import os

from pyaedt.desktop import Desktop

project_dict = {"error_exception": []}


class AedtTestException(Exception):
    """Base class for exceptions in this module."""


def get_designs(oProject):
    """
    get all the designs of the project. If no design, raise error
    Args:
        oProject:

    Returns:
        design_names list
    """

    design_names = oProject.GetTopDesignList()
    if not design_names:
        raise AedtTestException("Project has no design")

    return design_names


def get_single_setup_mesh_data(oDesign, var, setup, mesh_stats_file):
    oDesign.GenerateMesh(setup)
    oDesign.ExportMeshStats(setup, var, mesh_stats_file)

    with open(mesh_stats_file) as fid:
        lines = fid.readlines()

    line = [x for x in lines if "Total number of mesh elements" in x]

    mesh_data = int(line[0].strip().split(":")[1])

    return mesh_data


def get_all_setup_mesh_data(oDesign, design, design_dict, project_dir, project_name):
    oModule = oDesign.GetModule("AnalysisSetup")
    setups = oModule.GetSetups()

    if not setups:
        raise AedtTestException("Design {} has no setup".format(design))
    for setup in setups:
        design_dict[design][setup] = {}
        mesh_stats_file = r"{}_{}_{}.mstat".format(project_name, design, setup)
        mesh_data = get_single_setup_mesh_data(
            oDesign=oDesign,
            var="",
            setup=setup,
            mesh_stats_file=os.path.join(project_dir, mesh_stats_file),
        )

        design_dict[design][setup]["mesh_data"] = mesh_data


def extract_data(oProject, project_dir, project_name):
    design_dict = {}
    # get the designs, if no design raise error
    design_names = get_designs(oProject)

    # loop all design and  get all setups for each design
    for design in design_names:
        design_dict[design] = {}
        try:
            oDesign = oProject.SetActiveDesign(design)
            get_all_setup_mesh_data(oDesign, design, design_dict, project_dir, project_name)
        except AedtTestException as e:
            project_dict["error_exception"].append(str(e))
    return design_dict


def main():

    d = Desktop("2021.1", False, False)

    oDesktop = d._main.oDesktop
    oProject = oDesktop.GetActiveProject()
    project_name = oProject.GetName()
    project_dir = oProject.GetPath()

    try:
        design_dict = extract_data(oProject, project_dir, project_name)
        project_dict.update(design_dict)
    except AedtTestException as e:
        project_dict["error_exception"].append(str(e))

    out_json = r"{}.json".format(project_name)
    with open(os.path.join(project_dir, out_json), "w") as outfile:
        json.dump(project_dict, outfile, indent=4)


if __name__ == "__main__":
    main()
