"""
Konfigurácia auditu — globálne nastavenia bez hardcoded URL.
URL sa predáva ako parameter priamo do každej fázy.
"""

USER_AGENT = "site-audit-bot/1.0"
TIMEOUT = 15          # sekundy pre HTTP requesty
CRAWL_LIMIT = 100     # max URL pre interný link crawl
SAMPLE_URLS = 10      # počet URL zo sitemap na vzorkovú kontrolu
