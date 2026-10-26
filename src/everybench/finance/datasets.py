"""Pinned, hash-verified public finance data. Nothing is downloaded at import time."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import tempfile
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from everybench.config import home_dir

MAX_DATASET_BYTES = 20_000_000


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    revision: str
    url: str
    sha256: str
    count: int
    format: str
    license: str
    attribution: str
    license_url: str


DATASETS = {
    "findeepindicator": DatasetSpec(
        name="findeepindicator",
        revision="f59dad280fba4e736ccf346eb234265d13fc6737",
        url="https://huggingface.co/datasets/OpenFinArena/FinDeepIndicator/resolve/"
        "f59dad280fba4e736ccf346eb234265d13fc6737/all.csv",
        sha256="5dcf594762b283cd2059204a2f43d9dedc3ef4d390f43349d122c6ffc6777343",
        count=3350,
        format="csv",
        license="Apache-2.0",
        attribution="FinDeepIndicator / OpenFinArena; arXiv:2608.00764",
        license_url="https://huggingface.co/datasets/OpenFinArena/FinDeepIndicator/blob/"
        "f59dad280fba4e736ccf346eb234265d13fc6737/README.md",
    ),
    "bigfinance-public-50": DatasetSpec(
        name="bigfinance-public-50",
        revision="3b13eecc4cacb4cfa13ac2b7c111d14ce127dd71",
        url="https://raw.githubusercontent.com/Rogo-Technologies/big-finance-benchmark/"
        "3b13eecc4cacb4cfa13ac2b7c111d14ce127dd71/data/big_finance_subset.jsonl",
        sha256="0c1b06b2cf80ddc85eec9e06913abae33a5ff4e27f936cb510dd6286083171c5",
        count=50,
        format="jsonl",
        license="CC-BY-4.0",
        attribution="Rogo Technologies and the Big Finance authors; arXiv:2606.03829",
        license_url="https://github.com/Rogo-Technologies/big-finance-benchmark/blob/"
        "3b13eecc4cacb4cfa13ac2b7c111d14ce127dd71/data/LICENSE-DATA",
    ),
}


class DatasetError(ValueError):
    pass


def parse_dataset(body: bytes, spec: DatasetSpec) -> list[dict[str, Any]]:
    if len(body) > MAX_DATASET_BYTES or hashlib.sha256(body).hexdigest() != spec.sha256:
        raise DatasetError(f"{spec.name}: dataset size or SHA-256 mismatch; refusing changed data.")
    try:
        text = body.decode("utf-8-sig")
        if spec.format == "csv":
            rows = list(csv.DictReader(io.StringIO(text)))
        elif spec.format == "jsonl":
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            raise DatasetError("Unknown dataset format.")
        if len(rows) != spec.count:
            raise DatasetError(f"{spec.name}: expected {spec.count} items, got {len(rows)}.")
        ids: set[str] = set()
        for row in rows:
            identity = row["id"]
            if isinstance(identity, bool) or not isinstance(identity, str | int):
                raise DatasetError("Sample ids must be strings or integers.")
            sample_id = str(identity)
            if not sample_id.strip() or sample_id in ids:
                raise DatasetError("Missing or duplicate sample id.")
            ids.add(sample_id)
            if spec.name == "findeepindicator":
                required = ("question", "answer", "formula", "data")
                if not math.isfinite(float(row["answer"])):
                    raise DatasetError("Non-finite reference answer.")
                if "data_indicator" not in row:
                    raise DatasetError("Missing data_indicator column.")
            elif spec.name == "bigfinance-public-50":
                required = ("query", "reference_answer", "rubric")
                if not isinstance(row["rubric"], list) or not row["rubric"]:
                    raise DatasetError("Missing rubric.")
                for criterion in row["rubric"]:
                    points = criterion["points"]
                    if not criterion["text"] or isinstance(points, bool) or not isinstance(points, int | float):
                        raise DatasetError("Invalid rubric criterion.")
                    if not math.isfinite(points) or points <= 0:
                        raise DatasetError("Rubric points must be positive and finite.")
            else:
                raise DatasetError("Unknown finance dataset schema.")
            if any(not row.get(key) or (isinstance(row[key], str) and not row[key].strip()) for key in required):
                raise DatasetError(f"Missing required fields for sample {sample_id}.")
        return rows
    except (UnicodeError, KeyError, TypeError, ValueError) as ex:
        if isinstance(ex, DatasetError):
            raise
        raise DatasetError(f"{spec.name}: invalid dataset schema: {ex}") from ex


def load_dataset(name: str, *, cache_dir: Path | None = None) -> list[dict[str, Any]]:
    """Fetch immutable public data once; recheck its hash on every read, including cache hits."""
    if name not in DATASETS:
        raise DatasetError(f"Unknown finance dataset {name!r}.")
    spec = DATASETS[name]
    directory = (cache_dir or home_dir() / "benchmark-data") / name / spec.revision
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / f"data.{spec.format}"
    if path.exists():
        body = path.read_bytes()
    else:
        request = urllib.request.Request(spec.url, headers={"User-Agent": "everybench/0.1 finance-eval"})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read(MAX_DATASET_BYTES + 1)
        # Validate before writing any bytes to the shared cache.
        parse_dataset(body, spec)
        fd, tmp = tempfile.mkstemp(prefix=".data-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            Path(tmp).unlink(missing_ok=True)
    rows = parse_dataset(body, spec)
    # Keep attribution, license, canaries and do_not_train flags with the unmodified data.
    (directory / "source.json").write_text(json.dumps(asdict(spec), indent=2))
    return rows
