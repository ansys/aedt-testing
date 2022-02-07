import json
import os
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pytest

from aedttest import aedt_test_runner


def test_allocate_task_single():
    job_machines = aedt_test_runner.get_job_machines("host1:15,host2:10")
    machines_dict = {machine.hostname: machine.cores for machine in job_machines}

    allocated_machines = aedt_test_runner.allocate_task({"cores": 17}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}, "host2": {"cores": 2, "tasks": 1}}

    allocated_machines = aedt_test_runner.allocate_task({"cores": 15}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}}

    allocated_machines = aedt_test_runner.allocate_task({"cores": 2}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 2, "tasks": 1}}

    allocated_machines = aedt_test_runner.allocate_task({"cores": 25}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}, "host2": {"cores": 10, "tasks": 1}}

    allocated_machines = aedt_test_runner.allocate_task({"cores": 26}, machines_dict)
    assert allocated_machines is None

    allocated_machines = aedt_test_runner.allocate_task({"cores": 10, "single_node": True}, machines_dict)
    assert allocated_machines is None


def test_allocate_task_multiple():
    """
    Test all possible scenarios of job splitting. Every test is critical
    """
    job_machines = aedt_test_runner.get_job_machines("host1:20,host2:10")
    machines_dict = {machine.hostname: machine.cores for machine in job_machines}

    allocated_machines = aedt_test_runner.allocate_task({"cores": 16, "parametric_tasks": 2}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 16, "tasks": 2}}

    allocated_machines = aedt_test_runner.allocate_task({"cores": 24, "parametric_tasks": 2}, machines_dict)
    assert not allocated_machines

    allocated_machines = aedt_test_runner.allocate_task({"cores": 10, "parametric_tasks": 2}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 10, "tasks": 2}}

    allocated_machines = aedt_test_runner.allocate_task({"cores": 25, "parametric_tasks": 5}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 20, "tasks": 4}, "host2": {"cores": 5, "tasks": 1}}

    job_machines = aedt_test_runner.get_job_machines("host1:10,host2:15")
    machines_dict = {machine.hostname: machine.cores for machine in job_machines}

    allocated_machines = aedt_test_runner.allocate_task({"cores": 26, "parametric_tasks": 2}, machines_dict)
    assert not allocated_machines

    allocated_machines = aedt_test_runner.allocate_task({"cores": 10, "parametric_tasks": 2}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 10, "tasks": 2}}

    allocated_machines = aedt_test_runner.allocate_task({"cores": 25, "parametric_tasks": 5}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 10, "tasks": 2}, "host2": {"cores": 15, "tasks": 3}}


def test_allocate_task_within_node():
    job_machines = aedt_test_runner.get_job_machines("host1:15,host2:10")
    machines_dict = {machine.hostname: machine.cores for machine in job_machines}

    allocated_machines = aedt_test_runner.allocate_task_within_node({"cores": 17}, machines_dict)
    assert not allocated_machines

    allocated_machines = aedt_test_runner.allocate_task_within_node({"cores": 15}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}}

    allocated_machines = aedt_test_runner.allocate_task_within_node({"cores": 2}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 2, "tasks": 1}}


class TestCopyPathTo:
    def test_copy_path_file_absolute(self):
        with TemporaryDirectory(prefix="src_") as src_tmp_dir:
            file = Path(src_tmp_dir, "tmp_file.txt")
            file_no = Path(src_tmp_dir, "not_copy.txt")

            file.touch()
            file_no.touch()
            with TemporaryDirectory(prefix="dst_") as dst_tmp_dir:
                aedt_test_runner.copy_path_to(str(file), dst_tmp_dir)

                assert Path(dst_tmp_dir, file.name).is_file()
                assert Path(dst_tmp_dir, file.name).exists()
                assert not Path(dst_tmp_dir, file_no.name).exists()

    def test_copy_path_file_relative(self):
        with TemporaryDirectory(prefix="src_", dir=Path.cwd()) as src_tmp_dir:
            # test relative file
            folder_name = Path(src_tmp_dir).name
            file = Path(folder_name) / "tmp_file.txt"
            file_no = Path(folder_name) / "not_copy.txt"

            file.touch()
            file_no.touch()
            with TemporaryDirectory(prefix="dst_") as dst_tmp_dir:
                aedt_test_runner.copy_path_to(str(file), dst_tmp_dir)

                assert (Path(dst_tmp_dir) / file).is_file()
                assert (Path(dst_tmp_dir) / file).exists()
                assert not (Path(dst_tmp_dir) / file_no).exists()

    def test_copy_path_folder_absolute(self):
        with TemporaryDirectory(prefix="src_") as src_tmp_dir:
            folder = Path(src_tmp_dir, "tmp_folder")

            folder.mkdir()
            file = folder / "tmp_file.txt"
            file2 = folder / "tmp_file2.txt"
            file.touch()
            file2.touch()
            with TemporaryDirectory(prefix="dst_") as dst_tmp_dir:
                aedt_test_runner.copy_path_to(str(folder), dst_tmp_dir)

                assert Path(dst_tmp_dir, "tmp_folder", file.name).is_file()
                assert Path(dst_tmp_dir, "tmp_folder", file.name).exists()
                assert Path(dst_tmp_dir, "tmp_folder", file2.name).exists()

    def test_copy_path_folder_relative(self):
        with TemporaryDirectory(prefix="src_", dir=Path.cwd()) as src_tmp_dir:
            folder_name = Path(src_tmp_dir).name
            folder = Path(folder_name) / "tmp_folder"

            folder.mkdir()
            file = folder / "tmp_file.txt"
            file2 = folder / "tmp_file2.txt"
            file.touch()
            file2.touch()
            with TemporaryDirectory(prefix="dst_") as dst_tmp_dir:
                aedt_test_runner.copy_path_to(str(folder), dst_tmp_dir)

                assert (Path(dst_tmp_dir) / file).is_file()
                assert (Path(dst_tmp_dir) / file).exists()
                assert (Path(dst_tmp_dir) / file2).exists()

    def test_no_source(self):
        with pytest.raises(FileExistsError):
            aedt_test_runner.copy_path_to("/no/path/exists", "/tmp")


def test_get_aedt_executable_path():
    with mock.patch.dict(os.environ, {"ANSYSEM_ROOT212": "my/custom/path"}):
        with mock.patch("aedttest.aedt_test_runner.platform.system", return_value="Linux"):
            aedt_path = aedt_test_runner.get_aedt_executable_path("212")
            assert Path(aedt_path) == Path("my/custom/path/ansysedt")

        with mock.patch("aedttest.aedt_test_runner.platform.system", return_value="Windows"):
            aedt_path = aedt_test_runner.get_aedt_executable_path("212")
            assert Path(aedt_path) == Path("my/custom/path/ansysedt.exe")

        with mock.patch("aedttest.aedt_test_runner.platform.system", return_value="MacOS"):
            with pytest.raises(SystemError) as exc:
                aedt_test_runner.get_aedt_executable_path("212")

            assert "Platform is neither Windows nor Linux" in str(exc.value)

    with mock.patch.dict(os.environ, {"ANSYSEM_ROOT212": ""}):
        with pytest.raises(ValueError) as exc:
            aedt_test_runner.get_aedt_executable_path("212")

        assert "Environment variable ANSYSEM_ROOT212" in str(exc.value)


@mock.patch("aedttest.aedt_test_runner.subprocess.check_output", wraps=lambda *a, **kw: b"output")
@mock.patch("aedttest.aedt_test_runner.get_aedt_executable_path", return_value="aedt/install/path")
def test_execute_aedt(mock_aedt_path, mock_call):

    aedt_test_runner.execute_aedt(
        version="212",
        script="my/script/path.py",
        script_args="arg1",
        project_path="custom/pr.aedt",
        machines={"host1": {"cores": 10, "tasks": 2}, "host2": {"cores": 15, "tasks": 3}},
        distribution_config={
            "cores": 2,
            "distribution_types": ["Variations", "Frequencies"],
            "parametric_tasks": 3,
            "multilevel_distribution_tasks": 4,
            "single_node": False,
        },
    )

    assert mock_aedt_path.call_args[0][0] == "212"

    assert mock_call.call_args[0][0] == [
        "aedt/install/path",
        "-machinelist",
        "list=host1:2:10:90%,host2:3:15:90%",
        "-distributed",
        "includetypes=Variations,Frequencies",
        "maxlevels=2",
        "numlevel1=4",
        "-ng",
        "-features=SF6694_NON_GRAPHICAL_COMMAND_EXECUTION",
        "-RunScriptAndExit",
        "my/script/path.py",
        "-ScriptArgs",
        '"arg1"',
        "-LogFile",
        "custom/pr.log",
        "custom/pr.aedt",
    ]


class BaseElectronicsDesktopTester:
    reference_sample = {
        "error_exception": [],
        "aedt_version": "193",
        "projects": {
            "01_voltage_control": {
                "error_exception": [],
                "designs": {
                    "ctrl_prog": {
                        "report": {
                            "Plot_2V2S6O": {
                                "Current(Winding1)": {
                                    "x_name": "Time",
                                    "curves": {
                                        "": {
                                            "y_data": [
                                                1,
                                                0.0416939528629434,
                                            ],
                                            "x_data": [
                                                0,
                                                0.001,
                                            ],
                                        }
                                    },
                                    "y_unit": "A",
                                    "x_unit": "s",
                                },
                            }
                        }
                    }
                },
            }
        },
    }

    config_sample = {
        "just_winding": {
            "distribution": {
                "cores": 2,
                "distribution_types": ["Variations", "Frequencies"],
                "parametric_tasks": 1,
                "multilevel_distribution_tasks": 0,
                "single_node": True,
            },
            "path": "input\\just_winding.aedt",
        },
    }

    def setup(self):
        with TemporaryDirectory() as tmp_dir:
            conf_file = Path(tmp_dir) / "config.json"
            with open(conf_file, "w") as file:
                json.dump(self.config_sample, file)

            ref_file = Path(tmp_dir) / "ref.json"
            with open(ref_file, "w") as file:
                json.dump(self.reference_sample, file)

            self.aedt_tester = aedt_test_runner.ElectronicsDesktopTester(
                version="212",
                max_cores=9999,
                max_tasks=9999,
                config_file=conf_file,
                out_dir=None,
                save_projects=None,
                only_reference=None,
                reference_file=ref_file,
            )


class TestValidateConfig(BaseElectronicsDesktopTester):
    def test_missing_in_config(self):
        with pytest.raises(KeyError) as exc:
            self.aedt_tester.validate_config()

        assert "Following projects defined in reference results: 01_voltage_control," in str(exc.value)

    def test_missing_in_reference(self):
        self.aedt_tester.reference_data["projects"] = {}
        with pytest.raises(KeyError) as exc:
            self.aedt_tester.validate_config()

        assert "Following projects defined in configuration file: just_winding," in str(exc.value)

    def test_missing_projects(self):
        self.aedt_tester.reference_data = {}
        with pytest.raises(KeyError) as exc:
            self.aedt_tester.validate_config()

        assert "'projects' key is not specified in Reference File" in str(exc.value)

    def test_distribution(self):
        config = self.aedt_tester.project_tests_config
        distribution_config = config["just_winding"]["distribution"]

        distribution_config["cores"] = 0
        with pytest.raises(KeyError) as exc:
            self.aedt_tester.validate_config()
        assert "'parametric_tasks' key must be <= 'cores'" in str(exc.value)

        distribution_config["parametric_tasks"] = 1.1
        with pytest.raises(KeyError) as exc:
            self.aedt_tester.validate_config()
        assert "'parametric_tasks' key must be integer" in str(exc.value)

        distribution_config["cores"] = 3
        distribution_config["parametric_tasks"] = 2
        with pytest.raises(KeyError) as exc:
            self.aedt_tester.validate_config()
        assert "'cores' divided by 'parametric_tasks' must be integer" in str(exc.value)

        distribution_config["cores"] = 0
        distribution_config["parametric_tasks"] = 0
        with pytest.raises(KeyError) as exc:
            self.aedt_tester.validate_config()
        assert "'parametric_tasks' key must be >= 1" in str(exc.value)


class TestElectronicsDesktopTester(BaseElectronicsDesktopTester):
    def test_validate_hardware(self):
        self.aedt_tester.machines_dict = {"host1": 1}
        with pytest.raises(ValueError) as exc:
            self.aedt_tester.validate_hardware()

        assert "just_winding requires 2 cores. Not enough resources to run" in str(exc.value)

    @mock.patch("aedttest.aedt_test_runner.time_now", wraps=lambda *a, **kw: "2021-12-31 20:16:04")
    def test_initialize_results(self, time_mock):
        with TemporaryDirectory() as tmp_dir:
            self.aedt_tester.results_path = Path(tmp_dir)
            self.aedt_tester.initialize_results()

            assert self.aedt_tester.report_data == {
                "all_delta": 1,
                "projects": {
                    "just_winding": {
                        "cores": 2,
                        "status": "queued",
                        "link": None,
                        "delta": 0,
                        "time": "2021-12-31 20:16:04",
                    }
                },
            }

    @mock.patch(
        "aedttest.aedt_test_runner.ElectronicsDesktopTester.prepare_project_report",
        wraps=lambda *a, **kw: {"error_exception": [], "slider_limit": 2},
    )
    @mock.patch("aedttest.aedt_test_runner.ElectronicsDesktopTester.render_project_html", wraps=lambda *a, **kw: None)
    @mock.patch("aedttest.aedt_test_runner.ElectronicsDesktopTester.render_main_html", wraps=lambda *a, **kw: None)
    @mock.patch("aedttest.aedt_test_runner.execute_aedt", wraps=lambda *a, **kw: None)
    @mock.patch("aedttest.aedt_test_runner.time_now", wraps=lambda *a, **kw: "2021-12-31 20:16:04")
    def test_task_runner(self, time_mock, aedt_execute_mock, render_main_mock, render_project_mock, prep_proj_mock):
        self.aedt_tester.active_tasks = 5
        self.aedt_tester.machines_dict = {"my_host": 10}
        self.aedt_tester.report_data["projects"] = {"my_proj": {}}

        self.aedt_tester.task_runner("my_proj", "my/path", {"distribution": None}, {"my_host": {"cores": 5}})

        assert self.aedt_tester.report_data == {
            "projects": {
                "my_proj": {"time": "2021-12-31 20:16:04", "status": "success", "link": "my_proj.html", "delta": 2}
            }
        }
        assert self.aedt_tester.active_tasks == 4
        assert self.aedt_tester.machines_dict == {"my_host": 15}
        assert render_main_mock.call_count == 2


class TestCLIArgs:
    def setup(self):
        self.default_argv = ["aedt_test_runner.py", "--aedt-version=212", r"--config-file=file/path"]

    @mock.patch("sys.stderr", new_callable=StringIO)
    def test_version(self, mock_stderr):
        self.default_argv.pop(1)
        with mock.patch("sys.argv", self.default_argv):
            with pytest.raises(SystemExit):
                aedt_test_runner.parse_arguments()
            assert "the following arguments are required: --aedt-version" in mock_stderr.getvalue()

    def test_version_regex(self):
        self.default_argv[1] = "--aedt-version=2021R2"
        self.default_argv += ["--only-reference", "--suppress-validation"]
        with mock.patch("sys.argv", self.default_argv):
            with pytest.raises(ValueError) as exc:
                aedt_test_runner.parse_arguments()
            assert "Electronics Desktop version value is invalid. Valid format example: 221" in str(exc.value)

    @mock.patch("sys.stderr", new_callable=StringIO)
    def test_config_file(self, mock_stderr):
        self.default_argv.pop(2)
        with mock.patch("sys.argv", ["aedt_test_runner.py", "--aedt-version=212"]):
            with pytest.raises(SystemExit):
                aedt_test_runner.parse_arguments()
            assert "the following arguments are required: --config-file" in mock_stderr.getvalue()

    def test_reference(self):
        with mock.patch("sys.argv", self.default_argv):
            with pytest.raises(ValueError) as exc:
                aedt_test_runner.parse_arguments()
            assert "set --only-reference flag or provide path via --reference-file" in str(exc.value)

    def test_validation(self):
        self.default_argv += ["--only-reference", "--only-validate", "--suppress-validation"]
        with mock.patch("sys.argv", self.default_argv):
            with pytest.raises(ValueError) as exc:
                aedt_test_runner.parse_arguments()
            assert "--only-validate and --suppress-validation are mutually exclusive" in str(exc.value)

    def test_config_file_existence(self):
        self.default_argv += ["--only-reference", "--suppress-validation"]
        with mock.patch("sys.argv", self.default_argv):
            with pytest.raises(ValueError) as exc:
                aedt_test_runner.parse_arguments()
            assert "Configuration file does not exist" in str(exc.value)

    def test_sim_data(self):
        self.default_argv += ["--only-reference", "--suppress-validation", "-s"]
        with mock.patch("sys.argv", self.default_argv):
            with mock.patch("aedttest.aedt_test_runner.os.path.isfile", return_value=True):
                with pytest.raises(ValueError) as exc:
                    aedt_test_runner.parse_arguments()
                assert "Saving of simulation data was requested but output directory is not provided" in str(exc.value)


def test_unique_id():
    assert aedt_test_runner.unique_id() == "a1"
    assert aedt_test_runner.unique_id() == "a2"
    assert aedt_test_runner.unique_id() == "a3"


def test_compare_keys():
    dict_ref = {
        "1": 1,
        "2": 2,
        "3": {
            "4nest": 4,
            "5nest": {"6nn": 6},
        },
    }
    dict_now = {
        "1": 1,
        "3": {
            "5nest": {},
        },
    }
    report = []
    aedt_test_runner.compare_keys(dict_ref, dict_now, report, results_type="reference")
    assert report == [
        "Key '2' does not exist in reference results",
        "Key '3->4nest' does not exist in reference results",
        "Key '3->5nest->6nn' does not exist in reference results",
    ]


def test_mkdtemp_persistent_false():
    result = aedt_test_runner.mkdtemp_persistent(persistent=False)
    assert type(result) == TemporaryDirectory

    with aedt_test_runner.mkdtemp_persistent(persistent=False) as tempdir:
        assert Path(tempdir).exists()
    assert not Path(tempdir).exists()


def test_mkdtemp_persistent_true():
    from contextlib import _GeneratorContextManager

    result = aedt_test_runner.mkdtemp_persistent(persistent=True)
    assert type(result) == _GeneratorContextManager

    with aedt_test_runner.mkdtemp_persistent(persistent=True) as tempdir:
        assert Path(tempdir).exists()
    assert Path(tempdir).exists()
    Path(tempdir).rmdir()
