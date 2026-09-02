import os
import socket
import sys
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pytest

from aedttest import aedt_test_runner
from aedttest.aedt_test_runner import LOGFOLDER_PATH

TESTS_DIR = Path(__file__).resolve().parent.parent


def test_allocate_task_multiple():
    """
    Test all possible scenarios of job splitting. Every test is critical
    """
    job_machines = aedt_test_runner.get_job_machines("host1:20,host2:10")
    machines_dict = {machine.hostname: machine.cores for machine in job_machines}
    default = {"single_node": False, "parametric_tasks": 2}

    allocated_machines = aedt_test_runner.allocate_task(dict(default, **{"cores": 16}), machines_dict)
    assert allocated_machines == {"host1": {"cores": 16, "tasks": 2}}

    allocated_machines = aedt_test_runner.allocate_task(dict(default, **{"cores": 24}), machines_dict)
    assert allocated_machines is None

    allocated_machines = aedt_test_runner.allocate_task(dict(default, **{"cores": 10}), machines_dict)
    assert allocated_machines == {"host1": {"cores": 10, "tasks": 2}}

    allocated_machines = aedt_test_runner.allocate_task(
        dict(default, **{"cores": 25, "parametric_tasks": 5}), machines_dict
    )
    assert allocated_machines == {"host1": {"cores": 20, "tasks": 4}, "host2": {"cores": 5, "tasks": 1}}

    job_machines = aedt_test_runner.get_job_machines("host1:10,host2:15")
    machines_dict = {machine.hostname: machine.cores for machine in job_machines}

    allocated_machines = aedt_test_runner.allocate_task(dict(default, **{"cores": 26}), machines_dict)
    assert allocated_machines is None

    allocated_machines = aedt_test_runner.allocate_task(dict(default, **{"cores": 10}), machines_dict)
    assert allocated_machines == {"host1": {"cores": 10, "tasks": 2}}

    allocated_machines = aedt_test_runner.allocate_task(
        dict(default, **{"cores": 25, "parametric_tasks": 5}), machines_dict
    )
    assert allocated_machines == {"host1": {"cores": 10, "tasks": 2}, "host2": {"cores": 15, "tasks": 3}}


def test_allocate_one_task_not_split():
    job_machines = aedt_test_runner.get_job_machines("host1:10,host2:10")
    machines_dict = {machine.hostname: machine.cores for machine in job_machines}
    default = {"single_node": False, "parametric_tasks": 1, "auto": False}

    allocated_machines = aedt_test_runner.allocate_task(dict(default, **{"cores": 12}), machines_dict)
    assert allocated_machines is None


def test_allocate_one_task_split_if_auto():
    job_machines = aedt_test_runner.get_job_machines("host1:10,host2:10")
    machines_dict = {machine.hostname: machine.cores for machine in job_machines}
    default = {"single_node": False, "parametric_tasks": 1, "auto": True}

    allocated_machines = aedt_test_runner.allocate_task(dict(default, **{"cores": 12}), machines_dict)
    assert allocated_machines == {"host1": {"cores": 10, "tasks": 1}, "host2": {"cores": 2, "tasks": 1}}


def test_allocate_task_within_node():
    default = {"single_node": False, "parametric_tasks": 1}

    job_machines = aedt_test_runner.get_job_machines("host1:15,host2:10")
    machines_dict = {machine.hostname: machine.cores for machine in job_machines}

    allocated_machines = aedt_test_runner.allocate_task_within_node(dict(default, **{"cores": 17}), machines_dict)
    assert not allocated_machines

    allocated_machines = aedt_test_runner.allocate_task_within_node(dict(default, **{"cores": 15}), machines_dict)
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}}

    allocated_machines = aedt_test_runner.allocate_task_within_node(dict(default, **{"cores": 2}), machines_dict)
    assert allocated_machines == {"host1": {"cores": 2, "tasks": 1}}


def test_allocator():
    aedt_tester = aedt_test_runner.ElectronicsDesktopTester(
        version="212",
        max_cores=9999,
        max_parallel_projects=9999,
        config_folder=TESTS_DIR / "input" / "configs",
        out_dir=None,
        save_projects=None,
        only_reference=True,
        reference_folder=None,
    )
    job_machines = aedt_test_runner.get_job_machines("host1:28,host2:28,host3:28")
    aedt_tester.machines_dict = {machine.hostname: machine.cores for machine in job_machines}
    allocated = [(project_name, allocated_machines) for project_name, allocated_machines in aedt_tester.allocator()]
    assert ("just_winding", {"host1": {"cores": 28, "tasks": 1}}) == allocated.pop(0)
    assert ("expression_excitation", {"host2": {"cores": 20, "tasks": 1}}) == allocated.pop(0)
    assert ("19", {"host3": {"cores": 12, "tasks": 6}}) == allocated.pop(0)
    assert ("01_voltage_control", {"host3": {"cores": 10, "tasks": 1}}) == allocated.pop(0)
    assert ("2019R1", {"host2": {"cores": 4, "tasks": 2}}) == allocated.pop(0)


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


@mock.patch("aedttest.aedt_test_runner.subprocess.Popen", wraps=lambda *a, **kw: mock.MagicMock())
@mock.patch("aedttest.aedt_test_runner.get_aedt_executable_path", return_value="aedt/install/path")
def test_execute_aedt(mock_aedt_path, mock_popen):
    """AEDT is started bare and non-blocking as a gRPC server.

    No -auto/-distributed/-machinelist/-RunScriptAndExit are passed: those
    batch-solve flags are not compatible with a live scripting/gRPC session
    (confirmed to hang SetActiveDesign/analyze_all). Distribution is
    configured later from simulation_data.py via analyze_setup(cores=, tasks=).
    """
    process = aedt_test_runner.execute_aedt(
        version="212",
        grpc_port=50051,
        project_log_path=str(LOGFOLDER_PATH / "pr.log"),
    )

    assert process.stdout_log_path == str(LOGFOLDER_PATH / "pr.log") + ".stdout.log"
    assert mock_aedt_path.call_args[0][0] == "212"
    assert mock_popen.call_args[0][0] == [
        "aedt/install/path",
        "-ng",
        "-grpcsrv",
        "50051",
        "-LogFile",
        str(LOGFOLDER_PATH / "pr.log"),
    ]
    # no batch-solve / IronPython-shim flags whatsoever
    for forbidden in ("-auto", "-distributed", "-machinelist", "-RunScriptAndExit", "-ScriptArgs"):
        assert forbidden not in mock_popen.call_args[0][0]


def test_aedt_version_to_pyaedt():
    assert aedt_test_runner.aedt_version_to_pyaedt("212") == "2021.2"
    assert aedt_test_runner.aedt_version_to_pyaedt("251") == "2025.1"
    assert aedt_test_runner.aedt_version_to_pyaedt("2611") == "2026.11"


def test_wait_for_grpc_server_process_died(tmp_path):
    stdout_log_path = tmp_path / "aedt.log.stdout.log"
    stdout_log_path.write_bytes(b"crash details")

    process = mock.MagicMock()
    process.poll.return_value = 1
    process.returncode = 1
    process.stdout_log_path = str(stdout_log_path)

    with pytest.raises(OSError) as exc:
        aedt_test_runner.wait_for_grpc_server("localhost", 50051, process)

    assert "terminated before the gRPC server was available" in str(exc.value)
    assert "crash details" in str(exc.value)


def test_wait_for_grpc_server_timeout():
    process = mock.MagicMock()
    process.poll.return_value = None

    with mock.patch("aedttest.aedt_test_runner.sleep"):
        with pytest.raises(OSError) as exc:
            # port 0 is never connectable, timeout=0 exits the loop immediately
            aedt_test_runner.wait_for_grpc_server("localhost", 0, process, timeout=0)

    assert "did not start" in str(exc.value)
    assert process.kill.called


def test_wait_for_grpc_server_success():
    """Open a real socket so that the connect probe succeeds."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        process = mock.MagicMock()
        process.poll.return_value = None

        aedt_test_runner.wait_for_grpc_server("localhost", port, process)


def test_wait_for_grpc_server_uds_success(tmp_path):
    """On Linux, AEDT may expose the gRPC server via a Unix Domain Socket file
    instead of a TCP port; wait_for_grpc_server must detect that too."""
    port = 39589
    uds_socket = tmp_path / f"AnsysEMUDS-{port}.sock"
    uds_socket.write_text("")  # simulate the socket file being created by AEDT

    process = mock.MagicMock()
    process.poll.return_value = None

    with mock.patch("aedttest.aedt_test_runner.os.name", "posix"), mock.patch(
        "aedttest.aedt_test_runner.os.path.expanduser", return_value=str(uds_socket)
    ):
        aedt_test_runner.wait_for_grpc_server("localhost", port, process, timeout=5)


@mock.patch("aedttest.aedt_test_runner.subprocess.run")
@mock.patch("aedttest.aedt_test_runner.wait_for_grpc_server")
@mock.patch("aedttest.aedt_test_runner.execute_aedt")
@mock.patch("aedttest.aedt_test_runner.find_free_port", return_value=50051)
def test_run_aedt_with_extraction(mock_port, mock_execute, mock_wait, mock_run):
    process = mock.MagicMock()
    mock_execute.return_value = process

    aedt_test_runner.run_aedt_with_extraction(
        version="251",
        machines={"localhost": {"cores": 4, "tasks": 2}},
        distribution_config={
            "auto": True,
            "parametric_tasks": 2,
            "distribution_types": ["Variations", "Frequencies"],
        },
        project_path="my/proj.aedt",
        extraction_script="my/simulation_data.py",
        log_file="my/log.log",
        debug=True,
    )

    assert mock_execute.call_args[1]["grpc_port"] == 50051
    assert mock_run.call_args[0][0] == [
        sys.executable,
        "my/simulation_data.py",
        "--logfile-path",
        "my/log.log",
        "--project-path",
        "my/proj.aedt",
        "--port",
        "50051",
        "--machine",
        "localhost",
        "--aedt-version",
        "2025.1",
        "--cores",
        "4",
        "--tasks",
        "2",
        "--num-variations",
        "2",
        "--distribution-types",
        "Variations,Frequencies",
        "--use-auto-settings",
        "--debug",
    ]
    assert mock_run.call_args[1]["timeout"] == aedt_test_runner.EXTRACTION_TIMEOUT
    assert mock_run.call_args[1]["check"] is True
    assert process.wait.called


@mock.patch("aedttest.aedt_test_runner.subprocess.run")
@mock.patch("aedttest.aedt_test_runner.wait_for_grpc_server")
@mock.patch("aedttest.aedt_test_runner.execute_aedt")
@mock.patch("aedttest.aedt_test_runner.find_free_port", return_value=50051)
def test_run_aedt_with_extraction_timeout(mock_port, mock_execute, mock_wait, mock_run):
    """A hung extraction (e.g. no active design) must not block the framework forever."""
    process = mock.MagicMock()
    mock_execute.return_value = process
    mock_run.side_effect = aedt_test_runner.subprocess.TimeoutExpired("python", 1)

    with pytest.raises(OSError) as exc:
        aedt_test_runner.run_aedt_with_extraction(
            version="251",
            machines={"localhost": {"cores": 4, "tasks": 1}},
            distribution_config={"auto": True, "parametric_tasks": 1},
            project_path="my/proj.aedt",
            extraction_script="my/simulation_data.py",
            log_file="my/log.log",
        )

    assert "did not finish within" in str(exc.value)
    # AEDT must still be reaped even though extraction hung
    assert process.wait.called


@mock.patch("aedttest.aedt_test_runner.subprocess.run")
@mock.patch("aedttest.aedt_test_runner.wait_for_grpc_server")
@mock.patch("aedttest.aedt_test_runner.execute_aedt")
@mock.patch("aedttest.aedt_test_runner.find_free_port", return_value=50051)
def test_run_aedt_with_extraction_kills_hanging_desktop(mock_port, mock_execute, mock_wait, mock_run):
    process = mock.MagicMock()
    process.wait.side_effect = [aedt_test_runner.subprocess.TimeoutExpired("aedt", 1), 0]
    mock_execute.return_value = process

    aedt_test_runner.run_aedt_with_extraction(
        version="251",
        machines={"localhost": {"cores": 4, "tasks": 1}},
        distribution_config={"auto": True, "parametric_tasks": 1},
        project_path="my/proj.aedt",
        extraction_script="my/simulation_data.py",
        log_file="my/log.log",
    )

    assert process.kill.called


class BaseElectronicsDesktopTester:
    def setup_method(self):
        self.aedt_tester = aedt_test_runner.ElectronicsDesktopTester(
            version="212",
            max_cores=9999,
            max_parallel_projects=9999,
            config_folder=TESTS_DIR / "input" / "config_simple",
            out_dir=None,
            save_projects=None,
            only_reference=None,
            reference_folder=TESTS_DIR / "input" / "reference_simple",
        )


class TestValidateConfig(BaseElectronicsDesktopTester):
    def test_missing_in_config(self):
        with pytest.raises(KeyError) as exc:
            self.aedt_tester.validate_config()

        assert "Following projects defined in reference results: 01_voltage_control," in str(exc.value)

    def test_missing_in_reference(self):
        self.aedt_tester.reference_data = {}
        with pytest.raises(KeyError) as exc:
            self.aedt_tester.validate_config()

        assert "Following projects defined in configuration file: just_winding," in str(exc.value)

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
            self.aedt_tester.reference_folder = Path(tmp_dir) / "1"
            self.aedt_tester.reference_profiles = self.aedt_tester.reference_folder / "profiles"
            self.aedt_tester.initialize_results()

            assert self.aedt_tester.report_data == {
                "all_delta": 1,
                "projects": {
                    "just_winding": {
                        "avg": 0,
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
        wraps=lambda *a, **kw: {"error_exception": [], "slider_limit": 2, "max_avg": 3},
    )
    @mock.patch("aedttest.aedt_test_runner.ElectronicsDesktopTester.render_project_html", wraps=lambda *a, **kw: None)
    @mock.patch("aedttest.aedt_test_runner.ElectronicsDesktopTester.render_main_html", wraps=lambda *a, **kw: None)
    @mock.patch("aedttest.aedt_test_runner.run_aedt_with_extraction", wraps=lambda *a, **kw: None)
    @mock.patch("aedttest.aedt_test_runner.time_now", wraps=lambda *a, **kw: "2021-12-31 20:16:04")
    def test_task_runner(self, time_mock, aedt_execute_mock, render_main_mock, render_project_mock, prep_proj_mock):
        self.aedt_tester.active_tasks = 5
        self.aedt_tester.machines_dict = {"my_host": 10}
        self.aedt_tester.report_data["projects"] = {"my_proj": {}}

        self.aedt_tester.task_runner("my_proj", "my/path", {"distribution": None}, {"my_host": {"cores": 5}})

        assert self.aedt_tester.report_data == {
            "projects": {
                "my_proj": {
                    "time": "2021-12-31 20:16:04",
                    "status": "success",
                    "link": "my_proj.html",
                    "delta": 2,
                    "avg": 3,
                }
            }
        }
        assert self.aedt_tester.active_tasks == 4
        assert self.aedt_tester.machines_dict == {"my_host": 15}
        assert render_main_mock.call_count == 2


class TestCLIArgs:
    def setup_method(self):
        self.default_argv = ["aedt_test_runner.py", "--aedt-version=212", r"--config-folder=file/path"]

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
            assert "the following arguments are required: --config-folder" in mock_stderr.getvalue()

    def test_reference(self):
        with mock.patch("sys.argv", self.default_argv):
            with pytest.raises(ValueError) as exc:
                aedt_test_runner.parse_arguments()
            assert "set --only-reference flag or provide path via --reference-folder" in str(exc.value)

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
            assert "Configuration folder does not exist" in str(exc.value)

    def test_sim_data(self):
        self.default_argv += ["--only-reference", "--suppress-validation", "-s"]
        with mock.patch("sys.argv", self.default_argv):
            with mock.patch("aedttest.aedt_test_runner.Path.is_dir", return_value=True):
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
    aedt_test_runner.compare_keys(dict_ref, dict_now, report, results_type="current")
    assert report == [
        "Key '2' is missing from current results",
        "Key '3->4nest' is missing from current results",
        "Key '3->5nest->6nn' is missing from current results",
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
