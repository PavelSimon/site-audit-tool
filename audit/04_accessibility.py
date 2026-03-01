"""
Fáza 4 — Accessibility Audit (WCAG 2.1 AA)
Výstup: results/<job_id>/04_accessibility.json
"""

import json
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.table import Table

from audit.config import USER_AGENT, TIMEOUT

console = Console()

AXE_RUN_JS = """
async () => {
    const results = await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'best-practice']}
    });
    return {
        violations: results.violations.map(v => ({
            id: v.id,
            impact: v.impact,
            description: v.description,
            helpUrl: v.helpUrl,
            nodes_count: v.nodes.length,
            nodes_sample: v.nodes.slice(0, 2).map(n => n.html)
        })),
        passes: results.passes.length,
        incomplete: results.incomplete.length,
        inapplicable: results.inapplicable.length
    };
}
"""

_AXE_SOURCE: str | None = None


def _get_axe_source() -> str:
    global _AXE_SOURCE
    if _AXE_SOURCE is None:
        console.print("  Sťahujem axe-core…")
        resp = httpx.get(
            "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js",
            timeout=30,
        )
        _AXE_SOURCE = resp.text
    return _AXE_SOURCE


def run_axe(page, url: str, label: str) -> dict:
    console.print(f"\n  [bold]axe-core:[/] {label} ({url})")
    page.goto(url, wait_until="networkidle", timeout=30000)
    page.add_script_tag(content=_get_axe_source())
    page.wait_for_function("() => typeof axe !== 'undefined'", timeout=10000)
    result = page.evaluate(AXE_RUN_JS)

    violations = result.get("violations", [])
    critical = [v for v in violations if v["impact"] == "critical"]
    serious = [v for v in violations if v["impact"] == "serious"]
    moderate = [v for v in violations if v["impact"] == "moderate"]
    minor = [v for v in violations if v["impact"] == "minor"]

    console.print(f"    Passes: {result.get('passes')}  "
                  f"Violations: {len(violations)} "
                  f"(critical={len(critical)}, serious={len(serious)}, "
                  f"moderate={len(moderate)}, minor={len(minor)})")

    for v in violations:
        color = {"critical": "red", "serious": "yellow",
                 "moderate": "cyan", "minor": "dim"}.get(v["impact"], "white")
        console.print(f"    [{color}][{v['impact']}][/] {v['id']}: {v['description'][:80]}")

    return {
        "url": url, "label": label,
        "violations": violations,
        "passes": result.get("passes"),
        "incomplete": result.get("incomplete"),
        "summary": {"critical": len(critical), "serious": len(serious),
                     "moderate": len(moderate), "minor": len(minor)},
    }


def manual_checks(url: str, label: str) -> dict:
    console.print(f"\n  [bold]Manuálne kontroly:[/] {label}")
    result = {"url": url, "label": label}
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT},
                         timeout=TIMEOUT, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")

        html_tag = soup.find("html")
        lang = html_tag.get("lang") if html_tag else None
        result["html_lang"] = lang
        console.print(f"    html lang: {lang or '❌ chýba'}")

        skip_links = [a for a in soup.find_all("a", href=True)
                      if a.get("href", "").startswith("#")
                      and any(w in a.text.lower() for w in
                              ["skip", "obsah", "content", "main", "prejsť"])]
        result["skip_nav"] = len(skip_links) > 0
        console.print(f"    skip nav: {'✅' if result['skip_nav'] else '❌ chýba'}")

        inputs = soup.find_all("input", type=lambda t: t not in ["hidden", "submit", "button"])
        unlabeled = []
        for inp in inputs:
            inp_id = inp.get("id")
            has_label = (soup.find("label", attrs={"for": inp_id}) is not None) if inp_id else False
            has_aria = bool(inp.get("aria-label") or inp.get("aria-labelledby"))
            if not has_label and not has_aria:
                unlabeled.append(inp.get("name") or inp.get("id") or inp.get("type"))
        result["unlabeled_inputs"] = unlabeled
        console.print(f"    inputs bez label: {len(unlabeled)} — {unlabeled[:5]}")

        bad_buttons = []
        for btn in soup.find_all(["button", "a"]):
            text = btn.get_text(strip=True)
            aria = btn.get("aria-label") or btn.get("aria-labelledby") or btn.get("title")
            if not text and not aria:
                bad_buttons.append(str(btn)[:60])
        result["buttons_no_text"] = bad_buttons[:5]
        console.print(f"    tlačidlá bez textu/aria: {len(bad_buttons)}")

        css_links = [l["href"] for l in soup.find_all("link", rel="stylesheet") if l.get("href")]
        focus_in_css = False
        for css_url in css_links[:3]:
            full_css = urljoin(url, css_url)
            try:
                cr = httpx.get(full_css, headers={"User-Agent": USER_AGENT}, timeout=5)
                if ":focus" in cr.text or "focus-visible" in cr.text:
                    focus_in_css = True
                    break
            except Exception:
                pass
        result["focus_styles_in_css"] = focus_in_css
        console.print(f"    :focus v CSS: {'✅' if focus_in_css else '⚠️ nenájdené'}")

        inline_colors = soup.find_all(style=lambda s: s and "color" in s)
        result["inline_color_styles_count"] = len(inline_colors)

    except Exception as e:
        result["error"] = str(e)

    return result


def main(target_url: str, results_dir: Path) -> dict:
    console.rule("[bold]Fáza 4 — Accessibility Audit (WCAG 2.1 AA)")

    pages = [target_url]
    try:
        resp = httpx.get(target_url, headers={"User-Agent": USER_AGENT},
                         timeout=TIMEOUT, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")
        base_host = urlparse(target_url).netloc
        for a in soup.find_all("a", href=True):
            href = urljoin(target_url, a["href"])
            if urlparse(href).netloc == base_host and href.rstrip("/") != target_url.rstrip("/"):
                pages.append(href)
                break
    except Exception:
        pass

    results = {"axe": [], "manual": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT, bypass_csp=True)
        page = context.new_page()
        labels = ["Homepage", "Podstránka"]
        for i, url in enumerate(pages):
            label = labels[i] if i < len(labels) else f"Stránka {i+1}"
            results["axe"].append(run_axe(page, url, label))
        browser.close()

    for i, url in enumerate(pages):
        label = labels[i] if i < len(labels) else f"Stránka {i+1}"
        results["manual"].append(manual_checks(url, label))

    console.rule("[bold cyan]Súhrn violations")
    t = Table("Stránka", "Critical", "Serious", "Moderate", "Minor", "Passes")
    for r in results["axe"]:
        s = r["summary"]
        t.add_row(r["label"],
                  f"[red]{s['critical']}[/]", f"[yellow]{s['serious']}[/]",
                  f"[cyan]{s['moderate']}[/]", str(s["minor"]), str(r["passes"]))
    console.print(t)

    out_file = results_dir / "04_accessibility.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    console.print(f"\n[bold green]✓ Výsledok uložený:[/] {out_file}")
    return results


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else input("URL: ")
    main(url, Path("results"))
