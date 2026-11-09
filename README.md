[![](https://img.shields.io/pypi/v/aedttest.svg)](https://pypi.python.org/pypi/aedttest/)
[![](https://img.shields.io/pypi/pyversions/aedttest.svg)](https://pypi.python.org/pypi/aedttest/)

## Description
This project provides an automated framework to test Ansys Electronics Desktop (AEDT).
Users can set up a suite of projects to validate stability/regression of simulation results
(XY plots, mesh statistics, simulation time) between different versions of AEDT, or simply
generate a reference baseline for a given version.

The framework submits each project as a batch job on a cluster scheduler (currently Slurm),
waits for the solve to finish, and then extracts the results with **PyAEDT** (CPython, gRPC).
No AEDT-side scripts (`.py`/IronPython) are used.

> **Requires Python 3.10+ and pyaedt >= 1.0.0**

## Table of Contents

<!-- toc -->

- [Features](#features)
- [Installation](#installation)
- [Architecture / How It Works](#architecture--how-it-works)
- [Usage](#usage)
  * [Configuration file](#configuration-file)
  * [Quick start: one-command pipeline (Linux / Slurm)](#quick-start-one-command-pipeline-linux--slurm)
  * [CLI Commands](#cli-commands)
  * [Environment variables](#environment-variables)
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
* Parallel, throttled submission of test projects as cluster jobs, with per-project core and
  node settings and optional test matrices.
* Pure PyAEDT/CPython extraction - no AEDT-side scripting, no IronPython shim.
* Scheduler adapter: Slurm is implemented (LSF and PBS are planned).

## Installation
To install the package use:
```bash
pip install aedttest
```

> **Note:** `pyaedt >= 1.0.0` uses the `ansys.aedt.core` package namespace.
> On Python 3.10, `tomli` is installed automatically as a backport for TOML parsing.
> On Python 3.11+, the built-in `tomllib` module is used instead.

Make sure the AEDT installation you want to test is available on the cluster nodes and that
`root` in `site.toml` points to it (see [Quick start](#quick-start-one-command-pipeline-linux--slurm)).

## Architecture / How It Works

For every AEDT version under test, `aedt_test_pipeline`:

1. Reads the site settings (`site.toml`), sets up the AEDT environment for that version and
   expands the project configurations (including test matrices).
2. Copies each project into an isolated run folder.
3. **Submit:** submits one batch job per project through the scheduler adapter. The job starts
   AEDT in batch mode and solves the project. Submission is throttled by `max_parallel_jobs`.
4. **Poll:** polls the scheduler until every job reaches a terminal state.
5. **Collect:** for each successfully finished job, starts `simulation_data.py` as a CPython
   process. It connects to AEDT through PyAEDT (gRPC), opens the solved project and extracts
   the results to JSON. It does not solve again.
6. **Report:** compares the results with the reference baseline and writes the HTML report.

Only submit and poll are scheduler specific (`aedttest/clusters/scheduler.py`; the Slurm
implementation is in `slurm_scheduler.py`). Everything else is scheduler independent. All
framework code is plain CPython with PyAEDT; no IronPython scripts run inside AEDT.

## Usage
In this chapter we show how to run the pipeline with the Command Line Interface (CLI).

### Configuration file
Framework requires a configuration folder as input; every `*.toml` file inside it (recursively)
describes one project. Please see [config_with_comments.toml][1] to understand how to create a
file, or use [config_without_comments.toml][2] as a minimal template.

```toml
[project]
name = "5G_waveguide_4cores"
path = "tests/integration/projects/5G_waveguide_4cores.aedt"

[project.distribution]
cores = 4
parametric_tasks = 1
single_node = true
auto = false
distribution_types = ["Variations", "Frequencies"]
```

[1]: examples/configs/config_with_comments.toml
[2]: examples/configs/config_without_comments.toml

### Quick start: one-command pipeline (Linux / Slurm)
A single command runs the full cycle: submit, poll, collect, report.

- **Submit and poll:** each project is submitted as a batch job that solves it on the cluster.
  This goes through the scheduler adapter.
- **Collect:** once a job has finished successfully, `simulation_data.py` (CPython + PyAEDT)
  opens the solved project, extracts the results and builds the report. It does not solve again.

```bash
aedt_test_pipeline --site site.toml \
  --reference-version 242 --test-version 261 \
  --config-folder ./examples/configs
```

1. Runs `--reference-version` and creates a fresh reference baseline.
2. Runs `--test-version` and compares it against that baseline.
3. Writes the HTML report (`main.html`) under the output directory.

Omit `--test-version` to only create or refresh a baseline.

`site.toml` holds the cluster defaults: `working_dir` (required), one
`[site.aedt_versions.<version>]` table per version (`root`, and an optional `env`), and
optionally `out_dir`, `reference_folder`, `poll_interval`, `poll_timeout`, `max_parallel_jobs`,
`[site.scheduler]` and `[site.slurm]` defaults. Per-project TOML files take precedence over `site.toml`.

For each version the pipeline clears stale `ANSYSEM_ROOT*` and `ANSYS_DESKTOP*` variables from
the shell, then sets `ANSYSEM_ROOT<version>` (and, on Linux, `LD_LIBRARY_PATH`, which newer AEDT
builds need for PyAEDT collection) to the version's `root`. The optional `env` table overrides
these defaults or adds more variables. A warning is logged if `env` sets another version's
`ANSYSEM_ROOT`. See `examples/site.example.toml`.

Job submission and polling go through a scheduler adapter selected by `[site.scheduler] type`
(currently only `slurm`). The rest of the pipeline is scheduler independent.

The pipeline always expands test matrices (list-valued `cores`, `nodes`, etc.) and runs each
project in an isolated copy. The individual steps are also available separately:
`aedt_test_submit` (supports `--enable-matrix`, `--max-parallel`, `--in-place`),
`aedt_test_collect` and `aedt_test_report`.

### CLI Commands
To see all available options:
```bash
aedt_test_pipeline -h
```

| Argument | Required | Description |
|---|---|---|
| `--site` | yes | Path to `site.toml` |
| `--reference-version` | yes | AEDT version used to create the reference baseline, e.g. `242` |
| `--test-version` | no | AEDT version to run and compare against the baseline, e.g. `271` |
| `--config-folder` | yes | Path to the folder containing the project `.toml` configuration files |
| `--save-sim-data`, `-s` | no | Keep the simulation data under the output directory |
| `--debug` | no | Enable DEBUG level logging |

### Environment variables
The following environment variables let you tune internal timeouts without touching the code:

| Variable | Default | Purpose |
|---|---|---|
| `AEDTTEST_GRPC_TIMEOUT` | `600` (s) | Max time to wait for the AEDT gRPC server (TCP port / UDS socket) to become available |
| `AEDTTEST_EXTRACTION_TIMEOUT` | `21600` (s, 6h) | Max time for the extraction subprocess (open project + extract) to finish |
| `AEDTTEST_SHUTDOWN_TIMEOUT` | `300` (s) | Max time to wait for AEDT to exit after `release_desktop()` before it's killed |

Example (Linux):
```bash
export AEDTTEST_GRPC_TIMEOUT=900
```
Example (Windows PowerShell):
```powershell
$env:AEDTTEST_GRPC_TIMEOUT = "900"
```

### Viewing the HTML report
Every run creates a timestamped folder `results_YYYY_MM_DD_HH_MM_SS` under the output directory
(`out_dir` in `site.toml`), containing `main.html`, one `<project>.html` per project, and the
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
* Automatic reference result generation is supported for AEDT **2024 R1** and newer.
* On **Linux with AEDT 2026 R1+**, AEDT exposes its gRPC server over a Unix Domain Socket
  instead of a TCP port; this is handled transparently by the framework.

## Limitations
Currently, the project does not support or only partially supports the following features:
* Automatic results creation is possible only for versions 2024 R1+.
* LS-DYNA is not supported.
* Python < 3.10 is not supported.
* Only the Slurm scheduler is implemented for `aedt_test_pipeline`.
* A single AEDT project cannot currently span multiple nodes in the extraction step; use
  `single_node = true` in the project configuration.

## Troubleshooting
* **`AEDT gRPC server did not start on <host>:<port> within <timeout>s`** - increase
  `AEDTTEST_GRPC_TIMEOUT`, check the AEDT stdout log (`logs/<project>.log.stdout.log`) for
  license or startup errors, and confirm `root` in `site.toml` is correct.
* **`Failed to execute gRPC AEDT command: SetActiveDesign` / access errors on Linux** - usually
  indicates an AEDT/gRPC plugin or shared-library mismatch on the target machine (e.g. a
  `libstdc++`/`GLIBCXX` version too old for the AEDT release) rather than a framework issue;
  verify the AEDT installation and its runtime dependencies on that specific host.
* **`Environment variable ANSYSEM_ROOT<version> is not set`** - add a
  `[site.aedt_versions.<version>]` table with `root` to `site.toml`.

## Contributors
If you would like to contribute to this project, please see [CONTRIBUTE](docs/CONTRIBUTE.md).
