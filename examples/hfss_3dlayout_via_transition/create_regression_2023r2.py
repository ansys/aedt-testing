import sys
from aedttest.aedt_test_runner import main

if __name__ == '__main__':
    sys.argv.extend([r"--config-folder=configs",
                     "--aedt-version=232",
                     r"--reference-folder=results_2023r1_2023_04_19_17_37_02\reference_folder"
                     ])
    main()
