"""Expand list-valued TOML fields into a Cartesian-product test matrix.

Level 3 of the pipeline: a single project TOML can sweep several parameters
at once, e.g.::

    [project.distribution]
    cores = [4, 8, 16]

    [project.slurm]
    nodes = [1, 2]

This expands into 6 distinct job variants (4x1, 4x2, 8x1, 8x2, 16x1, 16x2),
each submitted as its own SLURM job with its own project-name suffix.

Only an explicit allowlist of keys may be swept - fields that are naturally
list-valued (``distribution_types``, ``machinelist_args``, ``batch_options``,
``extra_sbatch_args``, ...) are left untouched even if they are lists.
"""

import itertools
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple

# section -> keys allowed to be swept (i.e. a list value there means "sweep this",
# not "this is a literal list argument").
SWEEPABLE_KEYS: Dict[str, set] = {
    "distribution": {"cores", "parametric_tasks"},
    "slurm": {"nodes", "ntasks_per_node", "cpus_per_task", "partition"},
}


def _find_sweep_axes(project_config: Dict[str, Any]) -> List[Tuple[str, str, List[Any]]]:
    """Return [(section, key, values), ...] for every list-valued sweepable field present."""
    axes = []
    for section, allowed_keys in SWEEPABLE_KEYS.items():
        section_config = project_config.get(section, {})
        for key in allowed_keys:
            value = section_config.get(key)
            if isinstance(value, list):
                axes.append((section, key, value))
    return axes


def _variant_name(base_name: str, axes: List[Tuple[str, str, List[Any]]], combo: Tuple[Any, ...]) -> str:
    suffix = "_".join(f"{key}{value}" for (_section, key, _values), value in zip(axes, combo))
    return f"{base_name}__{suffix}"


def expand_matrix(project_tests_config: Dict[str, Any]) -> Dict[str, Any]:
    """Expand every project with list-valued sweepable fields into named variants.

    Projects with no sweepable list fields pass through unchanged (same name).

    Parameters
    ----------
    project_tests_config : dict
        Output of ``aedttest.aedt_test_runner.read_configs`` (or
        ``aedttest.clusters.slurm_submit.discover_projects_from_folder``).

    Returns
    -------
    dict
        Same shape, but projects with sweeps replaced by N named variants,
        e.g. ``my_project__cores4_nodes1``, ``my_project__cores4_nodes2``, ...

    """
    expanded: Dict[str, Any] = {}

    for project_name, project_config in project_tests_config.items():
        axes = _find_sweep_axes(project_config)
        if not axes:
            expanded[project_name] = project_config
            continue

        axis_values = [values for (_section, _key, values) in axes]
        for combo in itertools.product(*axis_values):
            variant_name = _variant_name(project_name, axes, combo)
            variant_config = {
                "path": project_config["path"],
                "dependencies": list(project_config.get("dependencies", [])),
                "distribution": dict(project_config.get("distribution", {})),
                "slurm": dict(project_config.get("slurm", {})),
            }
            for (section, key, _values), value in zip(axes, combo):
                variant_config[section][key] = value

            expanded[variant_name] = variant_config

    return expanded
