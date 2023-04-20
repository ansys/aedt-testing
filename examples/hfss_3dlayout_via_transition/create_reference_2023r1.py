import sys
from aedttest.aedt_test_runner import main

if __name__ == '__main__':
    sys.argv.extend([r"--config-folder=configs",
                     "--aedt-version=231",
                     "--only-reference"
                     ])
    main()
