"""
Fáza 1 — Discovery & Reconnaissance
Výstup: results/<job_id>/01_discovery.json
"""

import json
import time
import random
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import dns.resolver
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

from audit.config import USER_AGENT, TIMEOUT, SAMPLE_URLS

console = Console()


# ---------------------------------------------------------------------------
# 1. HTTP HEAD — redirect chain, headers, TTFB
# ---------------------------------------------------------------------------
def check_http(url: str) -> dict:
    console.rule("[bold cyan]1. HTTP / Redirect chain")
    result = {"url": url, "redirect_chain": [], "final_url": None,
              "status_code": None, "ttfb_ms": None, "headers": {}}

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
                      follow_redirects=True) as client:
        t0 = time.perf_counter()
        resp = client.get(url)
        ttfb = (time.perf_counter() - t0) * 1000

        for r in resp.history:
            hop = {"url": str(r.url), "status": r.status_code}
            result["redirect_chain"].append(hop)
            console.print(f"  [yellow]{r.status_code}[/] {r.url} → ")

        result["final_url"] = str(resp.url)
        result["status_code"] = resp.status_code
        result["ttfb_ms"] = round(ttfb, 1)
        result["headers"] = dict(resp.headers)

        console.print(f"  [green]{resp.status_code}[/] {resp.url}  (TTFB {ttfb:.0f} ms)")

    interesting = ["server", "x-powered-by", "content-encoding", "content-type",
                   "x-generator", "x-drupal-cache", "x-varnish", "via", "cf-ray",
                   "x-cache", "x-shopify-stage"]
    t = Table("Header", "Value")
    for h in interesting:
        if h in result["headers"]:
            t.add_row(h, result["headers"][h])
    console.print(t)
    return result


# ---------------------------------------------------------------------------
# 2. DNS analýza
# ---------------------------------------------------------------------------
def check_dns(hostname: str) -> dict:
    console.rule("[bold cyan]2. DNS")
    result = {}
    record_types = ["A", "AAAA", "MX", "NS", "TXT"]

    for rtype in record_types:
        try:
            answers = dns.resolver.resolve(hostname, rtype, lifetime=10)
            records = []
            for rdata in answers:
                records.append({"value": rdata.to_text(), "ttl": answers.ttl})
            result[rtype] = records
            console.print(f"  [green]{rtype}[/]: {[r['value'] for r in records]}")
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN,
                dns.resolver.NoNameservers, dns.exception.Timeout):
            result[rtype] = []
            console.print(f"  [dim]{rtype}: (none)[/]")

    return result


# ---------------------------------------------------------------------------
# 3. robots.txt
# ---------------------------------------------------------------------------
def check_robots(base_url: str) -> dict:
    console.rule("[bold cyan]3. robots.txt")
    robots_url = urljoin(base_url, "/robots.txt")
    result = {"url": robots_url, "status_code": None, "content": None,
              "disallow_paths": [], "sitemap_urls": []}

    try:
        resp = httpx.get(robots_url, headers={"User-Agent": USER_AGENT},
                         timeout=TIMEOUT, follow_redirects=True)
        result["status_code"] = resp.status_code
        if resp.status_code == 200:
            result["content"] = resp.text
            for line in resp.text.splitlines():
                line = line.strip()
                if line.lower().startswith("disallow:"):
                    path = line.split(":", 1)[1].strip()
                    if path:
                        result["disallow_paths"].append(path)
                elif line.lower().startswith("sitemap:"):
                    result["sitemap_urls"].append(line.split(":", 1)[1].strip())
            console.print(f"  Disallow paths: {len(result['disallow_paths'])}")
            console.print(f"  Sitemap refs: {result['sitemap_urls']}")
        else:
            console.print(f"  [red]robots.txt status: {resp.status_code}[/]")
    except Exception as e:
        result["error"] = str(e)
        console.print(f"  [red]Error: {e}[/]")

    return result


# ---------------------------------------------------------------------------
# 4. sitemap.xml
# ---------------------------------------------------------------------------
def check_sitemap(base_url: str, extra_sitemap_urls: list[str]) -> dict:
    console.rule("[bold cyan]4. sitemap.xml")
    candidates = list(dict.fromkeys(
        [urljoin(base_url, "/sitemap.xml"),
         urljoin(base_url, "/sitemap_index.xml")] + extra_sitemap_urls
    ))
    result = {"found": False, "url": None, "total_urls": 0,
              "sample_status": [], "lastmod_dates": []}

    for smap_url in candidates:
        try:
            resp = httpx.get(smap_url, headers={"User-Agent": USER_AGENT},
                             timeout=TIMEOUT, follow_redirects=True)
            if resp.status_code == 200 and ("xml" in resp.headers.get("content-type", "")
                                             or resp.text.strip().startswith("<")):
                result["found"] = True
                result["url"] = smap_url
                soup = BeautifulSoup(resp.text, "xml")

                child_sitemaps = soup.find_all("sitemap")
                if child_sitemaps:
                    console.print(f"  Sitemap index with {len(child_sitemaps)} child sitemaps")
                    result["is_index"] = True
                    result["child_sitemaps"] = [s.find("loc").text for s in child_sitemaps
                                                if s.find("loc")]
                    if result["child_sitemaps"]:
                        child_resp = httpx.get(result["child_sitemaps"][0],
                                               headers={"User-Agent": USER_AGENT},
                                               timeout=TIMEOUT, follow_redirects=True)
                        child_soup = BeautifulSoup(child_resp.text, "xml")
                        urls = child_soup.find_all("url")
                        result["total_urls"] = len(urls)
                        console.print(f"  First child has {len(urls)} URLs")
                else:
                    urls = soup.find_all("url")
                    result["total_urls"] = len(urls)
                    locs = [u.find("loc").text for u in urls if u.find("loc")]
                    lastmods = [u.find("lastmod").text for u in urls if u.find("lastmod")]
                    result["lastmod_dates"] = lastmods[:5]

                    sample = random.sample(locs, min(SAMPLE_URLS, len(locs)))
                    console.print(f"  Total URLs: {len(locs)}, sampling {len(sample)}…")
                    for loc in sample:
                        try:
                            sr = httpx.head(loc, headers={"User-Agent": USER_AGENT},
                                            timeout=TIMEOUT, follow_redirects=True)
                            result["sample_status"].append(
                                {"url": loc, "status": sr.status_code})
                            color = "green" if sr.status_code == 200 else "red"
                            console.print(f"    [{color}]{sr.status_code}[/] {loc}")
                        except Exception as e:
                            result["sample_status"].append({"url": loc, "error": str(e)})
                break
        except Exception:
            continue

    if not result["found"]:
        console.print("  [red]sitemap.xml not found[/]")
    return result


# ---------------------------------------------------------------------------
# 5. Technology fingerprinting
# ---------------------------------------------------------------------------
def fingerprint_tech(http_result: dict, base_url: str) -> dict:
    console.rule("[bold cyan]5. Technology Fingerprinting")
    result = {"cms": None, "js_frameworks": [], "server": None,
              "cdn": None, "payment": [], "cookies": [], "meta_generator": None}

    headers = http_result.get("headers", {})
    result["server"] = headers.get("server", headers.get("x-powered-by", "unknown"))

    cdn_signals = {
        "cf-ray": "Cloudflare",
        "x-amz-cf-id": "AWS CloudFront",
        "x-served-by": "Fastly",
        "x-cache": "Varnish/CDN",
        "x-vercel-id": "Vercel",
    }
    for header, cdn_name in cdn_signals.items():
        if header in headers:
            result["cdn"] = cdn_name
            break

    try:
        resp = httpx.get(base_url, headers={"User-Agent": USER_AGENT},
                         timeout=TIMEOUT, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")

        gen = soup.find("meta", attrs={"name": "generator"})
        if gen:
            result["meta_generator"] = gen.get("content", "")

        html = resp.text.lower()

        cms_signals = {
            "WordPress / WooCommerce": ["wp-content/", "wp-includes/", "woocommerce"],
            "Magento": ["mage/", "magento", "varien"],
            "Shopify": ["cdn.shopify.com", "shopify.com/s/files"],
            "PrestaShop": ["prestashop", "/modules/", "id_product"],
            "OpenCart": ["opencart", "route=common"],
            "Drupal": ["drupal", "sites/default/files"],
        }
        for cms, signals in cms_signals.items():
            if any(s in html for s in signals):
                result["cms"] = cms
                break

        js_signals = {
            "React": ["react", "reactdom", "__react"],
            "Vue.js": ["vue.js", "vue.min.js", "__vue__"],
            "jQuery": ["jquery"],
            "Angular": ["angular", "ng-version"],
            "Next.js": ["__next", "_next/static"],
            "Nuxt.js": ["__nuxt", "_nuxt/"],
        }
        for fw, signals in js_signals.items():
            if any(s in html for s in signals):
                result["js_frameworks"].append(fw)

        payment_signals = {
            "Stripe": ["stripe.com", "js.stripe.com"],
            "PayPal": ["paypal.com"],
            "GoPay": ["gopay.com", "gopay.cz"],
            "Comgate": ["comgate.cz"],
            "TrustPay": ["trustpay"],
            "Barion": ["barion.com"],
        }
        for gw, signals in payment_signals.items():
            if any(s in html for s in signals):
                result["payment"].append(gw)

        set_cookie = headers.get("set-cookie", "")
        if set_cookie:
            cookie_names = re.findall(r'([a-zA-Z0-9_\-]+)=', set_cookie)
            result["cookies"] = cookie_names[:10]

    except Exception as e:
        result["error"] = str(e)

    t = Table("Signal", "Value")
    for k, v in result.items():
        if v:
            t.add_row(k, str(v))
    console.print(t)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(target_url: str, results_dir: Path) -> dict:
    console.print(f"\n[bold]Audit target:[/] {target_url}\n")
    parsed = urlparse(target_url)
    hostname = parsed.hostname

    output = {}
    output["http"] = check_http(target_url)
    output["dns"] = check_dns(hostname)
    output["robots"] = check_robots(target_url)

    extra_sitemaps = output["robots"].get("sitemap_urls", [])
    output["sitemap"] = check_sitemap(target_url, extra_sitemaps)
    output["tech"] = fingerprint_tech(output["http"], target_url)

    out_file = results_dir / "01_discovery.json"
    out_file.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    console.print(f"\n[bold green]✓ Výsledok uložený:[/] {out_file}")
    return output


if __name__ == "__main__":
    import sys
    from pathlib import Path as P
    url = sys.argv[1] if len(sys.argv) > 1 else input("URL: ")
    main(url, P("results"))
