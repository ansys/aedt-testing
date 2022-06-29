import json

from .test_aedt_test_runner import TESTS_DIR
from .test_aedt_test_runner import BaseElectronicsDesktopTester


class TestProjectWebPage(BaseElectronicsDesktopTester):
    def test_creation(self):
        with open(TESTS_DIR / "input" / "project_report.json") as file:
            proj_dict = json.load(file)

        self.aedt_tester.initialize_results()
        self.aedt_tester.render_project_html("19", proj_dict)
