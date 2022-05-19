## Table of Contents

<!-- toc -->

- [Configure your enviornment](#configure-your-enviornment)
- [Validate types](#validate-types)
- [Build package](#build-package)

<!-- tocstop -->

## Configure your enviornment
Install all dependencies
```bash
pip install .[test]
```

Install pre-commit
```bash
pre-commit install
```

## Validate types
```bash
mypy --config-file=./mypy.ini -p aedttest
```


## Build package
package is built using:
```bash
flit build
```