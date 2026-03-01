"""
Audit runner — orchestruje fázy 1–8 a streamuje progress cez callback.
Fázy sa importujú dynamicky cez file path, aby sa vyhlo problémom s číslami v názvoch modulov.
"""

import asyncio
import importlib.util
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

_AUDIT_DIR = Path(__file__).parent.parent / "audit"


def _load_module(filename: str):
    """Loads a Python module from the audit/ directory by filename."""
    module_path = _AUDIT_DIR / filename
    spec = importlib.util.spec_from_file_location(filename.replace(".py", ""), module_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Load all phase modules once at startup
_MOD_DISCOVERY = _load_module("01_discovery.py")
_MOD_PERFORMANCE = _load_module("02_performance.py")
_MOD_SEO = _load_module("03_seo.py")
_MOD_ACCESSIBILITY = _load_module("04_accessibility.py")
_MOD_SECURITY = _load_module("05_security.py")
_MOD_COMPLIANCE = _load_module("06_compliance.py")
_MOD_LOAD = _load_module("07_load.py")
_MOD_REPORT = _load_module("report.py")

# (phase_id, label, module) — phase_id matches checkbox values "01"–"07"
_PHASE_DEFS = [
    ("01", "Fáza 1 — Discovery & Reconnaissance",    _MOD_DISCOVERY),
    ("02", "Fáza 2 — Performance Audit",             _MOD_PERFORMANCE),
    ("03", "Fáza 3 — SEO Audit",                     _MOD_SEO),
    ("04", "Fáza 4 — Accessibility (WCAG 2.1 AA)",   _MOD_ACCESSIBILITY),
    ("05", "Fáza 5 — Security Audit",                _MOD_SECURITY),
    ("06", "Fáza 6 — GDPR & Compliance",             _MOD_COMPLIANCE),
    ("07", "Fáza 7 — Load Test",                     _MOD_LOAD),
]

_executor = ThreadPoolExecutor(max_workers=2)


async def run_audit(
    job_id: str,
    target_url: str,
    results_dir: Path,
    progress_callback: Callable[[str], None],
    selected_phases: set[str] | None = None,
) -> str:
    """
    Runs all 7 audit phases sequentially + report generation.
    Calls progress_callback(sse_message) after each phase.
    Returns the final Markdown report as a string.
    """
    loop = asyncio.get_event_loop()
    active_phases = selected_phases or {num for num, _, _ in [(p[0], p[1], p[2]) for p in _PHASE_DEFS]}
    active_phase_list = [(num, label, mod) for num, label, mod in _PHASE_DEFS
                         if num in active_phases]
    total = len(active_phase_list) + 1  # +1 for report

    for step_index, (phase_num, label, module) in enumerate(active_phase_list, 1):
        progress_callback(f"data: [{step_index}/{total}] {label}\n\n")
        try:
            await loop.run_in_executor(
                _executor,
                module.main,
                target_url,
                results_dir,
            )
        except Exception as exc:
            error_msg = f"[CHYBA] {label}: {exc}"
            progress_callback(f"data: {error_msg}\n\n")
            # Pokračujeme ďalej — čiastočné výsledky sú stále užitočné

    progress_callback(f"data: [{total}/{total}] Generujem súhrnný report…\n\n")
    try:
        markdown_report = await loop.run_in_executor(
            _executor,
            _MOD_REPORT.main,
            target_url,
            results_dir,
        )
    except Exception as exc:
        markdown_report = (
            f"# Chyba pri generovaní reportu\n\n"
            f"```\n{exc}\n\n{traceback.format_exc()}\n```"
        )
        (results_dir / "REPORT.md").write_text(markdown_report, encoding="utf-8")

    progress_callback("data: DONE\n\n")
    return markdown_report
