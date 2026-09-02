[![](https://img.shields.io/pypi/v/aedttest.svg)](https://pypi.python.org/pypi/aedttest/)
[![](https://img.shields.io/pypi/pyversions/aedttest.svg)](https://pypi.python.org/pypi/aedttest/)

## Description
This project provides an automated framework to test Ansys Electronics Desktop (AEDT).
Users can set up a suite of projects to validate stability/regression of simulation results
(XY plots, mesh statistics, simulation time) between different versions of AEDT, or simply
generate a reference baseline for a given version.

The framework drives AEDT exclusively through **PyAEDT's gRPC API** - it starts a bare
`ansysedt -ng -grpcsrv <port>` process, connects to it, opens/solves the project and extracts
the results. No AEDT-side scripts (`.py`/IronPython) or `-batchsolve` flags are used, so the
same code path works identically on Windows and Linux, locally or on a cluster.

> **Requires Python 3.10+ and pyaedt >= 1.2.0**

## Table of Contents

<!-- toc -->

- [Features](#features)
- [Installation](#installation)
- [Architecture / How it works](#architecture--how-it-works)
- [Usage](#usage)
  * [Configuration file](#configuration-file)
  * [CLI Commands](#cli-commands)
  * [Environment variables](#environment-variables)
  * [Examples](#examples)
    + [Windows - local machine](#windows---local-machine)
    + [Linux - local machine](#linux---local-machine)
    + [Linux - Slurm cluster](#linux---slurm-cluster)
    + [Other schedulers (LSF, SGE/UGE, PBS, Windows HPC)](#other-schedulers-lsf-sgeuge-pbs-windows-hpc)
  * [Viewing the HTML report](#viewing-the-html-report)
- [Supported AEDT versions](#supported-aedt-versions)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Contributors](#contributors)

<!-- tocstop -->

## Features
The current framework provides the following features:
* Compare results of XY plots, mesh statistics and simulation time between two AEDT versions.
* Web page (HTML) output format for visual, interactive comparison.
* JSON file output format to support automated workflows and use of test results downstream.
* Parallel distribution of test projects, with automatic core allocation across available machines.
* Pure gRPC/PyAEDT execution - no AEDT-side scripting, no IronPython shim.
* Cross-platform: identical behavior on Windows and Linux.
* Compatible with running on a local workstation or on a cluster with a scheduler:
  Slurm, LSF, SGE (UGE), PBS, Windows HPC.
* Control of required resources (cores, parametric tasks, single/multi node) per project,
  with optimized distribution/queuing of tasks when resources are limited.
* Automatic generation of reference results (AEDT versions 2024R1+).
* Configurable timeouts (gRPC startup, extraction/solve, shutdown) via environment variables.

## Installation
To install the package use:
```bash
pip install aedttest
```

> **Note:** `pyaedt >= 1.2.0` uses the `ansys.aedt.core` package namespace.
> On Python 3.10, `tomli` is installed automatically as a backport for TOML parsing.
> On Python 3.11+, the built-in `tomllib` module is used instead.

Make sure the AEDT installation you want to test is available on the machine(s) the job runs on,
and that `ANSYSEM_ROOT<version>` (e.g. `ANSYSEM_ROOT251` for 2025 R1) is set to its installation
directory before launching `aedt_test_runner`.

## Architecture / How it works
For every project in the configuration folder, the framework:
1. Allocates cores/machines for the project (`allocator()`), respecting `--max-cores` /
   `--max-projects` and the per-project `distribution` settings.
2. Copies the project (and its dependencies) into a working directory.
3. Starts AEDT as a bare gRPC server: `<ansysedt> -ng -grpcsrv <port>` (plus `-LogFile` on the
   framework side). No `-auto`, `-distributed`, `-machinelist` or `-batchsolve` flags are used,
   since mixing those with a live scripting/gRPC session is not supported by AEDT and can hang
   API calls such as `SetActiveDesign`/`analyze_all`.
4. Waits for the gRPC server to become reachable:
   * On Windows / older Linux: a TCP connect check on the allocated port.
   * On Linux with AEDT 2026 R1+: AEDT switched the gRPC transport to a Unix Domain Socket
     (`~/.conn/AnsysEMUDS-<port>.sock`), so the framework checks for that socket file instead.
5. Runs `simulation_data.py` as a plain CPython subprocess (same interpreter as the framework),
   which connects to the AEDT gRPC session via PyAEDT, opens the project, triggers
   `analyze_setup(cores=, tasks=, ...)` for the requested distribution, and extracts results
   into a project-specific JSON file.
6. Releases/kills the AEDT process and renders the HTML report for that project.

This design removes the previous IronPython `launcher.py` shim entirely - everything after
step 3 is standard CPython + PyAEDT.

## Usage
Electronics Desktop testing framework automatically identifies the environment where it was
launched (local machine vs. cluster scheduler) via `aedttest.clusters.job_hosts`. In this chapter
we show basic examples of starting tests on a local machine (Windows/Linux) or on clusters with a
scheduler. In all scenarios the Command Line Interface (CLI) is used.

### Configuration file
Framework requires a configuration folder as input; every `*.toml` file inside it (recursively)
describes one project. Please see [config_with_comments.toml][1] to understand how to create a
file, or use [config_without_comments.toml][2] as a minimal template.

```toml
[project]
name = "just_winding"
path = "input/just_winding.aedt"

[project.distribution]
cores = 4
distribution_types = ["default"]
parametric_tasks = 1
multilevel_distribution_tasks = 0
single_node = false
auto = true
```

[1]: examples/configs/config_with_comments.toml
[2]: examples/configs/config_without_comments.toml

### CLI Commands
To see all available options:
```bash
aedt_test_runner -h
```

| Argument | Required | Description |
|---|---|---|
| `--aedt-version` | yes | AEDT version to test, e.g. `251` for 2025 R1, `261` for 2026 R1 |
| `--config-folder` | yes | Path to the folder containing the project `.toml` configuration files |
| `--reference-folder` | only if not `--only-reference` | Path to a previously generated reference results folder (`ref_*.json` files) |
| `--only-reference` | no | Only generate reference results, no comparison is performed |
| `--only-validate` | no | Only validate `--config-folder` (and `--reference-folder` if given), then exit |
| `--suppress-validation` | no | Skip configuration/reference validation (**dangerous**, use only if you know what you are doing) |
| `--out-dir`, `-o` | no | Output directory for the report and (if `--save-sim-data`) project files. Defaults to current working directory |
| `--save-sim-data`, `-s` | no | Keep the simulation working directory instead of using a temp folder (requires `--out-dir`) |
| `--max-cores`, `-c` | no | Total core limit across all projects (default: unlimited) |
| `--max-projects`, `-mp` | no | Maximum number of projects run in parallel (default: unlimited) |
| `--debug` | no | Enable DEBUG level logging for the framework and the extraction subprocess |

### Environment variables
The following environment variables let you tune internal timeouts without touching the code:

| Variable | Default | Purpose |
|---|---|---|
| `AEDTTEST_GRPC_TIMEOUT` | `600` (s) | Max time to wait for the AEDT gRPC server (TCP port / UDS socket) to become available |
| `AEDTTEST_EXTRACTION_TIMEOUT` | `21600` (s, 6h) | Max time for the extraction subprocess (open project + solve + extract) to finish |
| `AEDTTEST_SHUTDOWN_TIMEOUT` | `300` (s) | Max time to wait for AEDT to exit after `release_desktop()` before it's killed |

Example (Linux):
```bash
export AEDTTEST_GRPC_TIMEOUT=900
```
Example (Windows PowerShell):
```powershell
$env:AEDTTEST_GRPC_TIMEOUT = "900"
```

### Examples

#### Windows - local machine
Make sure `ANSYSEM_ROOT251` (or the corresponding variable for the version you test) points to
the AEDT installation directory, then from the repository root run:

**Generate only reference results (e.g. AEDT 2025 R1):**
```powershell
aedt_test_runner --config-folder=examples/configs --aedt-version=251 --only-reference --out-dir=out
```

**Run comparison against a previously generated reference (e.g. AEDT 2027 R1 vs 2025 R1 reference):**
```powershell
aedt_test_runner `
    --config-folder=examples/configs `
    --aedt-version=271 `
    --reference-folder="out\results_2026_08_31_10_15_00\reference_folder" `
    --out-dir=out `
    --debug
```

#### Linux - local machine
```bash
source /path/to/setenv_251.sh   # sets ANSYSEM_ROOT251 etc.

aedt_test_runner \
    --config-folder=tests/integration/config \
    --aedt-version=251 \
    --only-reference \
    --out-dir=out \
    --debug
```

```bash
aedt_test_runner \
    --config-folder=tests/integration/config \
    --aedt-version=271 \
    --reference-folder=out/results_2026_08_31_10_15_00/reference_folder \
    --out-dir=out \
    --debug
```

#### Linux - Slurm cluster
**Generate only reference results:**
```bash
sbatch \
  --job-name aedttest \
  --partition ottc01 \
  --export "ALL,ANSYSEM_ROOT251=/apps/software/ANSYS_EM_2025R1/AnsysEM,ANS_NODEPCHECK=1" \
  --nodes 2-2 --ntasks 56 \
  --wrap "aedt_test_runner --config-folder=examples/configs --aedt-version=251 --only-reference --out-dir=/shared/out"
```

**Run comparison between versions:**
```bash
sbatch \
  --job-name aedttest \
  --partition ottc01 \
  --export "ALL,ANSYSEM_ROOT271=/apps/software/ANSYS_EM_2027R1/AnsysEM,ANS_NODEPCHECK=1" \
  --nodes 2-2 --ntasks 56 \
  --wrap "aedt_test_runner --config-folder=examples/configs --aedt-version=271 --reference-folder=/shared/out/results_.../reference_folder --out-dir=/shared/out"
```

Under Slurm, `SLURM_JOB_NODELIST` / `SLURM_TASKS_PER_NODE` (or `SLURM_NTASKS_PER_NODE`) are read
automatically by `aedttest.clusters.job_hosts` to discover the allocated hosts and core counts -
no extra flags are required on top of what `sbatch` already provides.

#### Other schedulers (LSF, SGE/UGE, PBS, Windows HPC)
No extra CLI flags are needed either - the framework detects the scheduler automatically from
the environment variables it sets:

| Scheduler | Detected via |
|---|---|
| SGE / UGE | `PE_HOSTFILE` |
| LSF | `LSB_MCPU_HOSTS` |
| PBS | `PBS_NODEFILE` |
| Slurm | `SLURM_JOB_NODELIST` |
| Windows HPC / Azure Batch | `CCP_NODES` |

If none of these variables are present (plain local run), the framework falls back to the local
hostname and `os.cpu_count()`.

### Viewing the HTML report
Every run creates a timestamped folder `results_YYYY_MM_DD_HH_MM_SS` under `--out-dir` (current
directory by default), containing `main.html`, one `<project>.html` per project, and the
`reference_folder` with the generated `ref_*.json` reference data.

Because the report uses relative CSS/JS assets, opening `main.html` **directly from the
filesystem** (double-click, or `file://...`) works fine in most browsers. If your browser blocks
local assets, or the results are on a remote/cluster filesystem, serve the folder over HTTP
instead:

```bash
cd out/results_2026_08_31_14_16_55
python -m http.server 8000
```
then open `http://localhost:8000/main.html` (or `http://<remote-host>:8000/main.html`, using an
SSH tunnel/port-forward if the results live on a remote or cluster node, e.g.
`ssh -L 8000:localhost:8000 user@cluster-node`).

## Supported AEDT versions
* Version string format: last two digits of the year + release, e.g. `251` = 2025 R1,
  `261` = 2026 R1, `271` = 2027 R1.
* Automatic reference result generation is supported for AEDT **2019 R1** and newer.
* On **Linux with AEDT 2026 R1+**, AEDT exposes its gRPC server over a Unix Domain Socket
  instead of a TCP port; this is handled transparently by the framework.

## Limitations
Currently, the project does not support or only partially supports the following features:
* Automatic results creation is possible only for versions 2019R1+.
* LS-DYNA is not supported.
* Python < 3.10 is not supported.
* Multi-node distribution for a single project is not yet implemented for the gRPC execution
  path (`run_aedt_with_extraction` raises `NotImplementedError` if a project is allocated across
  more than one node) - use `single_node = true` in the project configuration to force
  single-node allocation.

## Troubleshooting
* **`AEDT gRPC server did not start on <host>:<port> within <timeout>s`** - increase
  `AEDTTEST_GRPC_TIMEOUT`, check the AEDT stdout log (`logs/<project>.log.stdout.log`) for
  license or startup errors, and confirm `ANSYSEM_ROOT<version>` is set correctly.
* **`Failed to execute gRPC AEDT command: SetActiveDesign` / access errors on Linux** - usually
  indicates an AEDT/gRPC plugin or shared-library mismatch on the target machine (e.g. a
  `libstdc++`/`GLIBCXX` version too old for the AEDT release) rather than a framework issue;
  verify the AEDT installation and its runtime dependencies on that specific host.
* **`Environment variable ANSYSEM_ROOT<version> is not set`** - export/set that variable to the
  AEDT installation directory before running `aedt_test_runner`.

## Contributors
If you would like to contribute to this project, please see [CONTRIBUTE](docs/CONTRIBUTE.md).
