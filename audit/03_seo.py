"""
Fáza 3 — SEO Audit
Výstup: results/<job_id>/03_seo.json
"""

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from collections import deque

import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

from audit.config import USER_AGENT, TIMEOUT, CRAWL_LIMIT

console = Console()


def check_on_page(url: str, label: str) -> dict:
    result = {"url": url, "label": label}
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT},
                         timeout=TIMEOUT, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")

        title_tag = soup.find("title")
        title = title_tag.text.strip() if title_tag else None
        result["title"] = {"text": title, "length": len(title) if title else 0,
                           "ok": 45 <= len(title) <= 65 if title else False}

        desc_tag = soup.find("meta", attrs={"name": "description"})
        desc = desc_tag.get("content", "").strip() if desc_tag else None
        result["meta_description"] = {"text": desc, "length": len(desc) if desc else 0,
                                       "ok": 120 <= len(desc) <= 160 if desc else False}

        canon = soup.find("link", rel="canonical")
        result["canonical"] = canon.get("href") if canon else None

        hreflangs = [{"lang": l.get("hreflang"), "href": l.get("href")}
                     for l in soup.find_all("link", rel="alternate")
                     if l.get("hreflang")]
        result["hreflang"] = hreflangs

        vp = soup.find("meta", attrs={"name": "viewport"})
        result["viewport"] = vp.get("content") if vp else None

        headings = {}
        for level in ["h1", "h2", "h3", "h4"]:
            tags = soup.find_all(level)
            headings[level] = [t.get_text(strip=True)[:80] for t in tags]
        result["headings"] = headings

        imgs = soup.find_all("img")
        no_alt = [i.get("src", "")[:80] for i in imgs if not i.get("alt")]
        result["images"] = {"total": len(imgs),
                            "missing_alt": len(no_alt),
                            "missing_alt_urls": no_alt[:5]}

        og = {}
        for tag in soup.find_all("meta", property=re.compile(r"^og:")):
            og[tag.get("property")] = tag.get("content")
        result["open_graph"] = og

        tc = {}
        for tag in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")}):
            tc[tag.get("name")] = tag.get("content")
        result["twitter_card"] = tc

        schemas = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
                schemas.append({"type": data.get("@type"), "raw": data})
            except Exception:
                pass
        result["json_ld"] = schemas

    except Exception as e:
        result["error"] = str(e)

    return result


def crawl_internal(base_url: str) -> dict:
    console.rule("[bold cyan]Interné linky — crawl")
    parsed_base = urlparse(base_url)
    base_host = parsed_base.netloc

    visited = set()
    queue = deque([(base_url, 0)])
    broken = []
    all_urls = []

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
                      follow_redirects=True) as client:
        while queue and len(visited) < CRAWL_LIMIT:
            url, depth = queue.popleft()
            if url in visited:
                continue
            visited.add(url)

            try:
                resp = client.get(url)
                status = resp.status_code
                all_urls.append({"url": url, "status": status, "depth": depth})

                if status == 404:
                    broken.append(url)
                    console.print(f"  [red]404[/] {url}")
                else:
                    console.print(f"  [green]{status}[/] {url[:80]}")

                if depth < 2 and status == 200:
                    soup = BeautifulSoup(resp.text, "lxml")
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        full = urljoin(url, href).split("#")[0].split("?")[0]
                        if urlparse(full).netloc == base_host and full not in visited:
                            queue.append((full, depth + 1))
            except Exception as e:
                all_urls.append({"url": url, "error": str(e), "depth": depth})

    console.print(f"\n  Navštívené: {len(visited)}, Broken (404): {len(broken)}")
    return {"crawled": len(visited), "broken_count": len(broken),
            "broken_urls": broken, "sample": all_urls[:20]}


def main(target_url: str, results_dir: Path) -> dict:
    console.rule("[bold]Fáza 3 — SEO Audit")

    pages_to_check = [(target_url, "Homepage")]

    try:
        resp = httpx.get(target_url, headers={"User-Agent": USER_AGENT},
                         timeout=TIMEOUT, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")
        base_host = urlparse(target_url).netloc
        for a in soup.find_all("a", href=True):
            href = urljoin(target_url, a["href"])
            if urlparse(href).netloc == base_host and href != target_url:
                pages_to_check.append((href, "Podstránka"))
                break
    except Exception:
        pass

    results = {"pages": [], "crawl": {}}

    console.rule("[bold cyan]On-page analýza")
    for url, label in pages_to_check:
        console.print(f"\n[bold]{label}:[/] {url}")
        data = check_on_page(url, label)
        results["pages"].append(data)

        t = Table("Parameter", "Hodnota", "OK")
        t.add_row("Title", (data.get("title") or {}).get("text", "—")[:50],
                  "✅" if (data.get("title") or {}).get("ok") else "❌")
        t.add_row("Meta desc len",
                  str((data.get("meta_description") or {}).get("length", 0)),
                  "✅" if (data.get("meta_description") or {}).get("ok") else "❌")
        t.add_row("Canonical", str(data.get("canonical") or "—")[:50], "")
        t.add_row("Viewport", str(data.get("viewport") or "❌"), "")
        t.add_row("H1 count", str(len((data.get("headings") or {}).get("h1", []))),
                  "✅" if len((data.get("headings") or {}).get("h1", [])) == 1 else "⚠️")
        imgs = data.get("images") or {}
        t.add_row("Imgs bez alt", f"{imgs.get('missing_alt', 0)}/{imgs.get('total', 0)}", "")
        t.add_row("Open Graph", "áno" if data.get("open_graph") else "❌", "")
        t.add_row("JSON-LD schemas",
                  str([s.get("type") for s in data.get("json_ld", [])]), "")
        console.print(t)

    results["crawl"] = crawl_internal(target_url)

    out_file = results_dir / "03_seo.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    console.print(f"\n[bold green]✓ Výsledok uložený:[/] {out_file}")
    return results


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else input("URL: ")
    main(url, Path("results"))
