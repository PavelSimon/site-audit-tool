"""
Site Audit Tool — FastAPI web aplikácia
Spustenie: uvicorn web.app:app --reload
"""

import asyncio
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Site Audit Tool")

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
RESULTS_DIR = BASE_DIR.parent / "results"
RESULTS_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# In-memory job state: job_id → {"url": str, "status": str, "report_md": str|None}
_jobs: dict[str, dict] = {}
# SSE queues: job_id → asyncio.Queue
_sse_queues: dict[str, asyncio.Queue] = {}


def _validate_url(url: str) -> str:
    """Normalizes and validates the target URL. Returns cleaned URL or raises ValueError."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError("Neplatná URL adresa")
    # Normalize: remove trailing slash
    return url.rstrip("/")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/audit/start")
async def start_audit(request: Request, url: str = Form(...)):
    try:
        clean_url = _validate_url(url)
    except ValueError as exc:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": str(exc)},
            status_code=400,
        )

    job_id = str(uuid.uuid4())
    job_results_dir = RESULTS_DIR / job_id
    job_results_dir.mkdir(parents=True, exist_ok=True)

    _jobs[job_id] = {"url": clean_url, "status": "running", "report_md": None}
    _sse_queues[job_id] = asyncio.Queue()

    # Start audit in background
    asyncio.create_task(_run_audit_background(job_id, clean_url, job_results_dir))

    return RedirectResponse(f"/audit/{job_id}", status_code=303)


async def _run_audit_background(job_id: str, target_url: str, results_dir: Path):
    """Background task that runs the audit and pushes SSE events."""
    from web.runner import run_audit

    queue = _sse_queues.get(job_id)

    def progress_callback(message: str):
        if queue:
            asyncio.get_event_loop().call_soon_threadsafe(queue.put_nowait, message)

    try:
        report_md = await run_audit(job_id, target_url, results_dir, progress_callback)
        _jobs[job_id]["report_md"] = report_md
        _jobs[job_id]["status"] = "done"
    except Exception as exc:
        _jobs[job_id]["status"] = "error"
        _jobs[job_id]["error"] = str(exc)
        if queue:
            queue.put_nowait(f"data: [ERROR] {exc}\n\n")
            queue.put_nowait("data: DONE\n\n")


@app.get("/audit/{job_id}", response_class=HTMLResponse)
async def audit_page(request: Request, job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return HTMLResponse("Audit nenájdený", status_code=404)
    return templates.TemplateResponse("audit.html", {
        "request": request,
        "job_id": job_id,
        "target_url": job["url"],
        "status": job["status"],
    })


@app.get("/audit/{job_id}/progress")
async def audit_progress(job_id: str):
    """Server-Sent Events endpoint for real-time progress."""
    queue = _sse_queues.get(job_id)
    if not queue:
        return HTMLResponse("Job nenájdený", status_code=404)

    async def event_generator():
        yield "data: Audit spustený…\n\n"
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=300)
                yield message
                if "DONE" in message:
                    break
            except asyncio.TimeoutError:
                yield "data: [TIMEOUT] Audit trvá príliš dlho\n\n"
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/audit/{job_id}/report", response_class=HTMLResponse)
async def audit_report(request: Request, job_id: str):
    job = _jobs.get(job_id)
    if not job or job["status"] != "done":
        return RedirectResponse(f"/audit/{job_id}")

    report_md_path = RESULTS_DIR / job_id / "REPORT.md"
    if not report_md_path.exists():
        return HTMLResponse("Report nenájdený", status_code=404)

    report_md = report_md_path.read_text(encoding="utf-8")

    # Convert markdown to HTML
    import markdown2
    report_html = markdown2.markdown(
        report_md,
        extras=["tables", "fenced-code-blocks", "break-on-newline"],
    )

    return templates.TemplateResponse("report.html", {
        "request": request,
        "job_id": job_id,
        "target_url": job["url"],
        "report_html": report_html,
    })


@app.get("/audit/{job_id}/download/md")
async def download_md(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return HTMLResponse("Job nenájdený", status_code=404)
    report_path = RESULTS_DIR / job_id / "REPORT.md"
    if not report_path.exists():
        return HTMLResponse("Report nenájdený", status_code=404)
    domain = urlparse(job["url"]).netloc.replace(".", "_")
    return FileResponse(
        path=str(report_path),
        media_type="text/markdown",
        filename=f"audit_{domain}.md",
    )


@app.get("/audit/{job_id}/download/pdf")
async def download_pdf(job_id: str, request: Request):
    job = _jobs.get(job_id)
    if not job:
        return HTMLResponse("Job nenájdený", status_code=404)

    report_path = RESULTS_DIR / job_id / "REPORT.md"
    if not report_path.exists():
        return HTMLResponse("Report nenájdený", status_code=404)

    pdf_path = RESULTS_DIR / job_id / "REPORT.pdf"

    if not pdf_path.exists():
        # Generate PDF using Playwright (already a dependency)
        import markdown2
        report_md = report_path.read_text(encoding="utf-8")
        report_html_content = markdown2.markdown(
            report_md,
            extras=["tables", "fenced-code-blocks", "break-on-newline"],
        )

        full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: Arial, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
  th {{ background: #f0f0f0; }}
  code {{ background: #f4f4f4; padding: 2px 6px; border-radius: 3px; }}
  pre {{ background: #f4f4f4; padding: 12px; border-radius: 5px; overflow-x: auto; }}
  h1 {{ border-bottom: 2px solid #333; padding-bottom: 0.3em; }}
  h2 {{ border-bottom: 1px solid #ccc; padding-bottom: 0.2em; margin-top: 2em; }}
  h3 {{ margin-top: 1.5em; }}
</style>
</head>
<body>
{report_html_content}
</body>
</html>"""

        from playwright.sync_api import sync_playwright
        import asyncio

        loop = asyncio.get_event_loop()

        def _generate_pdf():
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.set_content(full_html, wait_until="networkidle")
                page.pdf(path=str(pdf_path), format="A4",
                         margin={"top": "20mm", "right": "15mm",
                                 "bottom": "20mm", "left": "15mm"})
                browser.close()

        await loop.run_in_executor(None, _generate_pdf)

    domain = urlparse(job["url"]).netloc.replace(".", "_")
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"audit_{domain}.pdf",
    )
