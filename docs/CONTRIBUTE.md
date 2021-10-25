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