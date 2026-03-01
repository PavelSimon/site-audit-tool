"""
Fáza 2 — Performance Audit
Výstup: results/<job_id>/02_performance.json
"""

import json
import time
import statistics
import urllib.parse
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.table import Table

from audit.config import USER_AGENT, TIMEOUT

console = Console()


CWV_JS = """
() => new Promise(resolve => {
    const result = { lcp: null, cls: 0, fcp: null, inp: null };
    const timing = performance.getEntriesByType('navigation')[0] || {};

    const paintEntries = performance.getEntriesByType('paint');
    for (const e of paintEntries) {
        if (e.name === 'first-contentful-paint') result.fcp = e.startTime;
    }

    new PerformanceObserver(list => {
        const entries = list.getEntries();
        if (entries.length) result.lcp = entries[entries.length - 1].startTime;
    }).observe({ type: 'largest-contentful-paint', buffered: true });

    new PerformanceObserver(list => {
        for (const e of list.getEntries()) {
            if (!e.hadRecentInput) result.cls += e.value;
        }
    }).observe({ type: 'layout-shift', buffered: true });

    setTimeout(() => {
        result.ttfb = timing.responseStart || null;
        result.dom_content_loaded = timing.domContentLoadedEventEnd || null;
        result.load = timing.loadEventEnd || null;
        result.cls = parseFloat(result.cls.toFixed(4));
        resolve(result);
    }, 3000);
})
"""

RENDER_BLOCKING_JS = """
() => {
    const blocking = [];
    for (const link of document.querySelectorAll('link[rel="stylesheet"]')) {
        if (!link.media || link.media === 'all') blocking.push({type:'css', url: link.href});
    }
    for (const script of document.querySelectorAll('script[src]')) {
        if (!script.async && !script.defer) blocking.push({type:'js', url: script.src});
    }
    return blocking;
}
"""

IMAGES_JS = """
() => Array.from(document.querySelectorAll('img')).slice(0, 30).map(img => ({
    src: img.src,
    alt: img.alt,
    loading: img.loading,
    hasWidthHeight: img.hasAttribute('width') && img.hasAttribute('height'),
    naturalWidth: img.naturalWidth,
    format: img.src.split('?')[0].split('.').pop().toLowerCase()
}))
"""


def measure_page(browser, url: str, label: str, base_host: str, runs: int = 3) -> dict:
    console.print(f"\n  [bold]{label}[/] ({url})")
    run_results = []

    for i in range(runs):
        resources = []
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            java_script_enabled=True,
        )
        page = context.new_page()
        page.on("response", lambda r: resources.append({
            "url": r.url,
            "status": r.status,
            "type": r.request.resource_type,
            "size": int(r.headers.get("content-length", 0)),
        }))

        t0 = time.perf_counter()
        page.goto(url, wait_until="networkidle", timeout=30000)
        wall_ms = (time.perf_counter() - t0) * 1000

        cwv = page.evaluate(CWV_JS)
        blocking = page.evaluate(RENDER_BLOCKING_JS)
        images = page.evaluate(IMAGES_JS) if i == 0 else []

        run_results.append({
            "wall_ms": round(wall_ms, 1),
            "lcp": round(cwv.get("lcp") or 0, 1),
            "cls": cwv.get("cls", 0),
            "fcp": round(cwv.get("fcp") or 0, 1),
            "ttfb": round(cwv.get("ttfb") or 0, 1),
            "dom_content_loaded": round(cwv.get("dom_content_loaded") or 0, 1),
            "load": round(cwv.get("load") or 0, 1),
        })
        console.print(f"    Run {i+1}: wall={wall_ms:.0f}ms  LCP={cwv.get('lcp', 0) or 0:.0f}ms  "
                      f"CLS={cwv.get('cls', 0):.4f}  FCP={cwv.get('fcp', 0) or 0:.0f}ms")
        context.close()

    def med(key):
        vals = [r[key] for r in run_results if r[key] is not None]
        return round(statistics.median(vals), 1) if vals else None

    median = {k: med(k) for k in run_results[0]}

    by_type = {}
    third_party = set()
    for r in resources:
        rtype = r["type"]
        by_type.setdefault(rtype, {"count": 0, "bytes": 0})
        by_type[rtype]["count"] += 1
        by_type[rtype]["bytes"] += r["size"]
        host = urllib.parse.urlparse(r["url"]).hostname or ""
        if host and host != base_host and not host.endswith("." + base_host):
            third_party.add(host)

    return {
        "url": url,
        "label": label,
        "runs": run_results,
        "median": median,
        "resources": by_type,
        "third_party_domains": sorted(third_party),
        "render_blocking": blocking,
        "images": images,
    }


def check_cache_compression(url: str) -> dict:
    console.rule("[bold cyan]Cache & Compression headers")
    result = {}
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT,
                                        "Accept-Encoding": "br, gzip, deflate"},
                         timeout=TIMEOUT, follow_redirects=True)
        h = resp.headers
        result = {
            "content-encoding": h.get("content-encoding"),
            "cache-control": h.get("cache-control"),
            "etag": h.get("etag"),
            "last-modified": h.get("last-modified"),
            "expires": h.get("expires"),
            "vary": h.get("vary"),
        }
        for k, v in result.items():
            color = "green" if v else "red"
            console.print(f"  [{color}]{k}:[/] {v or '—'}")
    except Exception as e:
        result["error"] = str(e)
    return result


def discover_pages(base_url: str) -> list[dict]:
    pages = [{"label": "Homepage", "url": base_url}]
    try:
        resp = httpx.get(base_url, headers={"User-Agent": USER_AGENT},
                         timeout=TIMEOUT, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(base_url) and href != base_url and len(href) > len(base_url) + 1:
                pages.append({"label": "Kategória", "url": href})
                break

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(base_url) and any(x in href for x in ["/p/", "/product", ".html"]):
                pages.append({"label": "Produkt", "url": href})
                break
    except Exception:
        pass
    return pages[:3]


def main(target_url: str, results_dir: Path) -> dict:
    console.rule("[bold]Fáza 2 — Performance Audit")

    base_host = urllib.parse.urlparse(target_url).hostname
    cache = check_cache_compression(target_url)
    pages = discover_pages(target_url)
    console.print(f"\nMerané stránky: {[p['url'] for p in pages]}")

    results = {"cache_compression": cache, "pages": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for page_info in pages:
            data = measure_page(browser, page_info["url"], page_info["label"], base_host)
            results["pages"].append(data)
        browser.close()

    console.rule("[bold cyan]Súhrn výsledkov")
    t = Table("Stránka", "Wall ms", "TTFB ms", "FCP ms", "LCP ms", "CLS",
              "Requests", "3rd-party")
    for p in results["pages"]:
        m = p["median"]
        total_req = sum(v["count"] for v in p["resources"].values())
        t.add_row(
            p["label"],
            str(m.get("wall_ms")),
            str(m.get("ttfb")),
            str(m.get("fcp")),
            str(m.get("lcp")),
            str(m.get("cls")),
            str(total_req),
            str(len(p["third_party_domains"])),
        )
    console.print(t)

    out_file = results_dir / "02_performance.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    console.print(f"\n[bold green]✓ Výsledok uložený:[/] {out_file}")
    return results


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else input("URL: ")
    main(url, Path("results"))
