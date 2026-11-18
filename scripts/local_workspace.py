"""Run the existing APS workbench with fresh, checkout-local state."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".aps-local"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--make-example", action="store_true", help="Generate the synthetic XLSX workbook and exit.")
    parser.add_argument("--worker-url", help="Explicit URL of a separately provisioned APS Worker.")
    args = parser.parse_args()
    if args.make_example:
        from p2.finished_delivery_fixture import workbook_bytes

        data = json.loads((ROOT / "fixtures/finished-garment-confirmed.json").read_text(encoding="utf-8"))
        STATE.mkdir(exist_ok=True)
        workbook = STATE / "demo-garment.xlsx"
        if not workbook.exists():
            with workbook.open("xb") as stream:
                stream.write(workbook_bytes(data))
        print(workbook)
        return
    if not (ROOT / "frontend/dist/index.html").is_file():
        parser.error("Build frontend first: cd frontend, npm ci, npm run build.")
    if args.worker_url:
        worker = urlsplit(args.worker_url)
        if (worker.scheme not in {"http", "https"} or worker.hostname not in {"127.0.0.1", "localhost"}
                or worker.username or worker.password or worker.query or worker.fragment):
            parser.error("Use a loopback Worker HTTP(S) URL without credentials, a query or a fragment.")
        if not os.environ.get("PLANNING_COMPUTE_TOKEN"):
            parser.error("Set PLANNING_COMPUTE_TOKEN for the specified Worker.")
        os.environ.pop("PLANNING_COMPUTE_DISABLED", None)
        os.environ["PLANNING_COMPUTE_URL"] = args.worker_url
        os.environ["PLANNING_COMPUTE_WRITE_URL"] = args.worker_url
    else:
        os.environ["PLANNING_COMPUTE_DISABLED"] = "1"
    os.environ["PLANNING_STATE_DIR"] = str(STATE / "workbench")
    os.environ["PLANNING_RUNS_DIR"] = str(STATE / "runs")

    import uvicorn

    uvicorn.run("p1_api:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
