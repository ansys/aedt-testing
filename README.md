## Description
Current project aims to provide an Automated Framework to test Ansys Electronics Desktop (AEDT). User can set up a 
sweet of tests to validate stability/regression of results between different versions of Electronics Desktop 


## Table of Contents

<!-- toc -->

- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
  * [Configuration file](#configuration-file)
  * [Local machine](#local-machine)
  * [Slurm](#slurm)
- [Limitations](#limitations)

<!-- tocstop -->

## Features
Current framework has the following features:
* Compare results of XY plots, mesh statistics and simulation time.
* Web page output format for visual comparison
* JSON file output format for automated workflows
* Parallel distribution of test projects
* Cross-platform: supports Windows and Linux
* Compatibility with local machine and most known cluster schedulers: LSF, SGE, Slurm, PBS, Windows HPC
* Control of required resources for each project and optimized distribution of tasks
* Automatic generation of reference results (AEDT versions 2019R1+)

## Installation
To install the package use:
```bash
pip install .
```

## Usage
Electronics Desktop testing framework automatically identifies environment where it was launched. In this chapter we 
will show basic examples of starting tests on local machine or on clusters with scheduler. In all scenarios we use CLI.

### Configuration file
Framework requires configuration file as input. Please read [configuration.md](docs/configuration.md) to understand how 
to create a file.

### Local machine
To start test on local machine use following command line
```bash
python aedttest/aedt_test_runner.py --aedt-version=212 --config-file=C:\git\aedt-testing\examples\example_config.json
```

### Slurm
```bash
sbatch \
    --job-name aedttest \
    --partition ottc02 \
    --export "ALL,ANSYSEM_ROOT212=/ott/apps/software/ANSYS_EM_2021R2/AnsysEM21.2/Linux64,ANS_NODEPCHECK=1" \
    --nodes 12-12 \
    --wrap "python3 aedttest/aedt_test_runner.py -cf=/lus01/mbeliaev/aedt-test/examples/example_config.json -av=212"
```

## Limitations
Currently, project does not support or partially supports following features:
* Automatic results creation is possible only for versions 2019R1+
* LS-DSO is not supported