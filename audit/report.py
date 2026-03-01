"""
Fáza 8 — Súhrnný report
Načíta JSON výsledky fáz 1–7 a vygeneruje REPORT.md
"""

import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

class Score:
    def __init__(self, name: str):
        self.name = name
        self.points = 0
        self.max_points = 0
        self.items: list[dict] = []

    def add(self, label: str, value: bool, weight: int, note: str = ""):
        self.max_points += weight
        if value:
            self.points += weight
        self.items.append({"label": label, "ok": value, "weight": weight, "note": note})

    @property
    def pct(self) -> int:
        return round(self.points / self.max_points * 100) if self.max_points else 0

    @property
    def grade(self) -> str:
        p = self.pct
        if p >= 90: return "🟢 Výborné"
        if p >= 70: return "🟡 Dobré"
        if p >= 50: return "🟠 Potrebuje zlepšenie"
        return "🔴 Kritické"


def load(results_dir: Path, fname: str) -> dict:
    f = results_dir / fname
    return json.loads(f.read_text()) if f.exists() else {}


# ---------------------------------------------------------------------------
# Per-category scoring
# ---------------------------------------------------------------------------

def score_infrastructure(d1: dict) -> Score:
    s = Score("Infraštruktúra")
    http = d1.get("http", {})
    dns = d1.get("dns", {})
    s.add("HTTPS (žiadny HTTP redirect)", http.get("status_code") == 200, 10)
    s.add("HTTP/3 (QUIC) podpora", "h3" in http.get("headers", {}).get("alt-svc", ""), 5)
    s.add("TTFB < 800 ms", (http.get("ttfb_ms") or 9999) < 800, 10)
    s.add("robots.txt existuje", d1.get("robots", {}).get("status_code") == 200, 15)
    s.add("sitemap.xml existuje", d1.get("sitemap", {}).get("found", False), 15)
    s.add("DNS SPF záznam", len(dns.get("TXT", [])) > 0, 10)
    s.add("DNS DMARC záznam", any("dmarc" in str(r).lower() for r in dns.get("TXT", [])), 10)
    s.add("IPv6 (AAAA záznam)", len(dns.get("AAAA", [])) > 0, 5)
    s.add("CDN detekovaný", bool(d1.get("tech", {}).get("cdn")), 10)
    s.add("Kompresia (gzip/br)", bool(http.get("headers", {}).get("content-encoding")), 10)
    return s


def score_performance(d2: dict) -> Score:
    s = Score("Performance")
    pages = d2.get("pages", [])
    med = pages[0].get("median", {}) if pages else {}
    cache = d2.get("cache_compression", {})
    blocking = pages[0].get("render_blocking", []) if pages else []
    third = pages[0].get("third_party_domains", []) if pages else []

    lcp = med.get("lcp") or 9999
    cls = med.get("cls") if med.get("cls") is not None else 1
    fcp = med.get("fcp") or 9999
    ttfb = med.get("ttfb") or 9999

    s.add("LCP ≤ 2 500 ms", lcp <= 2500, 20, f"{lcp} ms")
    s.add("LCP ≤ 1 200 ms (výborný)", lcp <= 1200, 5, f"{lcp} ms")
    s.add("CLS ≤ 0.1", cls <= 0.1, 20, str(cls))
    s.add("FCP ≤ 1 800 ms", fcp <= 1800, 15, f"{fcp} ms")
    s.add("TTFB ≤ 800 ms", ttfb <= 800, 10, f"{ttfb} ms")
    s.add("Cache-Control header", bool(cache.get("cache-control")), 15)
    s.add("Bez render-blocking resources", len(blocking) == 0, 10, f"{len(blocking)} blokujúcich")
    s.add("Žiadne third-party domény", len(third) == 0, 5, f"{len(third)} domén")
    return s


def score_seo(d3: dict) -> Score:
    s = Score("SEO")
    pages = d3.get("pages", [])
    crawl = d3.get("crawl", {})
    hp = pages[0] if pages else {}
    sub = pages[1] if len(pages) > 1 else {}

    s.add("Title (homepage) — správna dĺžka", (hp.get("title") or {}).get("ok", False), 8)
    s.add("Meta description (homepage) — správna dĺžka",
          (hp.get("meta_description") or {}).get("ok", False), 8)
    s.add("Canonical (homepage)", bool(hp.get("canonical")), 5)
    s.add("Meta description (podstránka)", (sub.get("meta_description") or {}).get("ok", False), 6)
    s.add("Canonical (podstránka)", bool(sub.get("canonical")), 5)
    s.add("robots.txt existuje", False, 12, "viď infraštruktúra")
    s.add("sitemap.xml existuje", False, 12, "viď infraštruktúra")
    s.add("JSON-LD štruktúrované dáta", len(hp.get("json_ld", [])) > 0, 10)
    s.add("Open Graph kompletný (vrátane og:image)",
          bool((hp.get("open_graph") or {}).get("og:image")), 8)
    s.add("Twitter Card", bool(hp.get("twitter_card")), 5)
    imgs = hp.get("images") or {}
    s.add("Všetky obrázky majú alt", imgs.get("missing_alt", 1) == 0, 8,
          f"{imgs.get('missing_alt', 0)}/{imgs.get('total', 0)} bez alt")
    s.add("Žiadne broken linky (404)", crawl.get("broken_count", 1) == 0, 8)
    h1s = (hp.get("headings") or {}).get("h1", [])
    s.add("Práve 1× H1 na homepage", len(h1s) == 1, 5)
    return s


def score_accessibility(d4: dict) -> Score:
    s = Score("Accessibility (WCAG 2.1 AA)")
    axe = d4.get("axe", [])
    manual = d4.get("manual", [])

    total_violations = sum(len(p.get("violations", [])) for p in axe)
    critical = sum(p.get("summary", {}).get("critical", 0) for p in axe)
    serious = sum(p.get("summary", {}).get("serious", 0) for p in axe)

    s.add("Žiadne critical violations (axe-core)", critical == 0, 25, f"{critical} critical")
    s.add("Žiadne serious violations (axe-core)", serious == 0, 20, f"{serious} serious")
    s.add("Žiadne violations celkom", total_violations == 0, 10, f"{total_violations} violations")

    m0 = manual[0] if manual else {}
    s.add("html lang atribút", bool(m0.get("html_lang")), 15)
    s.add("Formuláre majú <label>", len(m0.get("unlabeled_inputs", ["x"])) == 0, 10)
    s.add("Skip navigation link", m0.get("skip_nav", False), 10)
    s.add(":focus štýly v CSS", m0.get("focus_styles_in_css", False), 10)
    return s


def score_security(d5: dict) -> Score:
    s = Score("Security")
    tls = d5.get("tls", {})
    headers = d5.get("headers", {}).get("headers", {})
    sensitive = d5.get("sensitive_files", {}).get("probes", [])

    protos = tls.get("protocols", {})
    cert = tls.get("certificate", {})
    s.add("TLS 1.3 podporovaný", protos.get("tls_1_3", False), 10)
    s.add("TLS 1.2 podporovaný", protos.get("tls_1_2", False), 5)
    s.add("Staré protokoly vypnuté",
          not any(protos.get(p, False) for p in ["ssl_2_0", "ssl_3_0", "tls_1_0", "tls_1_1"]), 10)
    s.add("Certifikát platný (chain valid)", cert.get("chain_valid", False), 10)
    s.add("Heartbleed — nie je zraniteľný", not tls.get("heartbleed", True), 5)
    s.add("ROBOT — nie je zraniteľný", not tls.get("robot", True), 5)

    for hdr in ["strict-transport-security", "content-security-policy",
                "x-frame-options", "x-content-type-options",
                "referrer-policy", "permissions-policy"]:
        ok = headers.get(hdr, {}).get("ok", False)
        s.add(f"Header: {hdr}", ok, 5)

    s.add("Server / X-Powered-By skrytý",
          headers.get("server", {}).get("ok", False) and
          headers.get("x-powered-by", {}).get("ok", False), 5)

    no_sensitive = all(not p.get("exposed", False) for p in sensitive)
    exposed = [p["path"] for p in sensitive if p.get("exposed")]
    s.add("Žiadne citlivé súbory prístupné", no_sensitive, 10,
          f"exposed: {exposed}" if exposed else "")

    s.add("Žiadny mixed content",
          len(d5.get("mixed_content", {}).get("http_resources", [])) == 0, 5)
    s.add("Žiadne cookies bez Secure/HttpOnly",
          len(d5.get("cookies", {}).get("cookies", [])) == 0, 5)
    return s


def score_compliance(d6: dict) -> Score:
    s = Score("GDPR & Compliance")

    def count(section):
        items = d6.get(section, [])
        auto = [i for i in items if i["result"] != "manual_review_required"]
        return sum(1 for i in auto if i["result"] is True), len(auto)

    gdpr_p, gdpr_t = count("gdpr_privacy")
    s.add(f"GDPR Privacy Policy ({gdpr_p}/{gdpr_t} kontrol)", gdpr_p == gdpr_t, 25,
          f"{gdpr_p}/{gdpr_t}")

    cc = d6.get("cookie_consent", [])

    def cc_check(label_fragment):
        return next((i["result"] for i in cc if label_fragment in i["label"]), False)

    s.add("Cookie banner prítomný", cc_check("C1") is True, 10)
    s.add("Žiadne cookies pred súhlasom", cc_check("C2") is True, 15)
    s.add("Granulárny súhlas (kategórie)", cc_check("C3") is True, 10)
    s.add("Tlačidlo 'Odmietnuť všetko'", cc_check("C5") is True, 10)
    s.add("Zoznam cookies dostupný", cc_check("C7") is True, 5)

    ec = d6.get("ecommerce_law", [])
    s.add("Email / telefón kontakt na stránke",
          any(i["result"] is True for i in ec if "E2" in i["label"]), 5)

    cr = d6.get("consumer_rights", [])
    s.add("ODR platforma EÚ odkaz",
          next((i["result"] for i in cr if "S7" in i["label"]), False) is True, 5)
    s.add("RSO / ADR zmienené",
          next((i["result"] for i in cr if "S6" in i["label"]), False) is True, 5)

    dsa = d6.get("dsa", [])
    s.add("Podmienky používania (ToS)",
          next((i["result"] for i in dsa if "D1" in i["label"]), False) is True, 5)

    trackers = d6.get("trackers", {})
    s.add("Žiadne third-party trackery", len(trackers.get("trackers", [])) == 0, 5)
    return s


def score_load(d7: dict) -> Score:
    s = Score("Load Test")
    scenarios = d7.get("scenarios", [])

    for sc in scenarios:
        agg = sc.get("aggregated") or {}
        name = sc["scenario"]["name"]
        p95 = agg.get("p95_ms", 9999)
        err = agg.get("failure_rate_pct", 100)
        s.add(f"{name}: p95 < 1 000 ms", p95 < 1000, 15, f"p95={p95:.0f} ms")
        s.add(f"{name}: error rate = 0 %", err == 0.0, 10, f"err={err}%")

    if scenarios:
        medium = next((sc for sc in scenarios if sc["scenario"]["name"] == "medium"), None)
        if medium:
            agg = medium.get("aggregated") or {}
            s.add("20 VU bez degradácie (p95 < 200 ms)",
                  (agg.get("p95_ms") or 9999) < 200, 10,
                  f"p95={agg.get('p95_ms', 0):.0f} ms")
    return s


# ---------------------------------------------------------------------------
# Dynamic findings generator (replaces hardcoded ET.sk summaries)
# ---------------------------------------------------------------------------

FINDINGS: list[dict] = []


def finding(priority: str, category: str, title: str, detail: str = ""):
    FINDINGS.append({"priority": priority, "category": category,
                     "title": title, "detail": detail})


def collect_findings(d1, d2, d3, d4, d5, d6, d7):
    # Infra
    if not d1.get("robots", {}).get("status_code") == 200:
        finding("kritické", "SEO / Infra", "Chýba robots.txt",
                "Vytvoriť /robots.txt s odkazom na sitemap.")
    if not d1.get("sitemap", {}).get("found"):
        finding("kritické", "SEO / Infra", "Chýba sitemap.xml",
                "Vygenerovať sitemap a zaregistrovať v Google Search Console.")
    if not any(d1.get("dns", {}).get("TXT", [])):
        finding("kritické", "Security / DNS", "Chýba SPF a DMARC DNS záznam",
                "Doména je zraniteľná na email spoofing bez SPF/DMARC.")

    http = d1.get("http", {})
    if not http.get("headers", {}).get("content-encoding"):
        finding("nízke", "Performance", "Kompresia (gzip/br) nie je aktívna",
                "Zapnúť gzip alebo Brotli kompresiu na webserveri.")

    # Performance
    pages = d2.get("pages", [])
    if pages and not d2.get("cache_compression", {}).get("cache-control"):
        finding("kritické", "Performance", "Chýba Cache-Control header",
                "Pridať Cache-Control: public, max-age=31536000 pre statické zdroje.")
    if pages and pages[0].get("render_blocking"):
        blocking_count = len(pages[0]["render_blocking"])
        finding("stredné", "Performance", f"{blocking_count} render-blocking CSS/JS súborov",
                "Zvážiť async/defer alebo preload pre blokujúce zdroje.")

    med = pages[0].get("median", {}) if pages else {}
    lcp = med.get("lcp") or 0
    if lcp > 2500:
        finding("kritické", "Performance", f"LCP {lcp:.0f} ms — nad limitom 2 500 ms",
                "Optimalizovať načítanie hlavného obrázka / obsahu nad prekladom.")
    elif lcp > 1200:
        finding("nízke", "Performance", f"LCP {lcp:.0f} ms — priestor na zlepšenie",
                "Cieľ pre výborné skóre je LCP < 1 200 ms.")

    cls = med.get("cls") if med.get("cls") is not None else 0
    if cls > 0.1:
        finding("kritické", "Performance", f"CLS {cls:.4f} — nad limitom 0.1",
                "Nastaviť pevné rozmery (width/height) pre obrázky a embedy.")

    # SEO
    pages3 = d3.get("pages", [])
    if pages3:
        hp = pages3[0]
        if not hp.get("json_ld"):
            finding("kritické", "SEO", "Chýba JSON-LD štruktúrované dáta",
                    "Pridať minimálne WebSite a Organization schémy.")
        if not (hp.get("open_graph") or {}).get("og:image"):
            finding("kritické", "SEO", "Chýba og:image Open Graph tag",
                    "Bez og:image sa pri zdieľaní na sieťach nezobrazí náhľad.")
        imgs = hp.get("images") or {}
        missing_alt = imgs.get("missing_alt", 0)
        if missing_alt > 0:
            finding("kritické", "SEO / A11y",
                    f"{missing_alt} obrázkov bez alt atribútu",
                    "Dôležité pre SEO aj prístupnosť (WCAG 1.1.1).")
        if not hp.get("twitter_card"):
            finding("stredné", "SEO", "Chýba Twitter Card meta tag",
                    "Pridať <meta name='twitter:card' content='summary_large_image'>.")
        if not (hp.get("title") or {}).get("ok"):
            title_len = (hp.get("title") or {}).get("length", 0)
            finding("stredné", "SEO", f"Title homepage má {title_len} znakov (optimum 45–65)",
                    "Upraviť dĺžku title tagu pre lepší CTR vo vyhľadávaní.")

    broken = d3.get("crawl", {}).get("broken_count", 0)
    if broken > 0:
        finding("kritické", "SEO", f"{broken} broken linkov (HTTP 404)",
                "Opraviť alebo odstrániť nefunkčné interné linky.")

    if len(pages3) > 1:
        sub = pages3[1]
        if not (sub.get("meta_description") or {}).get("ok"):
            finding("stredné", "SEO", "Podstránka nemá optimalizovanú meta description",
                    "Každá indexovateľná stránka by mala mať unikátny meta description (120–160 znakov).")

    # Accessibility
    axe_pages = d4.get("axe", [])
    for axe_page in axe_pages:
        for v in axe_page.get("violations", []):
            priority = "stredné" if v["impact"] in ("critical", "serious") else "nízke"
            finding(priority, "Accessibility",
                    f"axe [{v['impact']}] {v['id']} ({axe_page['label']})",
                    v["description"])

    manual_checks = d4.get("manual", [])
    if manual_checks and not manual_checks[0].get("skip_nav"):
        finding("stredné", "Accessibility", "Chýba skip navigation link",
                "Pridať <a href='#main' class='skip-link'>Preskočiť na obsah</a>.")
    if manual_checks and not manual_checks[0].get("focus_styles_in_css"):
        finding("stredné", "Accessibility", ":focus štýly nenájdené v CSS",
                "Klávesnicová navigácia môže byť neviditeľná — pridať :focus-visible štýly.")

    # Security
    sensitive = d5.get("sensitive_files", {}).get("probes", [])
    for probe in sensitive:
        if probe.get("exposed"):
            finding("kritické", "Security", f"Citlivý súbor prístupný: {probe['path']}",
                    "Zakázať prístup v konfigurácii webservera.")
    if d5.get("headers", {}).get("csp_warnings"):
        finding("stredné", "Security", "CSP obsahuje nebezpečné direktívy",
                f"CSP warnings: {d5['headers']['csp_warnings']}")

    headers5 = d5.get("headers", {}).get("headers", {})
    missing_headers = [h for h in ["strict-transport-security", "content-security-policy",
                                    "x-frame-options", "x-content-type-options",
                                    "referrer-policy", "permissions-policy"]
                       if not headers5.get(h, {}).get("ok")]
    if missing_headers:
        finding("stredné", "Security",
                f"Chýbajú / nesprávne nastavené HTTP security headers ({len(missing_headers)})",
                f"Chýba: {', '.join(missing_headers)}")

    # Compliance
    cc = d6.get("cookie_consent", [])
    if not next((i["result"] for i in cc if "C5" in i["label"]), True):
        finding("kritické", "Compliance / GDPR", "Cookie banner nemá tlačidlo 'Odmietnuť všetko'",
                "EDPB: odmietnutie musí byť rovnako jednoduché ako prijatie.")
    if not next((i["result"] for i in cc if "C2" in i["label"]), True):
        finding("kritické", "Compliance / GDPR", "Non-essential cookies nastavené pred súhlasom",
                "Porušenie ePrivacy direktívy — cookies sa môžu nastavovať až po súhlase.")
    if not next((i["result"] for i in cc if "C3" in i["label"]), True):
        finding("stredné", "Compliance / GDPR", "Cookie banner bez granulárnych kategórií",
                "Pridať samostatné kategórie: analytika, marketing, funkčné.")

    cr = d6.get("consumer_rights", [])
    if not next((i["result"] for i in cr if "S7" in i["label"]), True):
        finding("kritické", "Compliance", "Chýba odkaz na ODR platformu EÚ",
                "Povinný odkaz: https://ec.europa.eu/consumers/odr")

    dsa = d6.get("dsa", [])
    if not next((i["result"] for i in dsa if "D1" in i["label"]), True):
        finding("kritické", "Compliance / DSA", "Chýbajú Podmienky používania (ToS)",
                "DSA (EÚ 2022/2065) vyžaduje zverejnenie ToS pre každú online platformu.")


# ---------------------------------------------------------------------------
# Dynamic phase summaries (generated from data, not hardcoded)
# ---------------------------------------------------------------------------

def build_phase_summaries(target_url: str, d1, d2, d3, d4, d5, d6, d7) -> list[str]:
    lines = []

    # Phase 1 — Discovery
    lines.append("### Fáza 1 — Discovery")
    http = d1.get("http", {})
    tech = d1.get("tech", {})
    dns = d1.get("dns", {})
    ttfb = http.get("ttfb_ms")
    ttfb_str = f"TTFB {ttfb} ms" if ttfb else "TTFB neznámy"
    cms_str = tech.get("cms") or "vlastné riešenie / neidentifikované"
    cdn_str = tech.get("cdn") or "bez CDN"
    hosting_ip = (dns.get("A") or [{}])[0].get("value", "neznámy")
    robots_ok = d1.get("robots", {}).get("status_code") == 200
    sitemap_ok = d1.get("sitemap", {}).get("found", False)
    spf_ok = len(dns.get("TXT", [])) > 0
    ipv6_ok = len(dns.get("AAAA", [])) > 0

    lines.append(f"- **Hosting IP:** `{hosting_ip}`, {ttfb_str}")
    lines.append(f"- **CMS / Stack:** {cms_str}")
    lines.append(f"- **CDN:** {cdn_str}")
    if tech.get("js_frameworks"):
        lines.append(f"- **JS frameworky:** {', '.join(tech['js_frameworks'])}")
    lines.append(f"- robots.txt: {'✅' if robots_ok else '❌ chýba'} | "
                 f"sitemap.xml: {'✅' if sitemap_ok else '❌ chýba'}")
    lines.append(f"- SPF/DMARC DNS: {'✅' if spf_ok else '❌ chýba'} | "
                 f"IPv6: {'✅' if ipv6_ok else '⚠️ nie'}")
    lines.append("")

    # Phase 2 — Performance
    lines.append("### Fáza 2 — Performance")
    pages = d2.get("pages", [])
    if pages:
        med = pages[0].get("median", {})
        lcp = med.get("lcp", 0) or 0
        cls = med.get("cls", 0) or 0
        fcp = med.get("fcp", 0) or 0
        ttfb_p = med.get("ttfb", 0) or 0
        lcp_ok = lcp <= 2500
        cls_ok = cls <= 0.1
        blocking = pages[0].get("render_blocking", [])
        third = pages[0].get("third_party_domains", [])
        cache = d2.get("cache_compression", {})

        lines.append(f"- **LCP:** {lcp:.0f} ms {'✅' if lcp_ok else '❌'} | "
                     f"**CLS:** {cls:.4f} {'✅' if cls_ok else '❌'} | "
                     f"**FCP:** {fcp:.0f} ms | **TTFB:** {ttfb_p:.0f} ms")
        lines.append(f"- Render-blocking zdroje: {len(blocking)} | "
                     f"Third-party domény: {len(third)}")
        lines.append(f"- Cache-Control: {'✅' if cache.get('cache-control') else '❌ chýba'} | "
                     f"Kompresia: {cache.get('content-encoding') or '❌ chýba'}")
    else:
        lines.append("- Dáta nie sú k dispozícii")
    lines.append("")

    # Phase 3 — SEO
    lines.append("### Fáza 3 — SEO")
    pages3 = d3.get("pages", [])
    if pages3:
        hp = pages3[0]
        title_ok = (hp.get("title") or {}).get("ok", False)
        desc_ok = (hp.get("meta_description") or {}).get("ok", False)
        h1_count = len((hp.get("headings") or {}).get("h1", []))
        json_ld_count = len(hp.get("json_ld") or [])
        og_image = bool((hp.get("open_graph") or {}).get("og:image"))
        imgs = hp.get("images") or {}
        missing_alt = imgs.get("missing_alt", 0)
        broken = d3.get("crawl", {}).get("broken_count", 0)

        lines.append(f"- Title: {'✅' if title_ok else '❌'} | "
                     f"Meta description: {'✅' if desc_ok else '❌'} | "
                     f"H1 na homepage: {h1_count}× {'✅' if h1_count == 1 else '⚠️'}")
        lines.append(f"- JSON-LD schémy: {json_ld_count} | "
                     f"og:image: {'✅' if og_image else '❌'} | "
                     f"Obrázky bez alt: {missing_alt}")
        lines.append(f"- Broken linky (404): {broken} {'✅' if broken == 0 else '❌'}")
    else:
        lines.append("- Dáta nie sú k dispozícii")
    lines.append("")

    # Phase 4 — Accessibility
    lines.append("### Fáza 4 — Accessibility (WCAG 2.1 AA)")
    axe_pages = d4.get("axe", [])
    if axe_pages:
        total_critical = sum(p.get("summary", {}).get("critical", 0) for p in axe_pages)
        total_serious = sum(p.get("summary", {}).get("serious", 0) for p in axe_pages)
        total_moderate = sum(p.get("summary", {}).get("moderate", 0) for p in axe_pages)
        m0 = (d4.get("manual") or [{}])[0]
        lang = m0.get("html_lang")
        skip_nav = m0.get("skip_nav", False)
        focus = m0.get("focus_styles_in_css", False)

        lines.append(f"- axe-core violations: critical={total_critical} "
                     f"{'✅' if total_critical == 0 else '❌'}, "
                     f"serious={total_serious}, moderate={total_moderate}")
        lines.append(f"- html lang: {lang or '❌ chýba'} | "
                     f"skip-nav: {'✅' if skip_nav else '❌'} | "
                     f":focus CSS: {'✅' if focus else '⚠️'}")
    else:
        lines.append("- Dáta nie sú k dispozícii")
    lines.append("")

    # Phase 5 — Security
    lines.append("### Fáza 5 — Security")
    tls = d5.get("tls", {})
    cert = tls.get("certificate", {})
    protos = tls.get("protocols", {})
    sensitive = d5.get("sensitive_files", {}).get("probes", [])
    exposed = [p["path"] for p in sensitive if p.get("exposed")]
    headers5 = d5.get("headers", {}).get("headers", {})
    hsts_ok = headers5.get("strict-transport-security", {}).get("ok", False)

    tls_versions = [k.replace("_", " ").upper() for k, v in protos.items() if v]
    lines.append(f"- TLS protokoly: {', '.join(tls_versions) or 'neznáme'}")
    if not cert.get("error"):
        lines.append(f"- Certifikát platný do: {cert.get('not_after', '?')} | "
                     f"Vydavateľ: {cert.get('issuer', {}).get('organizationName', '?')}")
    lines.append(f"- HSTS: {'✅' if hsts_ok else '❌'} | "
                 f"Heartbleed: {'❌' if tls.get('heartbleed') else '✅ OK'} | "
                 f"ROBOT: {'❌' if tls.get('robot') else '✅ OK'}")
    if exposed:
        lines.append(f"- ❌ Citlivé súbory prístupné: {', '.join(exposed)}")
    else:
        lines.append("- ✅ Žiadne citlivé súbory verejne prístupné")
    lines.append("")

    # Phase 6 — Compliance
    lines.append("### Fáza 6 — GDPR & Compliance")
    gdpr = d6.get("gdpr_privacy", [])
    cc = d6.get("cookie_consent", [])
    trackers = d6.get("trackers", {})

    gdpr_auto = [i for i in gdpr if i["result"] != "manual_review_required"]
    gdpr_pass = sum(1 for i in gdpr_auto if i["result"] is True)
    cc_auto = [i for i in cc if i["result"] != "manual_review_required"]
    cc_pass = sum(1 for i in cc_auto if i["result"] is True)
    tracker_count = len(trackers.get("trackers", []))

    lines.append(f"- Privacy Policy GDPR kontroly: {gdpr_pass}/{len(gdpr_auto)} ✅")
    lines.append(f"- Cookie consent kontroly: {cc_pass}/{len(cc_auto)}")
    lines.append(f"- Third-party trackery: {tracker_count} "
                 f"{'✅ žiadne' if tracker_count == 0 else '⚠️ nájdené'}")
    if tracker_count > 0:
        for t_item in trackers.get("trackers", []):
            lines.append(f"  - {t_item['category']}: {', '.join(t_item['domains'])}")
    lines.append("")

    # Phase 7 — Load
    lines.append("### Fáza 7 — Load Test")
    scenarios = d7.get("scenarios", [])
    if scenarios:
        for sc in scenarios:
            agg = sc.get("aggregated") or {}
            name = sc["scenario"]["name"]
            p95 = agg.get("p95_ms", 0)
            err = agg.get("failure_rate_pct", 0)
            p95_mark = "✅" if p95 < 1000 else ("⚠️" if p95 < 3000 else "❌")
            lines.append(f"- {name} ({sc['scenario']['users']} VU): "
                         f"p50={agg.get('median_ms', 0):.0f}ms, "
                         f"p95={p95:.0f}ms {p95_mark}, "
                         f"err={err:.1f}%")
    else:
        lines.append("- Dáta nie sú k dispozícii")
    lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------

PRIORITY_ORDER = {"kritické": 0, "stredné": 1, "nízke": 2}
PRIORITY_ICON = {"kritické": "🔴", "stredné": "🟡", "nízke": "⚪"}


def render_score_bar(pct: int, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"`{bar}` {pct} %"


def generate_report(target_url: str, scores: list[Score],
                    phase_summaries: list[str]) -> str:
    today = date.today().isoformat()
    domain = urlparse(target_url).netloc or target_url
    lines = []

    lines += [
        f"# Audit Report — {domain}",
        f"",
        f"**Dátum auditu:** {today}  ",
        f"**Cieľ:** {target_url}  ",
        f"**Nástroje:** Python 3.13, httpx, dnspython, Playwright, axe-core, sslyze, Locust  ",
        f"",
        f"---",
        f"",
        f"## Súhrnné skóre",
        f"",
        f"| Oblast | Skóre | Body | Hodnotenie |",
        f"|--------|-------|------|------------|",
    ]

    for sc in scores:
        bar = render_score_bar(sc.pct)
        lines.append(f"| **{sc.name}** | {bar} | {sc.points}/{sc.max_points} | {sc.grade} |")

    total_pts = sum(s.points for s in scores)
    total_max = sum(s.max_points for s in scores)
    total_pct = round(total_pts / total_max * 100) if total_max else 0
    total_grade = ('🟢 Výborné' if total_pct >= 90 else '🟡 Dobré'
                   if total_pct >= 70 else '🟠 Potrebuje zlepšenie'
                   if total_pct >= 50 else '🔴 Kritické')
    lines += [
        f"| **CELKOVÉ** | {render_score_bar(total_pct)} | **{total_pts}/{total_max}** | {total_grade} |",
        f"",
    ]

    # Prioritized findings
    sorted_findings = sorted(FINDINGS, key=lambda f: PRIORITY_ORDER.get(f["priority"], 9))
    lines += [
        f"---",
        f"",
        f"## Prioritizované odporúčania",
        f"",
        f"| # | Priorita | Oblasť | Nález | Odporúčanie |",
        f"|---|----------|--------|-------|-------------|",
    ]
    for i, f in enumerate(sorted_findings, 1):
        icon = PRIORITY_ICON.get(f["priority"], "")
        detail = f["detail"].replace("|", "\\|").replace("\n", " ")
        title = f["title"].replace("|", "\\|")
        lines.append(f"| {i} | {icon} {f['priority']} | {f['category']} | {title} | {detail} |")

    # Per-category details
    lines += ["", "---", "", "## Detail skóre per kategória", ""]

    for sc in scores:
        lines += [f"### {sc.name} — {sc.pct} %", "", "| Kontrola | Výsledok | Váha |",
                  "|----------|----------|------|"]
        for item in sc.items:
            mark = "✅" if item["ok"] else "❌"
            note = f" — {item['note']}" if item["note"] else ""
            lines.append(f"| {item['label']}{note} | {mark} | {item['weight']} |")
        lines.append("")

    # Dynamic phase summaries
    lines += [
        "---",
        "",
        "## Kľúčové nálezy per fáza",
        "",
    ]
    lines += phase_summaries

    lines += [
        "---",
        "",
        f"*Report vygenerovaný: {today} | audit/report.py*",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(target_url: str, results_dir: Path) -> str:
    console.rule("[bold]Fáza 8 — Generujem súhrnný report")

    d1 = load(results_dir, "01_discovery.json")
    d2 = load(results_dir, "02_performance.json")
    d3 = load(results_dir, "03_seo.json")
    d4 = load(results_dir, "04_accessibility.json")
    d5 = load(results_dir, "05_security.json")
    d6 = load(results_dir, "06_compliance.json")
    d7 = load(results_dir, "07_load.json")

    scores = [
        score_infrastructure(d1),
        score_performance(d2),
        score_seo(d3),
        score_accessibility(d4),
        score_security(d5),
        score_compliance(d6),
        score_load(d7),
    ]

    FINDINGS.clear()
    collect_findings(d1, d2, d3, d4, d5, d6, d7)
    phase_summaries = build_phase_summaries(target_url, d1, d2, d3, d4, d5, d6, d7)

    t = Table("Oblasť", "Skóre %", "Body", "Hodnotenie")
    for sc in scores:
        color = "green" if sc.pct >= 70 else ("yellow" if sc.pct >= 50 else "red")
        t.add_row(sc.name, f"[{color}]{sc.pct} %[/]",
                  f"{sc.points}/{sc.max_points}", sc.grade)
    total_pts = sum(s.points for s in scores)
    total_max = sum(s.max_points for s in scores)
    total_pct = round(total_pts / total_max * 100)
    color = "green" if total_pct >= 70 else ("yellow" if total_pct >= 50 else "red")
    t.add_row("[bold]CELKOVÉ[/]", f"[{color}][bold]{total_pct} %[/][/]",
              f"[bold]{total_pts}/{total_max}[/]",
              "🟢 Výborné" if total_pct >= 90 else "🟡 Dobré"
              if total_pct >= 70 else "🟠 Potrebuje zlepšenie")
    console.print(t)

    console.print(f"\n[bold]Celkom nálezov:[/] {len(FINDINGS)} "
                  f"({sum(1 for f in FINDINGS if f['priority']=='kritické')} kritických, "
                  f"{sum(1 for f in FINDINGS if f['priority']=='stredné')} stredných, "
                  f"{sum(1 for f in FINDINGS if f['priority']=='nízke')} nízkych)")

    md = generate_report(target_url, scores, phase_summaries)
    out = results_dir / "REPORT.md"
    out.write_text(md, encoding="utf-8")
    console.print(f"\n[bold green]✓ Report uložený:[/] {out}")
    return md


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else input("URL: ")
    main(url, Path("results"))
