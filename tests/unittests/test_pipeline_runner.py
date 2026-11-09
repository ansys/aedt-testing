import pytest

from aedttest.pipeline_runner import check_submissions


def test_all_submissions_failed_stops_with_clear_message():
    job_map = {
        "a": {"job_id": None, "project_path": "/p/a.aedt", "error": "x"},
        "b": {"job_id": None, "project_path": "/p/b.aedt", "error": "x"},
    }

    with pytest.raises(RuntimeError, match=r"\[271\] All 2 job submission"):
        check_submissions(job_map, "271")


def test_partial_submit_failure_continues():
    job_map = {
        "a": {"job_id": "1", "project_path": "/p/a.aedt"},
        "b": {"job_id": None, "project_path": "/p/b.aedt", "error": "x"},
    }

    check_submissions(job_map, "271")


def test_empty_job_map_does_not_raise():
    check_submissions({}, "271")
