"""Object storage access and path conventions.

Provider-neutral by construction: every call goes through fsspec, which speaks
``gs://``, ``s3://`` and ``abfs://`` with identical semantics. Moving clouds
changes ``RP_STORAGE_URI`` and nothing in this file.

Two rules this module exists to enforce:

1. Paths are constructed here and nowhere else. A partition layout that drifts
   between jobs is unqueryable six months later.
2. Writes are atomic from a reader's point of view -- data lands first, the
   ``_SUCCESS`` marker lands second. A reader that checks for the marker never
   sees a half-written partition.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from datetime import date
from typing import Any
from uuid import uuid4

import fsspec
import pandas as pd

from jobs.common.config import get_settings

SUCCESS_MARKER = "_SUCCESS"
_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _component(value: str, label: str) -> str:
    """Reject separators and traversal in user/vendor-derived path segments."""
    if not _PATH_COMPONENT.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"invalid {label} path component: {value!r}")
    return value


# --- path construction -------------------------------------------------------


def _root() -> str:
    return get_settings().root_uri


def raw_prefix(source: str, dataset: str, run_date: date) -> str:
    """Landing zone: bytes as received, no transformation applied."""
    safe_source = _component(source, "source")
    safe_dataset = _component(dataset, "dataset")
    return f"{_root()}/raw/{safe_source}/{safe_dataset}/dt={run_date.isoformat()}"


def derived_prefix(dataset: str, run_date: date) -> str:
    """Anything computed from raw. Always reproducible by rerunning."""
    safe_dataset = _component(dataset, "dataset")
    return f"{_root()}/derived/{safe_dataset}/dt={run_date.isoformat()}"


def artifact_prefix(job: str, run_id: str) -> str:
    """Run-scoped outputs: reports, plots, model dumps."""
    return f"{_root()}/artifacts/{_component(job, 'job')}/{_component(run_id, 'run_id')}"


def integration_prefix(probe: str, run_date: date, probe_id: str) -> str:
    """Unique staging-integration partition for one synthetic probe run."""
    safe_probe = _component(probe, "probe")
    safe_probe_id = _component(probe_id, "probe_id")
    return (
        f"{_root()}/integration/{safe_probe}/dt={run_date.isoformat()}"
        f"/probe_id={safe_probe_id}"
    )


def dataset_glob(kind: str, *parts: str) -> str:
    """A glob spanning every partition, for DuckDB or pandas.

    >>> dataset_glob("derived", "pnl")
    'gs://bucket/derived/pnl/dt=*/*.parquet'
    """
    safe_kind = _component(kind, "kind")
    joined = "/".join(_component(part, "dataset") for part in parts)
    return f"{_root()}/{safe_kind}/{joined}/dt=*/*.parquet"


# --- filesystem --------------------------------------------------------------


def _fs_and_path(uri: str) -> tuple[Any, str]:
    fs, path = fsspec.core.url_to_fs(uri)
    return fs, path


def exists(uri: str) -> bool:
    fs, path = _fs_and_path(uri)
    return bool(fs.exists(path))


def remove(uri: str) -> None:
    """Remove one object if present."""
    fs, path = _fs_and_path(uri)
    if fs.exists(path):
        fs.rm(path)


def size_bytes(uri: str) -> int:
    fs, path = _fs_and_path(uri)
    if not fs.exists(path):
        return 0
    if fs.isdir(path):
        return sum(int(f.get("size", 0)) for f in fs.ls(path, detail=True))
    return int(fs.info(path).get("size", 0))


def read_json(uri: str) -> dict[str, Any]:
    fs, path = _fs_and_path(uri)
    with fs.open(path, "rb") as fh:
        return json.loads(fh.read().decode())


def write_json(uri: str, payload: dict[str, Any]) -> None:
    fs, path = _fs_and_path(uri)
    parent = path.rsplit("/", 1)[0]
    fs.makedirs(parent, exist_ok=True)
    _atomic_write(fs, path, json.dumps(payload, default=str, indent=2).encode())


def _atomic_write(fs: Any, path: str, payload: bytes) -> None:
    """Publish a complete object; never expose a partially written file."""
    temporary = f"{path}.tmp-{uuid4().hex}"
    try:
        with fs.open(temporary, "wb") as fh:
            fh.write(payload)
        if fs.exists(path):
            fs.rm(path)
        fs.mv(temporary, path)
    finally:
        with suppress(Exception):
            if fs.exists(temporary):
                fs.rm(temporary)


# --- parquet -----------------------------------------------------------------


def write_parquet(df: pd.DataFrame, prefix: str, filename: str = "part-000.parquet") -> str:
    """Write one parquet file under ``prefix`` and return its URI.

    Overwrites unconditionally: the same (job, date) must always produce the
    same path, so that a rerun replaces rather than duplicates.
    """
    fs, path = _fs_and_path(prefix)
    fs.makedirs(path, exist_ok=True)
    uri = f"{prefix.rstrip('/')}/{filename}"
    _, out_path = _fs_and_path(uri)
    temporary = f"{out_path}.tmp-{uuid4().hex}"
    try:
        with fs.open(temporary, "wb") as fh:
            df.to_parquet(fh, engine="pyarrow", index=False, compression="snappy")
        if fs.exists(out_path):
            fs.rm(out_path)
        fs.mv(temporary, out_path)
    finally:
        with suppress(Exception):
            if fs.exists(temporary):
                fs.rm(temporary)
    return uri


def read_parquet(uri_or_glob: str, **kwargs: Any) -> pd.DataFrame:
    fs, path = _fs_and_path(uri_or_glob)
    matches = sorted(fs.glob(path)) if any(c in path for c in "*?[") else [path]
    if not matches:
        raise FileNotFoundError(f"no parquet matched {uri_or_glob}")
    frames = []
    for match in matches:
        with fs.open(match, "rb") as fh:
            frames.append(pd.read_parquet(fh, engine="pyarrow", **kwargs))
    return pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]


# --- completion markers ------------------------------------------------------


def mark_success(prefix: str, metadata: dict[str, Any]) -> str:
    """Write the marker that makes a partition visible to downstream readers."""
    uri = f"{prefix.rstrip('/')}/{SUCCESS_MARKER}"
    write_json(uri, metadata)
    return uri


def is_complete(prefix: str) -> bool:
    return exists(f"{prefix.rstrip('/')}/{SUCCESS_MARKER}")


def clear_success(prefix: str) -> None:
    """Make a partition invisible before a forced replacement begins."""
    remove(f"{prefix.rstrip('/')}/{SUCCESS_MARKER}")
