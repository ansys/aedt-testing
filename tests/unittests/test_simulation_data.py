import copy
import os
from argparse import Namespace

try:
    from unittest import mock
except ImportError:
    # py27
    import mock

with mock.patch("argparse.ArgumentParser.parse_args", return_value=Namespace(desktop_version="2021.1")):
    from aedttest import simulation_data

TESTS_DIR = os.path.dirname(os.path.dirname(__file__))


class BaseTest:
    def teardown(self):
        simulation_data.PROJECT_DICT = {"error_exception": [], "designs": {}}


class TestParse(BaseTest):
    def test_parse_variation_string(self):
        result = simulation_data.parse_value_with_unit("abc")
        assert result == ("abc", "")

        result = simulation_data.parse_value_with_unit("")
        assert result == ("", "")

        result = simulation_data.parse_value_with_unit("3m2")
        assert result == ("3", "m2")

        result = simulation_data.parse_value_with_unit("3.12345678901234mH")
        assert result == ("3.123456789e+00", "mH")

        result = simulation_data.parse_value_with_unit("3.1234567891e-9mH")
        assert result == ("3.123456789e-09", "mH")

        result = simulation_data.parse_value_with_unit("3.1234567891e-111mH")
        assert result == ("3.123456789e-111", "mH")

        result = simulation_data.parse_value_with_unit("3")
        assert result == ("3", "")

        result = simulation_data.parse_value_with_unit("3.0")
        assert result == ("3.0", "")

    @mock.patch("aedttest.simulation_data.parse_profile_file", return_value="10:00:00")
    @mock.patch("aedttest.simulation_data.parse_mesh_stats", return_value=100)
    def test_extract_design_data(self, mock_parse_mesh, mock_parse_profile_file):
        mock_pyaedt_app = mock.Mock()
        mock_pyaedt_app.available_variations.get_variation_strings.return_value = ["Ia='30'A", "Ia='20'A"]
        mock_pyaedt_app.export_mesh_stats.return_value = None
        mock_pyaedt_app.export_profile.return_value = None

        result_dict = simulation_data.extract_design_data(
            app=mock_pyaedt_app,
            design_name="only_winding2",
            setup_dict={"Setup1": "Setup1 : LastAdaptive"},
            project_dir="/tmp",
            design_dict={"only_winding2": {"mesh": {}, "simulation_time": {}, "report": {}}},
        )

        assert result_dict == {
            "only_winding2": {
                "mesh": {"Ia=30A": {"Setup1": 100}, "Ia=20A": {"Setup1": 100}},
                "simulation_time": {"Ia=30A": {"Setup1": "10:00:00"}, "Ia=20A": {"Setup1": "10:00:00"}},
                "report": {},
            }
        }

    def test_parse_profile_2020r2(self):

        result = simulation_data.parse_profile_file(
            profile_file=os.path.join(TESTS_DIR, "input", "2020R2_profile.prof"),
            design_name="test_design",
            variation="test_variation",
            setup_name="test_setup",
        )
        assert result == "00:00:09"

    def test_parse_profile_2021r2(self):
        result = simulation_data.parse_profile_file(
            profile_file=os.path.join(TESTS_DIR, "input", "2021R2_profile.prof"),
            design_name="test_design",
            variation="test_variation",
            setup_name="test_setup",
        )
        assert result == "00:00:05"

    def test_parse_profile_2019r1(self):
        result = simulation_data.parse_profile_file(
            profile_file=os.path.join(TESTS_DIR, "input", "R2019R1_profile.prof"),
            design_name="test_design",
            variation="test_variation",
            setup_name="test_setup",
        )
        assert result == "00:00:02"

    def test_parse_mesh_stats_no_mesh(self):

        result = simulation_data.parse_mesh_stats(
            mesh_stats_file=os.path.join(TESTS_DIR, "input", "no_mesh.mstat"),
            design_name="only_winding2",
            variation="n_parallel='2' winding_current='15mA'",
            setup_name="Setup1",
        )
        assert result is None
        assert simulation_data.PROJECT_DICT == {
            "error_exception": [
                "Design:only_winding2 Variation: n_parallel='2' winding_current='15mA' Setup: Setup1 has no mesh stats"
            ],
            "designs": {},
        }

    def test_parse_mesh_stats(self):
        result = simulation_data.parse_mesh_stats(
            mesh_stats_file=os.path.join(TESTS_DIR, "input", "mesh.mstat"),
            design_name="test_design",
            variation="test_variation",
            setup_name="test_setup",
        )
        assert result == 44


class TestCheck(BaseTest):
    def setup(self):
        # output of pyaedt parse_rdat_file
        self.input_dat_dict = {
            "L Plot 1": {
                "Matrix1.L(Winding1,Winding1)": {
                    "x_name": "Freq",
                    "x_unit": "Hz",
                    "y_unit": "H",
                    "curves": {
                        "n_parallel=1 winding_current=5.123456789e-06mA": {
                            "x_data": [10, 60, 62.1052631578947, 1000, 250750, 500500, 750250, 1000000],
                            "y_data": [
                                4.11363150893661e-07,
                                4.11363150893661e-07,
                                4.11363150893661e-07,
                                4.11363150893661e-07,
                                4.11363150900861e-07,
                                4.11363150922348e-07,
                                4.11363150958121e-07,
                                4.11363151008181e-07,
                            ],
                        },
                        "n_parallel=1 winding_current=1.123456789123456e-06mA": {
                            "x_data": [10, 60, 62.1052631578947, 1000, 250750, 500500, 750250, 1000000],
                            "y_data": [
                                4.11363150893661e-07,
                                4.11363150893661e-07,
                                4.11363150893661e-07,
                                4.11363150893661e-07,
                                4.11363150900861e-07,
                                4.11363150922348e-07,
                                4.11363150958121e-07,
                                4.11363151008181e-07,
                            ],
                        },
                        "": {
                            "x_data": [10, 60, 62.1052631578947, 1000, 250750, 500500, 750250, 1000000],
                            "y_data": [
                                1.02840787723415e-07,
                                1.02840787723415e-07,
                                1.02840787723415e-07,
                                1.02840787723415e-07,
                                1.02840787725215e-07,
                                1.02840787730587e-07,
                                1.0284078773953e-07,
                                1.02840787752045e-07,
                            ],
                        },
                    },
                }
            }
        }

    def test_check_without_nan(self):
        ref_dict = copy.deepcopy(self.input_dat_dict)
        result = simulation_data.check_nan(self.input_dat_dict)
        assert result is not ref_dict
        assert result == ref_dict

    def test_check_with_nan(self):
        """
        insert nan in one curve, check the curve with nan is removed
        """
        self.input_dat_dict["L Plot 1"]["Matrix1.L(Winding1,Winding1)"]["curves"][
            "n_parallel=1 winding_current=5.123456789e-06mA"
        ]["y_data"][3] = "nan"

        ref_dict = copy.deepcopy(self.input_dat_dict)
        ref_dict["L Plot 1"]["Matrix1.L(Winding1,Winding1)"]["curves"].pop(
            "n_parallel=1 winding_current=5.123456789e-06mA"
        )

        result = simulation_data.check_nan(self.input_dat_dict)
        assert result is not ref_dict
        assert result == ref_dict

    def test_compose_curve_key(self):
        ref_keys = [
            "n_parallel=1 winding_current=1.123456789e-06mA",
            "n_parallel=1 winding_current=5.123456789e-06mA",
            "nominal",
        ]
        result = simulation_data.compose_curve_keys(self.input_dat_dict)
        result_keys = list(result["L Plot 1"]["Matrix1.L(Winding1,Winding1)"]["curves"].keys())
        result_keys.sort()
        assert ref_keys == result_keys
