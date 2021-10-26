import json
import os

from pyaedt.desktop import Desktop


class Error(Exception):
    """Base class for exceptions in this module."""


class DesignError(Error):
    """Exception raised for errors when project has no design.

    Attributes:
        expression -- input expression in which the error occurred
        message -- explanation of the error
    """

    def __init__(self, message="Project has no design"):
        self.message = message


class SetupError(Error):
    """Exception raised for errors when project has no design.

    Attributes:
        expression -- input expression in which the error occurred
        message -- explanation of the error
    """

    def __init__(self, message="Design has no setup"):
        self.message = message


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
    project_dict = {"name": oProject.GetName(), "dir": oProject.GetPath(), "error_exception": [], "designs": []}

    # get the designs and check empty project
    try:
        design_names = oProject.GetTopDesignList()
        if len(design_names) == 0:
            raise DesignError()
    except DesignError as e:
        project_dict["error_exception"].append(e.message)
    else:
        pass

    design_dict = {}
    # get the setups and check empty designs
    for design in design_names:
        design_dict[design] = {}
        design_dict[design]["error_exception"] = []
        try:
            oDesign = oProject.SetActiveDesign(design)
            oModule = oDesign.GetModule("AnalysisSetup")
            setups = oModule.GetSetups()

            if len(setups) == 0:
                raise SetupError()
            else:
                for setup in setups:
                    design_dict[design][setup] = {}
                    design_dict[design][setup]["error_exception"] = []
                    design_dict[design][setup]["mesh_data"] = 0

                    mesh_stats_file = r"{}_{}_{}.mstat".format(project_dict["name"], design, setup)

                    mesh_data = get_mesh_data(
                        odesign=oDesign,
                        var="",
                        setup=setup,
                        mesh_stats_file=os.path.join(project_dict["dir"], mesh_stats_file),
                    )
                    design_dict[design][setup]["mesh_data"] = mesh_data

        except SetupError as e:
            design_dict[design]["error_exception"].append(e.message)

    out_json = r"{}.json".format(project_dict["name"])

    with open(os.path.join(project_dict["dir"], out_json), "w") as outfile:
        json.dump(design_dict, outfile, indent=4)


if __name__ == "__main__":
    main()
