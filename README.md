# Site Audit Tool

Komplexný automatizovaný audit webu: Performance, SEO, Accessibility, Security a GDPR Compliance.

Použitie bez záruky kompletnosti a bez akejkoľvek zodpovednosti autora. Nevyužíva žiadne nelegálne nástroje, takže je (asi) v pohode ho použiť na akúkoľvek stránku. Ja som si auditoval lken svoje...

Funguje ako **web aplikácia** — zadáte URL, audit beží na pozadí, výsledky sa zobrazia v prehliadači a dajú sa exportovať ako `.md` alebo `.pdf`.

## Čo audit meria

| Fáza | Čo | Nástroje |
|------|-----|----------|
| 1 Discovery | DNS, robots.txt, sitemap, tech fingerprinting, TTFB | httpx, dnspython |
| 2 Performance | Core Web Vitals (LCP, CLS, FCP), cache, kompresia | Playwright |
| 3 SEO | Meta tagy, JSON-LD, Open Graph, broken links, crawl | httpx, BeautifulSoup |
| 4 Accessibility | WCAG 2.1 AA violations, formuláre, focus štýly | axe-core, Playwright |
| 5 Security | TLS/SSL, HTTP security headers, citlivé súbory, cookies | sslyze, httpx |
| 6 GDPR & Compliance | Cookie consent, Privacy Policy, eCommerce zákon, DSA, trackery | Playwright |
| 7 Load Test | Benchmark: 1/5/20 VU, p50/p95/p99 | Locust |

## Inštalácia

```bash
# Klonovanie
git clone <repo-url>
cd site-audit-tool

# Závislosti (odporúčame uv)
uv sync

# Playwright prehliadač
uv run playwright install chromium
```

## Spustenie — Web UI

```bash
uv run uvicorn web.app:app --reload --host 0.0.0.0 --port 8000
```

Otvorte `http://localhost:8000`, zadajte URL a spustite audit.

## Spustenie — CLI (jednotlivé fázy)

```bash
# Všetky fázy naraz
uv run python -m audit.runner https://example.com

# Jednotlivá fáza
uv run python audit/01_discovery.py https://example.com
uv run python audit/02_performance.py https://example.com
# ... atď.
```

## Štruktúra projektu

```
site-audit-tool/
├── audit/
│   ├── config.py           # Globálne nastavenia (timeout, limity, user-agent)
│   ├── 01_discovery.py     # Discovery & Reconnaissance
│   ├── 02_performance.py   # Core Web Vitals, cache, kompresia
│   ├── 03_seo.py           # On-page SEO, crawl, structured data
│   ├── 04_accessibility.py # WCAG 2.1 AA (axe-core + manuálne kontroly)
│   ├── 05_security.py      # TLS, headers, cookies, citlivé súbory
│   ├── 06_compliance.py    # GDPR, cookie consent, eCommerce, DSA
│   ├── 07_load.py          # Locust load test
│   └── report.py           # Scoring + Markdown report generátor
├── web/
│   ├── app.py              # FastAPI aplikácia
│   ├── runner.py           # Asynchrónny orchestrátor fáz
│   ├── templates/          # Jinja2 HTML šablóny
│   └── static/             # CSS
├── results/                # .gitignored — JSON + REPORT.md + REPORT.pdf
├── pyproject.toml
└── README.md
```

## Export výsledkov

- **`.md`** — Markdown report (automaticky generovaný z JSON výsledkov každej fázy)
- **`.pdf`** — PDF generovaný cez Playwright (žiadna extra závislosť)

## Je potrebná AI?

**Nie pre základný audit.** Všetkých 7 fáz je deterministických — rule-based kontroly bez AI.

AI by pridala hodnotu pre:
- Generovanie naratívnych súhrnov v prirodzenom jazyku (voliteľné)
- Analýzu kvality textového obsahu stránky
- Personalizované odporúčania pre konkrétny biznis typ

Aktuálne sú súhrny generované algoritmicky z nameraných dát.

## Etické obmedzenia

- Load test: max 20 VU s pauzami — nie DoS útok
- Žiadne exploity, SQL injection ani XSS proby
- Iba štandardné GET requesty
- User-Agent: `site-audit-bot/1.0` — transparentný identifikátor
- Citlivé súbory: iba kontrola HTTP status kódu (nie stiahnutie obsahu)

## Požiadavky

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (alebo pip)
- Chromium (inštaluje sa cez `playwright install chromium`)
