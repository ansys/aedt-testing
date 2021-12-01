from argparse import Namespace
from unittest import mock

with mock.patch("argparse.ArgumentParser.parse_args", return_value=Namespace(desktop_version="2021.1")):
    from aedttest import simulation_data


def test_parse_variation_string():
    result = simulation_data.parse_variation_string("abc")
    assert result == ("abc", "")

    result = simulation_data.parse_variation_string("")
    assert result == ("", "")

    result = simulation_data.parse_variation_string("3m2")
    assert result == ("3", "m2")

    result = simulation_data.parse_variation_string("3.12345678901234mH")
    assert result == ("3.123456789e+00", "mH")

    result = simulation_data.parse_variation_string("3.1234567891e-9mH")
    assert result == ("3.123456789e-09", "mH")

    result = simulation_data.parse_variation_string("3.1234567891e-111mH")
    assert result == ("3.123456789e-111", "mH")

    result = simulation_data.parse_variation_string("3")
    assert result == ("3", "")

    result = simulation_data.parse_variation_string("3.0")
    assert result == ("3.0", "")


@mock.patch("aedttest.simulation_data.parse_profile_file", return_value="10:00:00")
@mock.patch("aedttest.simulation_data.parse_mesh_stats", return_value=100)
def test_extract_design_data(mock_parse_mesh, mock_parse_profile_file):
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
