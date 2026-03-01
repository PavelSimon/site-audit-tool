"""
Fáza 7 — Load Test & Benchmark (Locust)
Výstup: results/<job_id>/07_load.json

Scenáre:
  baseline  — 1 VU, 30s
  light     — 5 VU, 60s
  medium    — 20 VU, 60s
"""

import csv
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

from audit.config import USER_AGENT, TIMEOUT

console = Console()

SCENARIOS = [
    {"name": "baseline", "users": 1,  "spawn_rate": 1,  "run_time": "30s"},
    {"name": "light",    "users": 5,  "spawn_rate": 2,  "run_time": "60s"},
    {"name": "medium",   "users": 20, "spawn_rate": 5,  "run_time": "60s"},
]


def discover_load_pages(target_url: str) -> list[tuple[str, str]]:
    """Discovers homepage + up to 2 internal pages for load testing."""
    pages = [("/", "Homepage /")]
    base_host = urlparse(target_url).netloc

    try:
        resp = httpx.get(target_url, headers={"User-Agent": USER_AGENT},
                         timeout=TIMEOUT, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")

        for a in soup.find_all("a", href=True):
            href = a["href"]
            full = urljoin(target_url, href)
            parsed = urlparse(full)
            path = parsed.path
            if (parsed.netloc == base_host and path and path != "/"
                    and not path.startswith("//")
                    and len(pages) < 3):
                label = f"Page {path}"
                if (path, label) not in pages:
                    pages.append((path, label))
    except Exception:
        pass

    return pages


def build_locust_file(target_url: str, pages: list[tuple[str, str]]) -> str:
    task_blocks = []
    for i, (path, label) in enumerate(pages):
        weight = 3 if i == 0 else 1
        safe_name = label.replace("/", "_").replace(" ", "_").strip("_") or f"page_{i}"
        task_blocks.append(
            f"    @task({weight})\n"
            f"    def {safe_name}(self):\n"
            f"        self.client.get({path!r}, headers=self.headers, name={label!r})\n"
        )

    tasks_block = "\n".join(task_blocks)
    return (
        "from locust import HttpUser, task, between\n\n"
        "class AuditUser(HttpUser):\n"
        f"    wait_time = between(1, 3)\n"
        f"    host = {target_url!r}\n"
        f"    headers = {{\"User-Agent\": {USER_AGENT!r}}}\n\n"
        f"{tasks_block}"
    )


def parse_locust_csv(stats_file: Path) -> list[dict]:
    rows = []
    if not stats_file.exists():
        return rows
    with open(stats_file, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Name") in ("Aggregated", ""):
                continue
            try:
                rows.append({
                    "name": row["Name"],
                    "requests": int(row.get("Request Count", 0)),
                    "failures": int(row.get("Failure Count", 0)),
                    "median_ms": float(row.get("50%", 0) or 0),
                    "p95_ms": float(row.get("95%", 0) or 0),
                    "p99_ms": float(row.get("99%", 0) or 0),
                    "avg_ms": float(row.get("Average (ms)", 0) or 0),
                    "min_ms": float(row.get("Min (ms)", 0) or 0),
                    "max_ms": float(row.get("Max (ms)", 0) or 0),
                    "rps": float(row.get("Requests/s", 0) or 0),
                    "failure_rate_pct": round(
                        int(row.get("Failure Count", 0)) /
                        max(int(row.get("Request Count", 1)), 1) * 100, 2
                    ),
                })
            except (ValueError, KeyError):
                continue
    return rows


def run_scenario(locust_file: Path, target_url: str, scenario: dict, tmp_dir: Path) -> dict:
    name = scenario["name"]
    console.print(f"\n  [bold]Scenár:[/] {name}  "
                  f"({scenario['users']} VU, {scenario['run_time']})")

    prefix = tmp_dir / name
    cmd = [
        sys.executable, "-m", "locust",
        "-f", str(locust_file),
        "--headless",
        "--host", target_url,
        "--users", str(scenario["users"]),
        "--spawn-rate", str(scenario["spawn_rate"]),
        "--run-time", scenario["run_time"],
        "--csv", str(prefix),
        "--csv-full-history",
        "--only-summary",
        "--loglevel", "WARNING",
    ]

    t0 = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    elapsed = time.perf_counter() - t0

    if result.returncode != 0:
        console.print(f"  [red]locust stderr:[/] {result.stderr[-500:]}")

    stats = parse_locust_csv(Path(str(prefix) + "_stats.csv"))

    agg = None
    agg_file = Path(str(prefix) + "_stats.csv")
    if agg_file.exists():
        with open(agg_file, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("Name") == "Aggregated":
                    agg = {
                        "requests": int(row.get("Request Count", 0)),
                        "failures": int(row.get("Failure Count", 0)),
                        "median_ms": float(row.get("50%", 0) or 0),
                        "p95_ms": float(row.get("95%", 0) or 0),
                        "p99_ms": float(row.get("99%", 0) or 0),
                        "avg_ms": float(row.get("Average (ms)", 0) or 0),
                        "rps": float(row.get("Requests/s", 0) or 0),
                        "failure_rate_pct": round(
                            int(row.get("Failure Count", 0)) /
                            max(int(row.get("Request Count", 1)), 1) * 100, 2
                        ),
                    }

    if agg:
        color = "green" if agg["p95_ms"] < 1000 else ("yellow" if agg["p95_ms"] < 3000 else "red")
        console.print(
            f"  Requests: {agg['requests']}  "
            f"Failures: {agg['failures']}  "
            f"p50: {agg['median_ms']:.0f}ms  "
            f"[{color}]p95: {agg['p95_ms']:.0f}ms[/]  "
            f"p99: {agg['p99_ms']:.0f}ms  "
            f"RPS: {agg['rps']:.1f}  "
            f"Error%: {agg['failure_rate_pct']:.1f}%"
        )

    return {
        "scenario": scenario,
        "duration_s": round(elapsed, 1),
        "per_endpoint": stats,
        "aggregated": agg,
    }


def main(target_url: str, results_dir: Path) -> dict:
    console.rule("[bold]Fáza 7 — Load Test & Benchmark")

    pages = discover_load_pages(target_url)
    console.print(f"  Testované cesty: {[p[0] for p in pages]}")

    results = {"target": target_url, "scenarios": []}

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        locust_content = build_locust_file(target_url, pages)
        locust_file = tmp / "locustfile.py"
        locust_file.write_text(locust_content)

        for scenario in SCENARIOS:
            data = run_scenario(locust_file, target_url, scenario, tmp)
            results["scenarios"].append(data)

    console.rule("[bold cyan]Súhrn load testu")
    t = Table("Scenár", "VU", "Requesty", "Failures", "p50 ms", "p95 ms",
              "p99 ms", "RPS", "Error%")
    for s in results["scenarios"]:
        agg = s.get("aggregated") or {}
        p95 = agg.get("p95_ms", 0)
        color = "green" if p95 < 1000 else ("yellow" if p95 < 3000 else "red")
        t.add_row(
            s["scenario"]["name"],
            str(s["scenario"]["users"]),
            str(agg.get("requests", "—")),
            str(agg.get("failures", "—")),
            f"{agg.get('median_ms', 0):.0f}",
            f"[{color}]{agg.get('p95_ms', 0):.0f}[/]",
            f"{agg.get('p99_ms', 0):.0f}",
            f"{agg.get('rps', 0):.1f}",
            f"{agg.get('failure_rate_pct', 0):.1f}%",
        )
    console.print(t)

    out_file = results_dir / "07_load.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    console.print(f"\n[bold green]✓ Výsledok uložený:[/] {out_file}")
    return results


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else input("URL: ")
    main(url, Path("results"))
