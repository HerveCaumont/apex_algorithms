"""
Pytest plugin to collect files generated during benchmark/test
and upload them to S3 (e.g. on test failure).

Usage:

-  Enable the plugin in `conftest.py`:

    ```python
    pytest_plugins = [
        "apex_algorithm_qa_tools.pytest_upload_assets",
    ]

-  Use the `upload_assets` fixture to register files for upload:

    ```python
    def test_dummy(upload_assets, tmp_path):
        path = tmp_path / "dummy.txt"
        path.write_text("dummy content")
        upload_assets(path)
    ```

- Run the tests with:
    - `--upload-assets-run-id=RUNID` (optional, defaults to random UUID)
    - `--upload-assets-endpoint-url=URL`
    - `--upload-assets-bucket=BUCKET`
    - and env vars `UPLOAD_ASSETS_ACCESS_KEY_ID` and `UPLOAD_ASSETS_SECRET_ACCESS_KEY` set.
"""

import logging
import os
import re
import uuid
import warnings
from pathlib import Path
from typing import Callable, Dict, Union

import boto3
import pytest

_log = logging.getLogger(__name__)

_UPLOAD_ASSETS_PLUGIN_NAME = "upload_assets"


def pytest_addoption(parser: pytest.Parser):
    # TODO: option to always upload (also on success).
    parser.addoption(
        "--upload-assets-run-id",
        metavar="RUNID",
        help="The run ID to use for building the S3 key.",
    )
    parser.addoption(
        "--upload-assets-endpoint-url",
        metavar="URL",
        help="The S3 endpoint URL to upload to.",
    )
    parser.addoption(
        "--upload-assets-bucket",
        metavar="BUCKET",
        help="The S3 bucket to upload to.",
    )


def pytest_configure(config: pytest.Config):
    run_id = config.getoption("upload_assets_run_id")
    endpoint_url = config.getoption("upload_assets_endpoint_url")
    bucket = config.getoption("upload_assets_bucket")
    if endpoint_url and bucket:
        s3_client = boto3.client(
            service_name="s3",
            aws_access_key_id=os.environ.get("UPLOAD_ASSETS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("UPLOAD_ASSETS_SECRET_ACCESS_KEY"),
            endpoint_url=endpoint_url,
        )
        config.pluginmanager.register(
            S3UploadPlugin(run_id=run_id, s3_client=s3_client, bucket=bucket),
            name=_UPLOAD_ASSETS_PLUGIN_NAME,
        )


def pytest_report_header(config):
    plugin: S3UploadPlugin | None = config.pluginmanager.get_plugin(
        _UPLOAD_ASSETS_PLUGIN_NAME
    )
    if plugin:
        return f"Plugin `upload_assets` is active, with upload to {plugin.bucket!r}"


def pytest_unconfigure(config):
    if config.pluginmanager.hasplugin(_UPLOAD_ASSETS_PLUGIN_NAME):
        config.pluginmanager.unregister(name=_UPLOAD_ASSETS_PLUGIN_NAME)


class _Collector:
    """
    Collects test outcomes and files to upload for a single test node.
    """

    def __init__(self, nodeid: str) -> None:
        self.nodeid = nodeid
        self.outcomes: Dict[str, str] = {}
        self.assets: Dict[str, Path] = {}

    def set_outcome(self, when: str, outcome: str):
        self.outcomes[when] = outcome

    def collect(self, path: Path, name: str):
        self.assets[name] = path


class S3UploadPlugin:
    def __init__(self, *, run_id: str | None = None, s3_client, bucket: str) -> None:
        self.run_id = run_id or uuid.uuid4().hex
        self.collector: Union[_Collector, None] = None
        self.s3_client = s3_client
        self.bucket = bucket

    def pytest_runtest_logstart(self, nodeid, location):
        self.collector = _Collector(nodeid=nodeid)

    def pytest_runtest_logreport(self, report: pytest.TestReport):
        self.collector.set_outcome(when=report.when, outcome=report.outcome)

    def pytest_runtest_logfinish(self, nodeid, location):
        # TODO: option to also upload on success?
        if self.collector.outcomes.get("call") == "failed":
            self._upload(self.collector)

        self.collector = None

    def _upload(self, collector: _Collector):
        for name, path in collector.assets.items():
            nodeid = re.sub(r"[^a-zA-Z0-9_.-]", "_", collector.nodeid)
            key = f"{self.run_id}!{nodeid}!{name}"
            # TODO: get upload info in report?
            _log.info(f"Uploading {path} to {self.bucket}/{key}")
            self.s3_client.upload_file(
                Filename=str(path),
                Bucket=self.bucket,
                Key=key,
                # TODO: option to override ACL, or ExtraArgs in general?
                ExtraArgs={"ACL": "public-read"},
            )


@pytest.fixture
def upload_assets(pytestconfig: pytest.Config, tmp_path) -> Callable:
    """
    Fixture to register a file (under `tmp_path`) for S3 upload
    after the test failed. The fixture is a function that
    can be called with one or more `Path` objects to upload.
    """
    uploader: S3UploadPlugin | None = pytestconfig.pluginmanager.get_plugin(
        _UPLOAD_ASSETS_PLUGIN_NAME
    )

    if uploader:

        def collect(*paths: Path):
            for path in paths:
                # TODO: option to make relative from other root
                #       (e.g. when test uses an `actual` folder for actual results)
                assert path.is_relative_to(tmp_path)
                name = str(path.relative_to(tmp_path))
                uploader.collector.collect(path=path, name=name)
    else:
        warnings.warn("Fixture `upload_assets` is a no-op (incomplete set up).")

        def collect(*paths: Path):
            pass

    return collect
