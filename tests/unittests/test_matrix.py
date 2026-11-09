from aedttest.clusters.matrix import expand_matrix


def test_expand_matrix_sweeps_listed_values():
    config = {
        "package": {
            "path": "/some/path/package.aedt",
            "dependencies": [],
            "distribution": {"cores": [4, 8], "parametric_tasks": 1},
            "slurm": {"nodes": [1, 2], "design_name": "HFSSDesign1"},
        },
    }

    result = expand_matrix(config)

    assert set(result) == {
        "package__cores4_nodes1",
        "package__cores4_nodes2",
        "package__cores8_nodes1",
        "package__cores8_nodes2",
    }
    variant = result["package__cores8_nodes2"]
    assert variant["distribution"]["cores"] == 8
    assert variant["slurm"]["nodes"] == 2
    assert variant["slurm"]["design_name"] == "HFSSDesign1"


def test_expand_matrix_passes_scalar_projects_through():
    config = {
        "single": {
            "path": "/some/path/5G.aedt",
            "dependencies": [],
            "distribution": {"cores": 4},
            "slurm": {"partition": "any"},
        },
    }

    assert expand_matrix(config) == config


def test_expand_matrix_ignores_non_allowlisted_lists():
    config = {
        "p": {
            "path": "/some/path/p.aedt",
            "dependencies": [],
            "distribution": {"cores": 4, "distribution_types": ["Variations", "Frequencies"]},
            "slurm": {},
        },
    }

    assert list(expand_matrix(config)) == ["p"]
