import json
import os

from pyaedt.desktop import Desktop

project_dict = {"error_exception": []}


class AedtTestException(Exception):
    """Base class for exceptions in this module."""


def get_mesh_data(odesign, var: str, setup: str, mesh_stats_file: str):
    odesign.GenerateMesh(setup)
    odesign.ExportMeshStats(setup, var, mesh_stats_file)

    with open(mesh_stats_file) as fid:
        lines = fid.readlines()

    line = [x for x in lines if "Total number of mesh elements" in x]

    mesh_data = int(line[0].strip().split(":")[1])

    return mesh_data


def main():

    d = Desktop("2021.1", False, False)

    oDesktop = d._main.oDesktop
    oProject = oDesktop.GetActiveProject()

    project_name = oProject.GetName()
    project_dir = oProject.GetPath()

    try:
        design_dict = extract_data(oProject, project_dir, project_name)
    except AedtTestException as e:
        project_dict["error_exception"].append(str(e))

    out_json = r"{}.json".format(project_dict["name"])
    with open(os.path.join(project_dict["dir"], out_json), "w") as outfile:
        json.dump(design_dict, outfile, indent=4)


def extract_data(oProject, project_dir, project_name):
    design_dict = {}
    # get the designs and check empty project
    design_names = oProject.GetTopDesignList()
    if not design_names:
        raise AedtTestException("Project has no design")
    # get the setups and check empty designs
    for design in design_names:
        design_dict[design] = {}

        try:
            oDesign = oProject.SetActiveDesign(design)
            oModule = oDesign.GetModule("AnalysisSetup")
            setups = oModule.GetSetups()

            get_all_setup_mesh(design, design_dict, oDesign, project_dir, project_name, setups)

        except AedtTestException as e:
            project_dict["error_exception"].append(str(e))
    return design_dict


def get_all_setup_mesh(design, design_dict, oDesign, project_dir, project_name, setups):
    if not setups:
        raise AedtTestException("Design has no setup")
    for setup in setups:
        design_dict[design][setup] = {}
        design_dict[design][setup]["mesh_data"] = 0

        mesh_stats_file = r"{}_{}_{}.mstat".format(project_name, design, setup)

        mesh_data = get_mesh_data(
            odesign=oDesign,
            var="",
            setup=setup,
            mesh_stats_file=os.path.join(project_dir, mesh_stats_file),
        )
        design_dict[design][setup]["mesh_data"] = mesh_data


if __name__ == "__main__":
    main()
