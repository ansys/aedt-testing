## Table of Contents

<!-- toc -->

- [Configure your enviornment](#configure-your-enviornment)
- [Validate types](#validate-types)

<!-- tocstop -->

## Configure your enviornment
Install all dependencies
```bash
pip install -r requirements-dev.txt
```

Install pre-commit
```bash
pre-commit install
```

## Validate types
```bash
mypy --config-file=./mypy.ini unittests\test_aedttest.py
```