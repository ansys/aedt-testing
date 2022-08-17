import json
import shutil
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from webdriver_manager.firefox import GeckoDriverManager

from aedttest import aedt_test_runner

from .test_aedt_test_runner import TESTS_DIR
from .test_aedt_test_runner import BaseElectronicsDesktopTester

options = FirefoxOptions()
options.headless = True

html_base_path = "/html/body/div/div/div/section"


class TestProjectWebPage(BaseElectronicsDesktopTester):
    def setup_class(self):
        super().setup(self)
        self.aedt_tester.only_reference = True

        with open(TESTS_DIR / "input" / "project_report.json") as file:
            proj_dict = json.load(file)

        self.aedt_tester.initialize_results()
        self.aedt_tester.render_project_html("just_winding_221", proj_dict)

        self.webpage = f"file:///{self.aedt_tester.results_path / 'just_winding_221.html'}"
        self.rel_path = str(self.aedt_tester.results_path).lower()
        self.driver = webdriver.Firefox(service=FirefoxService(GeckoDriverManager().install()), options=options)
        self.driver.get(str(self.webpage))

    def teardown_class(self):
        self.driver.close()
        shutil.rmtree(self.aedt_tester.results_path)

    def test_title(self):
        assert self.driver.title == "just_winding_221"

    def test_simtime_link(self):
        simtime_link = self.driver.find_element(
            by=By.XPATH, value=f"{html_base_path}/div[2]/div/div/div[2]/div/table/tbody/tr[2]/td[2]/a"
        )
        assert simtime_link.text == "00:00:07"

        profile_path = Path(simtime_link.get_attribute("href").replace("file:///", ""))
        assert profile_path.relative_to(self.rel_path) == Path("reference_folder", "profiles", "_VMG6BS.prof")

    def test_sim_name(self):
        sim_name = self.driver.find_element(
            by=By.XPATH, value=f"{html_base_path}/div[2]/div/div/div[2]/div/table/tbody/tr[1]/td[1]"
        )
        assert sim_name.text == "Maxwell2DDesign1:Setup1:xs=0.5mm"

    def test_mesh_link(self):
        mesh_link = self.driver.find_element(
            by=By.XPATH, value=f"{html_base_path}/div[3]/div/div/div[2]/div/table/tbody/tr[1]/td[2]/a"
        )
        assert mesh_link.text == "133"

        profile_path = Path(mesh_link.get_attribute("href").replace("file:///", ""))
        assert profile_path.relative_to(self.rel_path) == Path("reference_folder", "profiles", "_7GWPCH.mstat")

    def test_mesh_name(self):
        mesh_name = self.driver.find_element(
            by=By.XPATH, value=f"{html_base_path}/div[3]/div/div/div[2]/div/table/tbody/tr[2]/td[1]"
        )
        assert mesh_name.text == "Maxwell2DDesign1:Setup1:xs=6.000000000e-01mm"

    def test_plot_button(self):
        button = self.driver.find_element(by=By.XPATH, value=f"{html_base_path}/div[8]/div/div/button")

        assert button.text == "Maxwell2DDesign1:Loss Plot 1:SolidLoss:Pass=2 xs=0.6mm"

    def test_error_messages(self):
        msg = self.driver.find_element(
            by=By.XPATH, value=f"{html_base_path}/div[1]/div/div/div[2]/div/table/tbody/tr/td"
        )

        assert msg.text == "Design:Maxwell2DDesign1 Variation: xs=6.000000000e-01mm Setup: Setup1 has no mesh stats"


class TestMainWebPage(BaseElectronicsDesktopTester):
    def setup_class(self):
        self.aedt_tester = aedt_test_runner.ElectronicsDesktopTester(
            version="212",
            max_cores=9999,
            max_parallel_projects=9999,
            config_folder=TESTS_DIR / "input" / "configs",
            out_dir=None,
            save_projects=None,
            only_reference=True,
            reference_folder=None,
        )
        self.aedt_tester.only_reference = True

        self.aedt_tester.initialize_results()
        self.aedt_tester.render_main_html()

        self.webpage = f"file:///{self.aedt_tester.results_path / 'main.html'}"
        self.rel_path = str(self.aedt_tester.results_path).lower()
        self.driver = webdriver.Firefox(service=FirefoxService(GeckoDriverManager().install()), options=options)
        self.driver.get(str(self.webpage))

    def teardown_class(self):
        self.driver.close()
        shutil.rmtree(self.aedt_tester.results_path)

    def test_projects_order(self):
        project_names = ["01_voltage_control", "19", "2019R1", "expression_excitation", "just_winding"]

        for i, name in enumerate(project_names, start=1):
            elem = self.driver.find_element(
                by=By.XPATH, value=f"{html_base_path}/div[1]/div/div/div[2]/div/table/tbody/tr[{i}]/td[1]"
            )
            assert elem.text == name
