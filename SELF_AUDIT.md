# Self-Audit Analýza — Site Audit Tool (192.168.1.40:8000)

> Dátum auditu: 2026-03-01
> Celkové skóre: 34 % 🔴 Kritické
> Tento dokument analyzuje nálezy a navrhuje konkrétne opravy **iba v zdrojovom kóde nástroja** (nie na infraštruktúre).

---

## Čo je relevantné vs. čo nie

Audit bol spustený na lokálnom LAN serveri (nie verejná doména). Niektoré nálezy sú **nie aplikovateľné** pre lokálny dev nástroj:

| Nález | Relevantné? | Dôvod |
|-------|-------------|-------|
| SPF / DMARC DNS záznam | ❌ N/A | LAN IP 192.168.1.40 — nie je doména, email spoofing irelevantný |
| HTTP/3 (QUIC) | ❌ N/A | Vyžaduje TLS + doménu, pre local dev N/A |
| IPv6 (AAAA záznam) | ❌ N/A | LAN infraštruktúra |
| CDN detekovaný | ❌ N/A | Lokálny server |
| TLS / HTTPS / HSTS / Heartbleed / ROBOT | ❌ N/A lokálne | Pre produkčné nasadenie za nginx/Caddy áno |
| GDPR Cookie banner / ODR / ToS / GDPR PP | ⚠️ Čiastočne | Nástroj nezbiera dáta, takže full GDPR N/A. Ale ak pôjde verejne, treba. |
| Load Test 0 % score | ✅ Bug v kóde | Locust mal 0 requestov — treba vyšetriť |

---

## Skupiny opráv podľa priority

### 🔴 P1 — Rýchle opravy v kóde (každá < 30 min)

#### 1. HTTP Security Headers Middleware
**Súbor:** `web/app.py`
**Problém:** Chýba 6 security headers: `Strict-Transport-Security`, `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`
**Riešenie:** Pridať FastAPI middleware, ktorý tieto headers pridá do každej odpovede.

```python
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; connect-src 'self'"
        )
        return response

app.add_middleware(SecurityHeadersMiddleware)
```

**Poznámka:** HSTS vynechať pre lokálny HTTP server — pridať len pre produkciu s HTTPS.

---

#### 2. GZip kompresia
**Súbor:** `web/app.py`
**Problém:** Chýba `Content-Encoding: gzip` — statické súbory a HTML odpovede sa posielajú nekomprimované.
**Riešenie:** Pridať `GZipMiddleware` z Starlette (je súčasťou FastAPI, žiadna extra závislosť).

```python
from starlette.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
```

---

#### 3. Cache-Control pre statické súbory
**Súbor:** `web/app.py`
**Problém:** `StaticFiles` mount nemá nastavený `Cache-Control` header.
**Riešenie:** Pridať do Security Headers middleware pre `/static/` cesty:

```python
if request.url.path.startswith("/static/"):
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
```

---

#### 4. robots.txt a sitemap.xml
**Súbor:** `web/app.py`
**Problém:** Chýbajú `/robots.txt` a `/sitemap.xml` routes.
**Riešenie:** Pridať dve jednoduché GET routes:

```python
@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return "User-agent: *\nDisallow: /audit/\nSitemap: /sitemap.xml\n"

@app.get("/sitemap.xml", response_class=Response)
async def sitemap(request: Request):
    base = str(request.base_url).rstrip("/")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/0.9">
  <url><loc>{base}/</loc></url>
</urlset>"""
    return Response(content=xml, media_type="application/xml")
```

---

### 🟡 P2 — SEO meta tagy (všetky šablóny, ~ 1 hodina)

**Súbory:** `web/templates/index.html`, `audit.html`, `report.html`

#### 5. Title tag — príliš krátky (15 znakov)
Aktuálne: `<title>Site Audit Tool</title>`
Potrebné: 45–65 znakov
Návrh: `<title>Site Audit Tool — automatický audit webu: SEO, Performance, GDPR</title>`

#### 6. Meta description — chýba úplne
Pridať do `index.html`:
```html
<meta name="description" content="Automatický audit webu: Core Web Vitals, SEO, WCAG prístupnosť, bezpečnosť a GDPR compliance. Výsledky ako .md a .pdf.">
```

#### 7. Open Graph a Twitter Card meta tagy
Chýbajú úplne. Pridať do `<head>` všetkých šablón:
```html
<meta property="og:title" content="Site Audit Tool">
<meta property="og:description" content="Automatický audit webu…">
<meta property="og:type" content="website">
<meta property="og:image" content="/static/og-image.png">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Site Audit Tool">
```
Potrebné: vytvoriť `/web/static/og-image.png` (1200×630 px).

#### 8. Canonical URL
```html
<link rel="canonical" href="{{ request.url }}">
```

#### 9. JSON-LD Structured Data
Pridať do `index.html`:
```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "WebApplication",
  "name": "Site Audit Tool",
  "description": "Automatický audit webu",
  "applicationCategory": "DeveloperApplication",
  "operatingSystem": "Any"
}
</script>
```

---

### 🟡 P3 — Accessibility (WCAG 2.1 AA)

#### 10. Color Contrast — serious violation
**Problém:** axe-core našiel 1 serious color-contrast violation.
**Identifikovaný element:** `.phase-card p` s farbou `#64748b` (muted) na pozadí `#eff6ff` (checked card bg) = **kontrast ~4.33:1** — pod limitom 4.5:1.
**Riešenie:** Stmaviť text v kartách:
```css
.phase-card p {
  color: #475569;  /* stmavenie z #64748b → #475569, kontrast ~5.5:1 na #eff6ff */
}
```

#### 11. Skip navigation link
**Problém:** Chýba skip-nav pre klávesnicovú navigáciu.
**Riešenie:** Pridať na začiatok `<body>` v `index.html` a `report.html`:
```html
<a href="#main-content" class="skip-link">Preskočiť na obsah</a>
```
S CSS:
```css
.skip-link {
  position: absolute;
  top: -100%;
  left: 0;
  background: #000;
  color: #fff;
  padding: 0.5rem 1rem;
  z-index: 9999;
}
.skip-link:focus { top: 0; }
```
A pridať `id="main-content"` na `<main>`.

---

### 🔴 P4 — Load Test bug (vyšetriť + opraviť)

#### 12. Locust 0 requestov — 0 % skóre
**Symptóm:** Scoring ukazuje `p95=9999ms ❌`, ale phase summary ukazuje `p95=0ms ✅` — protichodné výsledky.
**Príčina:** `aggregated` v JSON je `None` — Locust subprocess neparsal CSV (buď CSV neexistovalo alebo malo prázdny "Aggregated" riadok).
**Koreň problému — dve hypotézy:**
  - **A) Sieťový problém:** Locust subprocess beží vo `ThreadPoolExecutor` z asyncio event loop. Je možné, že z tohto kontextu nedokáže pripojiť na `http://192.168.1.40:8000` (napr. firewall, network namespace).
  - **B) Locust CSV parsing bug:** Ak Locust nenašiel žiadne úspešné requesty, CSV "Aggregated" riadok môže mať `Request Count=0` a niektoré polia prázdne → parsing vráti `None`.

**Ako vyšetriť:** Spustiť ručne:
```bash
uv run python audit/07_load.py http://192.168.1.40:8000
```
A skontrolovať či Locust robí requesty.

**Bug v kóde `report.py`:** Nekonzistentné default hodnoty:
- `score_load()`: `agg.get("p95_ms", 9999)` → pri None dat vyhodí ❌ (správne)
- `build_phase_summaries()`: `agg.get("p95_ms", 0)` + `p95 < 1000` → falošné ✅

Oprava: v `build_phase_summaries` zmeniť default na `9999` alebo zobraziť "N/A" ak `agg` je prázdne.

---

### ⚪ P5 — Pre produkčné nasadenie (mimo rozsah lokálneho dev)

Tieto veci sú **irelevantné pre lokálny nástroj** ale treba ich adresovať ak pôjde verejne:

| Položka | Čo treba |
|---------|----------|
| HTTPS / TLS | Nasadiť za **Caddy** alebo **nginx + Certbot** (automatický Let's Encrypt) |
| HSTS | Pridať po nasadení HTTPS: `Strict-Transport-Security: max-age=31536000` |
| GDPR Privacy Policy | Pridať stránku `/privacy` so základnou PP ak pôjde verejne |
| Cookie banner | Ak sa pridajú analytické cookies — inak N/A |
| sitemap v Google Search Console | Po nasadení na verejnú doménu |

---

## Zhrnutie — poradie práce

| # | Priorita | Čo | Súbor | Odhad |
|---|----------|----|-------|-------|
| 1 | 🔴 | Security headers middleware | `web/app.py` | 15 min |
| 2 | 🔴 | GZip middleware | `web/app.py` | 5 min |
| 3 | 🔴 | Cache-Control pre `/static/` | `web/app.py` | 5 min |
| 4 | 🔴 | `/robots.txt` a `/sitemap.xml` routes | `web/app.py` | 10 min |
| 5 | 🟡 | Title, meta description, OG, Twitter Card, canonical | všetky šablóny | 30 min |
| 6 | 🟡 | JSON-LD WebApplication schema | `index.html` | 10 min |
| 7 | 🟡 | Color contrast fix `.phase-card p` | `style.css` | 5 min |
| 8 | 🟡 | Skip navigation link | všetky šablóny + `style.css` | 15 min |
| 9 | 🔴 | Load Test bug — vyšetriť + opraviť | `audit/07_load.py` + `audit/report.py` | 30 min |

**Celkový odhad:** ~2 hodiny

---

## Čo sa NEbude opravovať (N/A pre lokálny nástroj)

- SPF / DMARC → nie je doména
- HTTP/3 / QUIC → vyžaduje TLS
- CDN → lokálna sieť
- TLS / HSTS / Heartbleed → HTTP lokálne (riešiť na reverse proxy úrovni)
- GDPR compliance (Cookie banner, PP, ODR, ToS) → nástroj nezbiera osobné dáta, nie je e-commerce
