"""
Fáza 8 — GEO: Generative Engine Optimization
Kontroluje pripravenosť stránky pre AI-poháňané vyhľadávače (ChatGPT, Perplexity, Google AI).
"""

import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from rich.console import Console

PHASE_NUMBER = "08"
PHASE_NAME = "geo"

console = Console()

# AI crawleri, ktorých prístup kontrolujeme v robots.txt
AI_CRAWLERS = [
    "GPTBot",             # OpenAI ChatGPT
    "Google-Extended",    # Google AI Overviews / SGE
    "PerplexityBot",      # Perplexity AI
    "ClaudeBot",          # Anthropic Claude
    "anthropic-ai",       # Anthropic alternatívny UA
    "Bytespider",         # ByteDance / TikTok AI
    "cohere-ai",          # Cohere
    "Meta-ExternalAgent", # Meta AI
    "YouBot",             # You.com
    "Amazonbot",          # Amazon AI
]

# JSON-LD typy schém, ktoré aktívne pomáhajú GEO
VALUABLE_SCHEMA_TYPES = {
    "Organization", "Person", "LocalBusiness", "WebSite",
    "Article", "BlogPosting", "NewsArticle", "WebPage",
    "FAQPage", "HowTo", "Product", "Service",
    "BreadcrumbList", "Event", "Recipe", "Review", "AggregateRating",
}


def _parse_robots(robots_content: str) -> dict[str, dict]:
    """Parsuje robots.txt do {user_agent: {'disallow': [...], 'allow': [...]}}."""
    agent_rules: dict[str, dict] = {}
    current_agents: list[str] = []

    for raw_line in robots_content.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            continue
        if not line:
            current_agents = []
            continue
        if ":" not in line:
            continue

        directive, _, value = line.partition(":")
        directive = directive.strip().lower()
        value = value.strip()

        if directive == "user-agent":
            current_agents.append(value)
            for agent_name in current_agents:
                agent_rules.setdefault(agent_name, {"disallow": [], "allow": []})
        elif directive == "disallow" and current_agents:
            for agent_name in current_agents:
                agent_rules.setdefault(agent_name, {"disallow": [], "allow": []})
                if value:
                    agent_rules[agent_name]["disallow"].append(value)
        elif directive == "allow" and current_agents:
            for agent_name in current_agents:
                agent_rules.setdefault(agent_name, {"disallow": [], "allow": []})
                if value:
                    agent_rules[agent_name]["allow"].append(value)

    return agent_rules


def check_ai_crawler_access(target_url: str) -> dict:
    """Kontroluje robots.txt na pravidlá pre AI crawlerov."""
    parsed_url = urlparse(target_url)
    robots_url = f"{parsed_url.scheme}://{parsed_url.netloc}/robots.txt"
    robots_content = ""

    try:
        with httpx.Client(timeout=10, follow_redirects=True) as http_client:
            response = http_client.get(robots_url)
            if response.status_code == 200:
                robots_content = response.text
    except Exception:
        pass

    agent_rules = _parse_robots(robots_content)
    wildcard_rules = agent_rules.get("*", {"disallow": [], "allow": []})
    wildcard_blocks_all = "/" in wildcard_rules["disallow"]

    crawler_status: dict[str, dict] = {}
    for crawler_name in AI_CRAWLERS:
        matched_key = next(
            (key for key in agent_rules if key.lower() == crawler_name.lower()), None
        )
        explicit_rules = agent_rules.get(matched_key, {}) if matched_key else {}
        is_explicitly_blocked = "/" in explicit_rules.get("disallow", [])
        is_explicitly_mentioned = bool(matched_key)
        is_blocked_by_wildcard = wildcard_blocks_all and not is_explicitly_mentioned

        crawler_status[crawler_name] = {
            "explicitly_mentioned": is_explicitly_mentioned,
            "blocked": is_explicitly_blocked,
            "blocked_by_wildcard": is_blocked_by_wildcard,
            "allowed": (
                (is_explicitly_mentioned and not is_explicitly_blocked)
                or (not is_explicitly_mentioned and not wildcard_blocks_all)
            ),
        }

    blocked_count = sum(
        1 for status in crawler_status.values()
        if status["blocked"] or status["blocked_by_wildcard"]
    )

    return {
        "robots_url": robots_url,
        "robots_found": bool(robots_content),
        "crawlers": crawler_status,
        "blocked_count": blocked_count,
        "allowed_count": len(AI_CRAWLERS) - blocked_count,
    }


def analyze_structured_data(soup: BeautifulSoup) -> dict:
    """Analyzuje JSON-LD štruktúrované dáta z pohľadu GEO signálov."""
    schemas: list[dict] = []

    for script_tag in soup.find_all("script", type="application/ld+json"):
        try:
            raw_data = json.loads(script_tag.string or "{}")
            if isinstance(raw_data, dict):
                items = raw_data.get("@graph", [raw_data])
            else:
                items = raw_data
            if not isinstance(items, list):
                items = [items]

            for item in items:
                schema_type = item.get("@type", "Unknown")
                if isinstance(schema_type, list):
                    schema_type = schema_type[0]

                schema_info: dict = {
                    "type": schema_type,
                    "has_name": bool(item.get("name")),
                    "has_description": bool(item.get("description")),
                    "has_url": bool(item.get("url")),
                    "has_image": bool(item.get("image")),
                }

                if schema_type in ("Article", "BlogPosting", "NewsArticle"):
                    schema_info["has_author"] = bool(item.get("author"))
                    schema_info["has_date_published"] = bool(item.get("datePublished"))
                    schema_info["has_date_modified"] = bool(item.get("dateModified"))
                    schema_info["has_headline"] = bool(item.get("headline"))
                    schema_info["has_publisher"] = bool(item.get("publisher"))

                if schema_type in ("Organization", "LocalBusiness"):
                    schema_info["has_logo"] = bool(item.get("logo"))
                    same_as_value = item.get("sameAs", [])
                    schema_info["same_as_count"] = (
                        len(same_as_value) if isinstance(same_as_value, list)
                        else (1 if same_as_value else 0)
                    )
                    schema_info["has_contact"] = bool(item.get("contactPoint"))

                if schema_type == "FAQPage":
                    faq_entities = item.get("mainEntity", [])
                    schema_info["question_count"] = (
                        len(faq_entities) if isinstance(faq_entities, list) else 0
                    )

                if schema_type == "Person":
                    schema_info["has_job_title"] = bool(item.get("jobTitle"))
                    schema_info["has_affiliation"] = bool(item.get("affiliation"))

                schemas.append(schema_info)
        except (json.JSONDecodeError, AttributeError):
            pass

    schema_types = [s["type"] for s in schemas]
    return {
        "total_schemas": len(schemas),
        "types_present": schema_types,
        "valuable_types": [t for t in schema_types if t in VALUABLE_SCHEMA_TYPES],
        "has_article_schema": any(
            t in ("Article", "BlogPosting", "NewsArticle") for t in schema_types
        ),
        "has_faq_schema": "FAQPage" in schema_types,
        "has_howto_schema": "HowTo" in schema_types,
        "has_organization_schema": any(
            t in ("Organization", "LocalBusiness") for t in schema_types
        ),
        "has_website_schema": "WebSite" in schema_types,
        "has_breadcrumbs_schema": "BreadcrumbList" in schema_types,
        "article_has_author": any(
            s.get("has_author")
            for s in schemas if s["type"] in ("Article", "BlogPosting", "NewsArticle")
        ),
        "article_has_dates": any(
            s.get("has_date_published") or s.get("has_date_modified")
            for s in schemas if s["type"] in ("Article", "BlogPosting", "NewsArticle")
        ),
        "organization_has_same_as": any(
            (s.get("same_as_count") or 0) > 0
            for s in schemas if s["type"] in ("Organization", "LocalBusiness")
        ),
        "schemas": schemas,
    }


def analyze_eeat_signals(soup: BeautifulSoup, page_url: str) -> dict:
    """Analyzuje E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness) signály."""
    author_elements = (
        soup.find_all(class_=re.compile(r"author|byline|writer", re.I))
        + soup.find_all(itemprop="author")
        + soup.find_all(rel="author")
    )

    date_elements = (
        soup.find_all("time")
        + soup.find_all(itemprop=re.compile(r"datePublished|dateModified", re.I))
        + soup.find_all(class_=re.compile(r"\bdate\b|\bpublished\b|\bmodified\b|\bupdated\b", re.I))
    )

    all_links = soup.find_all("a", href=True)
    link_texts = [anchor.get_text(strip=True).lower() for anchor in all_links]
    link_hrefs = [anchor.get("href", "").lower() for anchor in all_links]
    page_netloc = urlparse(page_url).netloc

    has_about_link = any(
        "about" in text or "o nás" in text or "/about" in href or "/o-nas" in href
        for text, href in zip(link_texts, link_hrefs)
    )
    has_contact_link = any(
        "contact" in text or "kontakt" in text or "/contact" in href or "/kontakt" in href
        for text, href in zip(link_texts, link_hrefs)
    )

    external_citation_links = [
        anchor for anchor in all_links
        if anchor.get("href", "").startswith("http")
        and urlparse(anchor["href"]).netloc not in ("", page_netloc)
    ]

    text_lower = soup.get_text(" ", strip=True).lower()
    credential_keywords = [
        "phd", "mba", "certified", "certifikovan", "expert", "specialist",
        "specializovan", "years experience", "rokov skúseností", "ing.", "mgr.",
    ]
    has_credentials_mention = any(keyword in text_lower for keyword in credential_keywords)

    return {
        "author_elements_count": len(author_elements),
        "has_author_info": len(author_elements) > 0,
        "date_elements_count": len(date_elements),
        "has_date_signals": len(date_elements) > 0,
        "has_time_element": bool(soup.find("time")),
        "has_about_link": has_about_link,
        "has_contact_link": has_contact_link,
        "external_citation_count": len(external_citation_links),
        "has_external_citations": len(external_citation_links) > 0,
        "has_credentials_mention": has_credentials_mention,
    }


def analyze_content_structure(soup: BeautifulSoup) -> dict:
    """Analyzuje štruktúru obsahu z pohľadu AI pochopiteľnosti a citovania."""
    semantic_elements = {
        "article": bool(soup.find("article")),
        "main": bool(soup.find("main")),
        "aside": bool(soup.find("aside")),
        "header": bool(soup.find("header")),
        "footer": bool(soup.find("footer")),
        "nav": bool(soup.find("nav")),
        "section": bool(soup.find("section")),
    }

    headings: dict[str, list[str]] = {}
    for heading_level in range(1, 5):
        headings[f"h{heading_level}"] = [
            heading_tag.get_text(strip=True)
            for heading_tag in soup.find_all(f"h{heading_level}")
        ]

    h2_and_h3_texts = headings.get("h2", []) + headings.get("h3", [])
    headings_with_questions = sum(1 for heading_text in h2_and_h3_texts if "?" in heading_text)

    body_tag = soup.find("body")
    page_text = body_tag.get_text(" ", strip=True) if body_tag else ""
    word_count = len(re.findall(r"\w+", page_text))
    text_lower = page_text.lower()

    has_summary = any(
        keyword in text_lower
        for keyword in [
            "tl;dr", "summary", "zhrnutie", "záver",
            "conclusion", "key takeaways", "kľúčové body",
        ]
    )

    html_tag = soup.find("html")
    lang_attribute = html_tag.get("lang") if html_tag else None

    return {
        "semantic_elements": semantic_elements,
        "semantic_score": sum(1 for is_present in semantic_elements.values() if is_present),
        "heading_counts": {level: len(texts) for level, texts in headings.items()},
        "h1_count": len(headings.get("h1", [])),
        "has_single_h1": len(headings.get("h1", [])) == 1,
        "headings_with_questions": headings_with_questions,
        "has_faq_like_structure": headings_with_questions >= 2,
        "table_count": len(soup.find_all("table")),
        "list_count": len(soup.find_all("ul")) + len(soup.find_all("ol")),
        "has_definition_lists": bool(soup.find("dl")),
        "word_count": word_count,
        "has_substantial_content": word_count >= 300,
        "has_summary_section": has_summary,
        "lang_attribute": lang_attribute,
        "has_lang_attribute": bool(lang_attribute),
    }


def check_ai_meta_tags(soup: BeautifulSoup) -> dict:
    """Kontroluje meta tagy relevantné pre AI dohľadateľnosť."""
    collected_meta: dict[str, str] = {}

    for meta_tag in soup.find_all("meta"):
        name = meta_tag.get("name", "").lower()
        content = meta_tag.get("content", "")
        property_attr = meta_tag.get("property", "").lower()

        if name == "robots":
            collected_meta["robots"] = content
        elif name == "googlebot":
            collected_meta["googlebot"] = content
        elif name == "author":
            collected_meta["author"] = content
        elif name == "article:published_time":
            collected_meta["article_published"] = content
        elif name == "article:modified_time":
            collected_meta["article_modified"] = content
        elif property_attr == "og:type":
            collected_meta["og_type"] = content
        elif property_attr in ("article:author", "og:article:author"):
            collected_meta["article_author"] = content

    robots_content = collected_meta.get("robots", "")
    is_indexable = "noindex" not in robots_content.lower()
    ai_blocked_by_meta = any(
        directive in robots_content.lower()
        for directive in ["noai", "noimageai", "noarchive"]
    )

    return {
        "meta_tags": collected_meta,
        "is_indexable": is_indexable,
        "ai_blocked_by_meta": ai_blocked_by_meta,
        "has_author_meta": "author" in collected_meta or "article_author" in collected_meta,
        "has_article_dates": (
            "article_published" in collected_meta or "article_modified" in collected_meta
        ),
        "og_type": collected_meta.get("og_type", ""),
    }


def main(target_url: str, results_dir: Path) -> dict:
    """
    Fáza 8 — GEO: Generative Engine Optimization.
    Analyzuje pripravenosť stránky pre AI-poháňané vyhľadávače.
    """
    console.print("\n[bold blue]Fáza 8 — GEO: Generative Engine Optimization[/bold blue]")
    console.print(f"Cieľ: {target_url}")

    audit_results: dict = {
        "target": target_url,
        "ai_crawler_access": {},
        "pages": [],
    }

    console.print("  Kontrolujem prístup AI crawlerov (robots.txt)…")
    audit_results["ai_crawler_access"] = check_ai_crawler_access(target_url)

    pages_to_check = [{"url": target_url, "label": "Homepage"}]

    # Pokus nájsť blog/článok stránku pre E-E-A-T analýzu
    try:
        with httpx.Client(
            timeout=15, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}
        ) as http_client:
            homepage_response = http_client.get(target_url)
            home_soup = BeautifulSoup(homepage_response.text, "lxml")
            blog_url_pattern = re.compile(
                r"/(blog|clanok|article|post|news|clanky|novinky|aktuality)/", re.I
            )
            for anchor in home_soup.find_all("a", href=True):
                full_href = urljoin(target_url, anchor["href"])
                if (
                    blog_url_pattern.search(full_href)
                    and urlparse(full_href).netloc == urlparse(target_url).netloc
                ):
                    pages_to_check.append({"url": full_href, "label": "Blog/Article stránka"})
                    break
    except Exception:
        pass

    with sync_playwright() as playwright_instance:
        browser = playwright_instance.chromium.launch(headless=True)

        for page_info in pages_to_check:
            page_url = page_info["url"]
            page_label = page_info["label"]
            console.print(f"  Analyzujem: {page_label} ({page_url})")

            try:
                browser_page = browser.new_page()
                browser_page.goto(page_url, wait_until="networkidle", timeout=30_000)
                html_content = browser_page.content()
                browser_page.close()

                soup = BeautifulSoup(html_content, "lxml")
                audit_results["pages"].append({
                    "url": page_url,
                    "label": page_label,
                    "structured_data": analyze_structured_data(soup),
                    "eeat_signals": analyze_eeat_signals(soup, page_url),
                    "content_structure": analyze_content_structure(soup),
                    "ai_meta_tags": check_ai_meta_tags(soup),
                })
            except Exception as error:
                console.print(
                    f"  [yellow]Upozornenie: Nepodarilo sa analyzovať {page_url}: {error}[/yellow]"
                )
                audit_results["pages"].append({
                    "url": page_url,
                    "label": page_label,
                    "error": str(error),
                })

        browser.close()

    output_file = results_dir / f"{PHASE_NUMBER}_{PHASE_NAME}.json"
    output_file.write_text(json.dumps(audit_results, indent=2, ensure_ascii=False))
    console.print(f"✓ Výsledok uložený: {output_file}")

    return audit_results


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Použitie: uv run python audit/08_geo.py https://example.com")
        sys.exit(1)

    cli_target = sys.argv[1]
    cli_output_dir = Path("results/cli_test")
    cli_output_dir.mkdir(parents=True, exist_ok=True)
    main(cli_target, cli_output_dir)
