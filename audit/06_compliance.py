"""
Fáza 6 — GDPR & EU Compliance Audit
Výstup: results/<job_id>/06_compliance.json

Automaticky detekované položky: HTTP/HTML analýza + Playwright network log.
Manuálne položky: uložené so stavom "manual_review_required".
"""

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.table import Table

from audit.config import USER_AGENT, TIMEOUT

console = Console()


def get_soup(url: str) -> tuple[BeautifulSoup, dict]:
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT},
                     timeout=TIMEOUT, follow_redirects=True)
    return BeautifulSoup(resp.text, "lxml"), dict(resp.headers)


def find_link(soup: BeautifulSoup, keywords: list[str]) -> str | None:
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        href = a["href"].lower()
        if any(k in text or k in href for k in keywords):
            return a["href"]
    return None


def find_text(soup: BeautifulSoup, keywords: list[str]) -> bool:
    text = soup.get_text(" ", strip=True).lower()
    return any(k in text for k in keywords)


def check(label: str, result: bool, note: str = "") -> dict:
    color = "green" if result else "red"
    mark = "✅" if result else "❌"
    console.print(f"  [{color}]{mark}[/] {label}" + (f" — {note}" if note else ""))
    return {"label": label, "result": result, "note": note}


def manual(label: str, note: str = "") -> dict:
    console.print(f"  [yellow]👁 [/] {label}" + (f" — {note}" if note else ""))
    return {"label": label, "result": "manual_review_required", "note": note}


def check_gdpr_privacy(base_url: str) -> list[dict]:
    console.rule("[bold cyan]GDPR — Privacy Policy")
    results = []
    soup_home, _ = get_soup(base_url)

    pp_keywords = ["ochrana osobných", "privacy", "gdpr", "osobné údaje",
                   "ochrana-osobnych", "privacy-policy"]
    pp_href = find_link(soup_home, pp_keywords)
    results.append(check("G1 Privacy policy link z homepage", bool(pp_href), pp_href or ""))

    if pp_href:
        pp_url = urljoin(base_url, pp_href)
        soup_pp, _ = get_soup(pp_url)

        footer = soup_home.find("footer")
        footer_has_pp = False
        if footer:
            footer_has_pp = any(k in footer.get_text().lower() for k in pp_keywords)
        results.append(check("G2 Link v pätičke (footer)", footer_has_pp))

        identity_kw = ["prevádzkovateľ", "prevadzkovatel", "spoločnosť", "s.r.o", "a.s.",
                       "ico", "ičo", "obchodné meno"]
        results.append(check("G3 Identita prevádzkovateľa (meno/IČO)", find_text(soup_pp, identity_kw)))

        contact_kw = ["kontakt", "email", "@", "zodpovedná osoba", "dpo"]
        results.append(check("G4 Kontakt / DPO email", find_text(soup_pp, contact_kw)))

        legal_kw = ["právny základ", "právnom základe", "čl. 6", "article 6",
                    "súhlas", "oprávnený záujem", "zmluva"]
        results.append(check("G5 Právny základ spracúvania", find_text(soup_pp, legal_kw)))

        purpose_kw = ["účel", "účely spracúvania", "spracúvame", "spracovávame"]
        results.append(check("G6 Účely spracúvania", find_text(soup_pp, purpose_kw)))

        recipient_kw = ["príjemcov", "príjemca", "tretie strany", "tretím stranám",
                        "poskytujeme", "zdieľame"]
        results.append(check("G7 Príjemcovia / tretie strany", find_text(soup_pp, recipient_kw)))

        retention_kw = ["doba uchovávania", "uchovávame", "lehota", "rokov", "mesiacov",
                        "do odvolania"]
        results.append(check("G8 Doba uchovávania", find_text(soup_pp, retention_kw)))

        rights_kw = ["právo na prístup", "právo na výmaz", "právo na opravu",
                     "právo na prenosnosť", "právo namietať", "vaše práva"]
        results.append(check("G9 Práva dotknutých osôb", find_text(soup_pp, rights_kw)))

        exercise_kw = ["kontaktujte", "písomne", "emailom", "formulár", "žiadosť"]
        results.append(check("G10 Postup uplatnenia práv", find_text(soup_pp, exercise_kw)))

        dpa_kw = ["úooú", "úrad na ochranu", "dozorný orgán", "sťažnosť",
                  "dataprotection.gov.sk"]
        results.append(check("G11 Právo sťažovať sa na ÚOOÚ", find_text(soup_pp, dpa_kw)))
    else:
        for code in ["G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "G10", "G11"]:
            results.append(check(f"{code} (nedostupné — chýba PP)", False))

    return results


def check_cookie_consent(base_url: str) -> list[dict]:
    console.rule("[bold cyan]ePrivacy — Cookie Consent")
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)

        requests_before = []
        page = context.new_page()
        page.on("request", lambda r: requests_before.append({
            "url": r.url, "type": r.resource_type
        }))
        page.goto(base_url, wait_until="networkidle", timeout=30000)
        cookies_before = context.cookies()

        banner_selectors = [
            "[id*='cookie']", "[class*='cookie']", "[id*='consent']",
            "[class*='consent']", "[id*='gdpr']", "[class*='gdpr']",
            "[id*='banner']", "[class*='banner']",
        ]
        banner_found = False
        banner_selector_used = None
        for sel in banner_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    banner_found = True
                    banner_selector_used = sel
                    break
            except Exception:
                pass
        results.append(check("C1 Cookie banner pri prvej návšteve", banner_found,
                             banner_selector_used or ""))

        non_essential_before = [
            c for c in cookies_before
            if not any(ess in c["name"].lower()
                       for ess in ["session", "csrf", "_token", "lang", "locale"])
        ]
        results.append(check("C2 Žiadne non-essential cookies pred súhlasom",
                             len(non_essential_before) == 0,
                             f"cookies pred súhlasom: {[c['name'] for c in cookies_before]}"))

        granular = page.query_selector_all(
            "input[type='checkbox'][id*='categor'], "
            "input[type='checkbox'][id*='analytics'], "
            "input[type='checkbox'][id*='marketing'], "
            "[class*='cookie-category'], [class*='consent-category']"
        )
        results.append(check("C3 Granulárny súhlas (kategórie)",
                             len(granular) > 0,
                             f"nájdené elementy: {len(granular)}"))

        preticked = page.query_selector_all(
            "input[type='checkbox']:checked[id*='marketing'], "
            "input[type='checkbox']:checked[id*='analytics'], "
            "input[type='checkbox']:checked[id*='advertis']"
        )
        results.append(check("C4 Žiadne predvolene zaškrtnuté políčka",
                             len(preticked) == 0,
                             f"pre-ticked: {len(preticked)}"))

        reject_btn = None
        for text in ["odmietnuť", "odmietam", "reject", "decline", "nesúhlasím"]:
            try:
                reject_btn = page.get_by_text(re.compile(text, re.I)).first
                if reject_btn and reject_btn.is_visible():
                    break
                reject_btn = None
            except Exception:
                pass
        results.append(check("C5 Tlačidlo 'Odmietnuť všetko' prítomné", reject_btn is not None))
        results.append(manual("C6 Odvolanie súhlasu rovnako ľahké ako udelenie"))

        soup_home, _ = get_soup(base_url)
        cookie_list_kw = ["zoznam cookies", "zoznam súborov", "cookie policy",
                          "cookies používame"]
        results.append(check("C7 Zoznam konkrétnych cookies dostupný",
                             find_text(soup_home, cookie_list_kw)
                             or bool(find_link(soup_home, ["cookie-policy", "cookie-list"]))))

        base_host = urlparse(base_url).hostname
        third_party_before = list({
            urlparse(r["url"]).hostname
            for r in requests_before
            if urlparse(r["url"]).hostname
            and urlparse(r["url"]).hostname != base_host
            and not urlparse(r["url"]).hostname.endswith("." + base_host)
        })
        results.append(check("C8 Žiadne third-party requesty pred súhlasom",
                             len(third_party_before) == 0,
                             f"third-party pred súhlasom: {third_party_before}"))

        browser.close()

    return results


def check_ecommerce(base_url: str) -> list[dict]:
    console.rule("[bold cyan]E-commerce zákon (č. 22/2004 Z.z.)")
    results = []
    soup, _ = get_soup(base_url)

    identity_kw = ["ičo", "ico", "s.r.o", "a.s.", "obchodné meno", "sídlo"]
    results.append(check("E1 IČO / obchodné meno / sídlo", find_text(soup, identity_kw)))

    email_pattern = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
    phone_pattern = re.compile(r"(\+421|0[0-9]{9}|\d{3}[\s\-]\d{3}[\s\-]\d{3})")
    text = soup.get_text()
    results.append(check("E2 Email kontakt", bool(email_pattern.search(text))))
    results.append(check("E2 Telefón kontakt", bool(phone_pattern.search(text))))
    results.append(manual("E3 Zápis v obchodnom registri"))
    results.append(manual("E4 Živnostenské oprávnenie"))

    return results


def check_consumer_rights(base_url: str) -> list[dict]:
    console.rule("[bold cyan]Práva spotrebiteľov (zákon č. 102/2014 Z.z.)")
    results = []
    soup, _ = get_soup(base_url)

    withdrawal_kw = ["14 dní", "odstúpiť od zmluvy", "právo na odstúpenie",
                     "14-dňová lehota", "14 calendar"]
    results.append(check("S1 Právo na odstúpenie (14 dní)", find_text(soup, withdrawal_kw)))

    withdrawal_link = find_link(soup, ["odstúpenie", "withdrawal", "reklamácia"])
    results.append(check("S2 Formulár / postup odstúpenia dostupný",
                         bool(withdrawal_link), withdrawal_link or ""))

    vat_kw = ["s dph", "vrátane dph", "vč. dph", "inc. vat", "€"]
    results.append(check("S3 Ceny vrátane DPH", find_text(soup, vat_kw)))
    results.append(manual("S4 Náklady na dopravu jasne uvedené pred dokončením objednávky"))

    complaint_kw = ["reklamáci", "reklamačný poriadok", "sťažnosť", "complaint"]
    complaint_link = find_link(soup, ["reklamaci", "complaint", "sťažnost"])
    results.append(check("S5 Reklamačný poriadok",
                         find_text(soup, complaint_kw) or bool(complaint_link)))

    adr_kw = ["mimosúdne riešenie", "alternatívne riešenie sporov", "rso", "adr"]
    results.append(check("S6 Mimosúdne riešenie sporov (RSO/ADR)", find_text(soup, adr_kw)))

    odr_kw = ["ec.europa.eu/consumers/odr", "odr", "online dispute"]
    odr_link = find_link(soup, ["odr", "consumers/odr"])
    results.append(check("S7 Odkaz na ODR platformu EÚ",
                         find_text(soup, odr_kw) or bool(odr_link), odr_link or ""))

    return results


def check_dsa(base_url: str) -> list[dict]:
    console.rule("[bold cyan]DSA — Digital Services Act (EÚ 2022/2065)")
    results = []
    soup, _ = get_soup(base_url)

    tos_kw = ["obchodné podmienky", "podmienky používania", "terms of service", "terms of use"]
    tos_link = find_link(soup, ["podmienky", "terms", "obchodne-podmienky"])
    results.append(check("D1 Podmienky používania (ToS)",
                         find_text(soup, tos_kw) or bool(tos_link), tos_link or ""))
    results.append(manual("D2 Podmienky formulované jasne a zrozumiteľne"))

    report_kw = ["nahlásiť", "nahlásenie", "report", "porušenie", "nezákonný obsah"]
    results.append(check("D3 Mechanizmus nahlasenia nezákonného obsahu",
                         find_text(soup, report_kw)))
    results.append(manual("D4 Transparentnosť algoritmického odporúčania"))
    results.append(manual("D5 Kontakt pre orgány dohľadu"))

    return results


TRACKER_CATEGORIES = {
    "Analytika": ["google-analytics", "googletagmanager", "gtag", "hotjar",
                  "matomo", "plausible", "clarity.ms", "mixpanel"],
    "Reklama / retargeting": ["facebook.net", "connect.facebook", "doubleclick",
                               "googlesyndication", "ads.google", "tiktok",
                               "bing.com/bat", "pinterest"],
    "Social widgets": ["facebook.com/tr", "platform.twitter", "linkedin.com/px"],
    "Chat / support": ["intercom.io", "zendesk", "crisp.chat", "tawk.to", "freshchat"],
    "Platobné brány": ["stripe.com", "paypal.com", "gopay", "comgate", "barion.com", "trustpay"],
}


def check_trackers(base_url: str) -> dict:
    console.rule("[bold cyan]Third-party Tracker Detekcia")
    results = {"trackers": [], "requests_count": 0}

    all_requests = []
    base_host = urlparse(base_url).hostname

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        page.on("request", lambda r: all_requests.append(r.url))
        page.goto(base_url, wait_until="networkidle", timeout=30000)
        browser.close()

    results["requests_count"] = len(all_requests)

    found_trackers = {}
    for url in all_requests:
        host = urlparse(url).hostname or ""
        if host == base_host or host.endswith("." + base_host):
            continue
        for category, signals in TRACKER_CATEGORIES.items():
            if any(s in url.lower() for s in signals):
                found_trackers.setdefault(category, set()).add(host)

    if found_trackers:
        for cat, hosts in found_trackers.items():
            results["trackers"].append({"category": cat, "domains": list(hosts)})
            console.print(f"  [yellow]{cat}:[/] {list(hosts)}")
    else:
        console.print("  [green]✅ Žiadne trackery detekované[/]")

    all_third_party = {
        urlparse(u).hostname for u in all_requests
        if urlparse(u).hostname and urlparse(u).hostname != base_host
        and not (urlparse(u).hostname or "").endswith("." + base_host)
    }
    results["all_third_party_domains"] = sorted(all_third_party)

    return results


def main(target_url: str, results_dir: Path) -> dict:
    console.rule(f"[bold]Fáza 6 — GDPR & EU Compliance Audit — {target_url}")

    results = {
        "target": target_url,
        "gdpr_privacy": check_gdpr_privacy(target_url),
        "cookie_consent": check_cookie_consent(target_url),
        "ecommerce_law": check_ecommerce(target_url),
        "consumer_rights": check_consumer_rights(target_url),
        "dsa": check_dsa(target_url),
        "trackers": check_trackers(target_url),
    }

    console.rule("[bold cyan]Súhrn")
    t = Table("Sekcia", "Pass", "Fail", "Manual")
    for section, items in results.items():
        if section in ("trackers", "target"):
            continue
        auto = [i for i in items if i["result"] != "manual_review_required"]
        manual_items = [i for i in items if i["result"] == "manual_review_required"]
        passed = sum(1 for i in auto if i["result"] is True)
        failed = sum(1 for i in auto if i["result"] is False)
        t.add_row(section, f"[green]{passed}[/]", f"[red]{failed}[/]", str(len(manual_items)))
    console.print(t)

    out_file = results_dir / "06_compliance.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    console.print(f"\n[bold green]✓ Výsledok uložený:[/] {out_file}")
    return results


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else input("URL: ")
    main(url, Path("results"))
