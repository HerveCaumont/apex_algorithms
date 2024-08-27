"""
Pytest plugin to track test/benchmark metrics and report them with a JSON file.


Usage:

-   Enable the plugin in `conftest.py`:

    ```python
    pytest_plugins = [
        "apex_algorithm_qa_tools.pytest_track_metrics",
    ]
    ```

-   Use the `track_metric` fixture to record metrics during tests:

    ```python
    def test_dummy(track_metric):
        x = 3
        track_metric("x squared", x*x)
    ...

-   Run the tests with desired configuration through CLI options and env vars:
    - CLI option to set JSON file to store the metrics to:
      `--track-metrics-json=path/to/metrics.json`
    - S3 credentials with env vars `APEX_ALGORITHMS_S3_ACCESS_KEY_ID`
      and `APEX_ALGORITHMS_S3_SECRET_ACCESS_KEY`
      (Note that the classic `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`
      are also supported as fallback)
    - S3 endpoint URL with env var `APEX_ALGORITHMS_S3_ENDPOINT_URL`
      (Note that the classic `AWS_ENDPOINT_URL` is also supported as fallback).
"""

import dataclasses
import json
import os
import warnings
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple, Union

import pyarrow
import pyarrow.fs
import pyarrow.parquet
import pytest

_TRACK_METRICS_PLUGIN_NAME = "track_metrics"


_S3_KEY_DEFAULT = "metrics/v0/metrics.parquet"


def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--track-metrics-json",
        metavar="PATH",
        help="Path to JSON file to store test/benchmark metrics.",
    )
    parser.addoption(
        "--track-metrics-parquet-s3-bucket",
        metavar="BUCKET",
        help="S3 bucket to use for Parquet storage of metrics.",
    )
    parser.addoption(
        "--track-metrics-parquet-s3-key",
        metavar="KEY",
        default=_S3_KEY_DEFAULT,
        help="S3 key to use for Parquet storage of metrics.",
    )


def pytest_configure(config):
    if hasattr(config, "workerinput"):
        warnings.warn("`track_metrics` plugin is not supported on xdist worker nodes.")
        return

    track_metrics_json = config.getoption("--track-metrics-json")

    track_metrics_parquet_s3_bucket = config.getoption(
        "--track-metrics-parquet-s3-bucket"
    )
    track_metrics_parquet_s3_key = config.getoption(
        "--track-metrics-parquet-s3-key", _S3_KEY_DEFAULT
    )

    if track_metrics_json or track_metrics_parquet_s3_bucket:
        config.pluginmanager.register(
            TrackMetricsReporter(
                json_path=track_metrics_json,
                parquet_s3=_ParquetS3StorageSettings(
                    bucket=track_metrics_parquet_s3_bucket,
                    key=track_metrics_parquet_s3_key,
                ),
            ),
            name=_TRACK_METRICS_PLUGIN_NAME,
        )


@dataclasses.dataclass(frozen=True)
class _ParquetS3StorageSettings:
    bucket: str | None = None
    key: str = _S3_KEY_DEFAULT


class TrackMetricsReporter:
    def __init__(
        self,
        json_path: Union[None, str, Path] = None,
        parquet_s3: Optional[_ParquetS3StorageSettings] = None,
        user_properties_key: str = "track_metrics",
    ):
        self._json_path = Path(json_path) if json_path else None
        self._parquet_s3 = parquet_s3
        self._suite_metrics: List[dict] = []
        self._user_properties_key = user_properties_key

    def pytest_runtest_logreport(self, report: pytest.TestReport):
        if report.when == "call":
            self._suite_metrics.append(
                {
                    "nodeid": report.nodeid,
                    "report": {
                        "outcome": report.outcome,
                        "duration": report.duration,
                        "start": report.start,
                        "stop": report.stop,
                    },
                    "metrics": self.get_metrics(report.user_properties),
                }
            )

    def pytest_sessionfinish(self, session):
        if self._json_path:
            self._write_json_report(self._json_path)

        if self._parquet_s3.bucket:
            # TODO: also support local parquet file (without partitioning), e.g. for test/debug reasons
            self._write_parquet_report(self._parquet_s3)

    def _write_json_report(self, path: Union[str, Path]):
        with Path(path).open("w", encoding="utf8") as f:
            json.dump(self._suite_metrics, f, indent=2)

    def _write_parquet_report(self, parquet_s3: _ParquetS3StorageSettings):
        # Compile all (free-form) metrics into a more rigid table
        columns = set()
        suite_metrics = []
        for m in self._suite_metrics:
            node_metrics = {
                "nodeid": m["nodeid"],
                "outcome": m["report"]["outcome"],
                # TODO: more explicit that this about timing of test, not just the job
                "duration": m["report"]["duration"],
                "start": m["report"]["start"],
                "stop": m["report"]["stop"],
                # TODO: add start date as iso date string
                # TODO: also include runid (like in upload_assets)
            }
            for k, v in m["metrics"]:
                assert k not in node_metrics, f"Duplicate metric key: {k}"
                node_metrics[k] = v
            columns.update(node_metrics.keys())
            suite_metrics.append(node_metrics)

        table = pyarrow.Table.from_pydict(
            {col: [m.get(col) for m in suite_metrics] for col in columns}
        )

        fs = pyarrow.fs.S3FileSystem(
            access_key=os.environ.get("APEX_ALGORITHMS_S3_ACCESS_KEY_ID"),
            secret_key=os.environ.get("APEX_ALGORITHMS_S3_SECRET_ACCESS_KEY"),
            endpoint_override=os.environ.get("APEX_ALGORITHMS_S3_ENDPOINT_URL"),
        )
        root_path = f"{parquet_s3.bucket}/{parquet_s3.key}"
        pyarrow.parquet.write_to_dataset(
            table=table,
            root_path=root_path,
            filesystem=fs,
            # TODO: add support for partitioning (date and nodeid)
        )

    def pytest_report_header(self):
        return f"Plugin `track_metrics` is active, reporting to json={self._json_path}, parquet={self._parquet_s3}"

    def pytest_terminal_summary(self, terminalreporter):
        reports = []
        if self._json_path:
            reports.append(str(self._json_path))
        if self._parquet_s3:
            reports.append(str(self._parquet_s3))
        if reports:
            terminalreporter.write_sep(
                "-", f"Generated track_metrics report: {', '.join(reports)}"
            )

    def get_metrics(
        self, user_properties: List[Tuple[str, Any]]
    ) -> List[Tuple[str, Any]]:
        """
        Extract existing test metrics items from user properties
        or create new one.
        """
        for name, value in user_properties:
            if name == self._user_properties_key:
                return value
        # Not found: create it
        metrics = []
        user_properties.append((self._user_properties_key, metrics))
        return metrics


@pytest.fixture
def track_metric(
    pytestconfig: pytest.Config, request: pytest.FixtureRequest
) -> Callable[[str, Any], None]:
    """
    Fixture to record a metric during tests/benchmarks,
    which will be stored in the pytest node's "user_properties".

    Returns a callable that expects a metric name and value
    """

    reporter: TrackMetricsReporter | None = pytestconfig.pluginmanager.get_plugin(
        _TRACK_METRICS_PLUGIN_NAME
    )

    if reporter:

        def append(name: str, value: Any):
            reporter.get_metrics(request.node.user_properties).append((name, value))
    else:
        warnings.warn("Fixture `track_metric` is a no-op (incomplete set up).")

        def append(name: str, value: Any):
            pass

    return append
