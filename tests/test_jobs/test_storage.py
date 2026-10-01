from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from jobs.common import storage


def test_path_layout_is_hive_partitioned():
    d = date(2026, 8, 11)
    assert storage.raw_prefix("vendor", "ohlcv_hourly", d).endswith(
        "/raw/vendor/ohlcv_hourly/dt=2026-08-11"
    )
    assert storage.derived_prefix("daily_pnl", d).endswith("/derived/daily_pnl/dt=2026-08-11")
    assert storage.integration_prefix("staging_probe", d, "probe-123").endswith(
        "/integration/staging_probe/dt=2026-08-11/probe_id=probe-123"
    )


def test_glob_spans_all_partitions():
    assert storage.dataset_glob("derived", "daily_pnl").endswith(
        "/derived/daily_pnl/dt=*/*.parquet"
    )


def test_round_trip_parquet():
    prefix = storage.derived_prefix("unit", date(2026, 8, 11))
    df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
    uri = storage.write_parquet(df, prefix)

    assert storage.exists(uri)
    assert storage.size_bytes(prefix) > 0
    pd.testing.assert_frame_equal(storage.read_parquet(uri), df)


def test_partition_is_incomplete_until_marked():
    """The invariant downstream tasks depend on: data first, marker second."""
    prefix = storage.derived_prefix("unit", date(2026, 8, 11))
    storage.write_parquet(pd.DataFrame({"a": [1]}), prefix)

    assert storage.is_complete(prefix) is False
    storage.mark_success(prefix, {"rows": 1})
    assert storage.is_complete(prefix) is True
    assert storage.read_json(f"{prefix}/_SUCCESS")["rows"] == 1


def test_rerun_overwrites_rather_than_duplicating():
    prefix = storage.derived_prefix("unit", date(2026, 8, 11))
    storage.write_parquet(pd.DataFrame({"a": [1, 2, 3]}), prefix)
    storage.write_parquet(pd.DataFrame({"a": [9]}), prefix)

    assert len(storage.read_parquet(f"{prefix}/*.parquet")) == 1


def test_missing_glob_raises():
    with pytest.raises(FileNotFoundError):
        storage.read_parquet(storage.dataset_glob("derived", "nonexistent"))


@pytest.mark.parametrize("component", ["../escape", "nested/path", "", ".", ".."])
def test_path_components_cannot_escape_the_storage_root(component: str):
    with pytest.raises(ValueError, match="invalid dataset"):
        storage.derived_prefix(component, date(2026, 8, 11))


@pytest.mark.parametrize("component", ["../escape", "nested/path", "", ".", ".."])
def test_integration_probe_ids_cannot_escape_the_storage_root(component: str):
    with pytest.raises(ValueError, match="invalid probe_id"):
        storage.integration_prefix("staging_probe", date(2026, 8, 11), component)
