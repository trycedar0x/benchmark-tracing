"""Full sample evidence, separate from truncated UI previews and filtered before storage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from everybench.config import settings
from everybench.redact import apply_policy


def sample_artifact_path(run_id: str, sample_id: str, epoch: int) -> Path:
    # Neither run nor sample identifiers become user-controlled path components.
    run_key = hashlib.sha256(run_id.encode()).hexdigest()
    sample_key = hashlib.sha256(json.dumps([sample_id, epoch]).encode()).hexdigest()
    return settings().files_dir / "sample-evidence" / run_key / f"{sample_key}.json"


def write_sample_artifact(run_id: str, sample_id: str, epoch: int, evidence: dict[str, Any], policy: str) -> None:
    path = sample_artifact_path(run_id, sample_id, epoch)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content, state = apply_policy(evidence, policy)
    record = {
        "schema": "everybench.sample-evidence.v1",
        "run_id": run_id,
        "sample_id": sample_id,
        "epoch": epoch,
        "content_state": state,
        "content": content,
    }
    fd, name = tempfile.mkstemp(prefix=".evidence-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_sample_artifact(run_id: str, sample_id: str, epoch: int) -> dict[str, Any]:
    # The caller must authorize workspace access to the run before reading.
    return json.loads(sample_artifact_path(run_id, sample_id, epoch).read_text())
