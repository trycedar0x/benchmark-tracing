"""Export actual offline API payloads for mocked browser tests; never execute queued work."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def main():
    with TemporaryDirectory(prefix="everybench-web-contract-") as directory:
        # Never use the invoking operator's database, auth or target configuration.
        for name in list(os.environ):
            if name.startswith(("EVERYBENCH_", "EVERYEVAL_", "BENCHTRACE_")):
                del os.environ[name]
        os.environ["EVERYBENCH_HOME"] = directory
        profiles = [
            {
                "id": "response-agent",
                "name": "Response agent",
                "version": "v1",
                "workspace_id": None,
                "url": "https://agent.example/private-capability",
                "token_secret": "AGENT_TOKEN",
            },
            {
                "id": "trace-agent",
                "name": "Trace agent",
                "version": "v2",
                "workspace_id": None,
                "url": "https://trace-agent.example/private-capability",
                "token_secret": "AGENT_TOKEN",
                "trace": {"type": "store"},
            },
        ]
        path = Path(directory) / "targets.json"
        path.write_text(json.dumps(profiles))
        os.environ["EVERYBENCH_TARGETS_FILE"] = str(path)

        from fastapi.testclient import TestClient

        from everybench.finance import datasets
        from everybench.server import create_app

        def forbidden(*args, **kwargs):
            raise AssertionError("Browser contract discovery/submission must not load datasets")

        datasets.load_dataset = forbidden
        with TestClient(create_app(workers=0), base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
            catalog = client.get("/api/catalog")
            targets = client.get("/api/evaluation-targets")
            queued = client.post(
                "/api/runs",
                json={
                    "benchmark": "findeepindicator",
                    "target_id": "response-agent",
                    "judge_models": ["mock/perfect"],
                    "extractor_model": "mock/perfect",
                    "allow_paid": True,
                },
            )
            assert catalog.status_code == targets.status_code == 200
            assert queued.status_code == 201, queued.text
            print(json.dumps({"catalog": catalog.json(), "targets": targets.json(), "queuedRun": queued.json()}))


if __name__ == "__main__":
    main()
