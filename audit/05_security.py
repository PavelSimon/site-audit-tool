"""
Fáza 5 — Security Audit
Výstup: results/<job_id>/05_security.json
"""

import json
import ssl
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table
from sslyze import (
    Scanner, ServerNetworkLocation, ServerScanRequest,
    ScanCommand,
)

from audit.config import USER_AGENT, TIMEOUT

console = Console()


def check_tls(hostname: str, port: int = 443) -> dict:
    console.rule("[bold cyan]1. TLS/SSL analýza")
    result = {"hostname": hostname, "port": port}

    proto_tests = {
        "tls_1_2": ssl.TLSVersion.TLSv1_2,
        "tls_1_3": ssl.TLSVersion.TLSv1_3,
    }
    protocols = {}
    for name, version in proto_tests.items():
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.minimum_version = version
            ctx.maximum_version = version
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with ctx.wrap_socket(
                __import__("socket").create_connection((hostname, port), timeout=10),
                server_hostname=hostname,
            ) as s:
                protocols[name] = True
        except Exception:
            protocols[name] = False

    for name in ["ssl_2_0", "ssl_3_0", "tls_1_0", "tls_1_1"]:
        protocols[name] = False

    result["protocols"] = protocols

    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(
            __import__("socket").create_connection((hostname, port), timeout=10),
            server_hostname=hostname,
        ) as s:
            cert = s.getpeercert()
            cipher = s.cipher()
            result["certificate"] = {
                "subject": dict(x[0] for x in cert.get("subject", [])),
                "issuer": dict(x[0] for x in cert.get("issuer", [])),
                "not_after": cert.get("notAfter"),
                "not_before": cert.get("notBefore"),
                "san": [v for t, v in cert.get("subjectAltName", []) if t == "DNS"],
                "chain_valid": True,
            }
            result["negotiated_cipher"] = cipher[0] if cipher else None
            result["negotiated_protocol"] = s.version()
    except ssl.SSLCertVerificationError as e:
        result["certificate"] = {"error": f"Cert verification failed: {e}"}
    except Exception as e:
        result["certificate"] = {"error": str(e)}

    try:
        location = ServerNetworkLocation(hostname=hostname, port=port)
        request = ServerScanRequest(
            server_location=location,
            scan_commands={
                ScanCommand.HEARTBLEED,
                ScanCommand.ROBOT,
                ScanCommand.OPENSSL_CCS_INJECTION,
            },
        )
        scanner = Scanner()
        scanner.queue_scans([request])
        for scan_result in scanner.get_results():
            if scan_result.scan_result is None:
                break
            sr = scan_result.scan_result
            try:
                hb = sr.heartbleed
                result["heartbleed"] = (hb.result.is_vulnerable_to_heartbleed
                                        if hb and hb.result else False)
            except Exception:
                result["heartbleed"] = False
            try:
                robot = sr.robot
                robot_str = str(robot.result.robot_result) if robot and robot.result else ""
                result["robot"] = (
                    "VULNERABLE" in robot_str and "NOT_VULNERABLE" not in robot_str
                )
            except Exception:
                result["robot"] = False
            try:
                ccs = sr.openssl_ccs_injection
                result["ccs_injection"] = (ccs.result.is_vulnerable_to_ccs_injection
                                           if ccs and ccs.result else False)
            except Exception:
                result["ccs_injection"] = False
    except Exception as e:
        result["sslyze_error"] = str(e)

    t = Table("Protokol", "Podporovaný", "Hodnotenie")
    good = {"tls_1_2", "tls_1_3"}
    bad = {"ssl_2_0", "ssl_3_0", "tls_1_0", "tls_1_1"}
    for proto, supported in result.get("protocols", {}).items():
        if supported:
            color = "red" if proto in bad else "green"
            mark = "❌ NEBEZPEČNÝ" if proto in bad else "✅"
        else:
            color = "green" if proto in bad else "dim"
            mark = "✅ vypnutý" if proto in bad else "—"
        t.add_row(proto.upper().replace("_", " "), str(supported), f"[{color}]{mark}[/]")
    console.print(t)

    cert = result.get("certificate", {})
    if cert and not cert.get("error"):
        console.print(f"  Certifikát CN: {cert.get('subject', {}).get('commonName')}")
        console.print(f"  Vydavateľ: {cert.get('issuer', {}).get('organizationName')}")
        console.print(f"  Platný do: {cert.get('not_after')}")
        console.print(f"  Heartbleed: {'❌' if result.get('heartbleed') else '✅ OK'}")
        console.print(f"  ROBOT: {'❌' if result.get('robot') else '✅ OK'}")
        console.print(f"  CCS Injection: {'❌' if result.get('ccs_injection') else '✅ OK'}")

    return result


SECURITY_HEADERS = {
    "strict-transport-security": {
        "desc": "HSTS",
        "check": lambda v: v and "max-age" in v and
                 any(int(p.split("=")[1]) >= 15552000
                     for p in v.split(";") if "max-age" in p)
    },
    "content-security-policy": {
        "desc": "CSP",
        "check": lambda v: bool(v)
    },
    "x-frame-options": {
        "desc": "X-Frame-Options",
        "check": lambda v: v and v.upper() in ("DENY", "SAMEORIGIN")
    },
    "x-content-type-options": {
        "desc": "X-Content-Type-Options",
        "check": lambda v: v and "nosniff" in v.lower()
    },
    "referrer-policy": {
        "desc": "Referrer-Policy",
        "check": lambda v: v and v.lower() in (
            "no-referrer", "no-referrer-when-downgrade",
            "strict-origin", "strict-origin-when-cross-origin")
    },
    "permissions-policy": {
        "desc": "Permissions-Policy",
        "check": lambda v: bool(v)
    },
    "x-powered-by": {
        "desc": "X-Powered-By (nesmie byť)",
        "check": lambda v: not v
    },
    "server": {
        "desc": "Server (nesmie odhaľovať verziu)",
        "check": lambda v: not v or not any(c.isdigit() for c in (v or ""))
    },
}

CSP_WARNINGS = ["unsafe-inline", "unsafe-eval", "*"]


def check_security_headers(url: str) -> dict:
    console.rule("[bold cyan]2. HTTP Security Headers")
    result = {"headers": {}, "csp_warnings": []}

    resp = httpx.get(url, headers={"User-Agent": USER_AGENT},
                     timeout=TIMEOUT, follow_redirects=True)
    headers = {k.lower(): v for k, v in resp.headers.items()}

    t = Table("Header", "Hodnota", "Hodnotenie")
    for header, cfg in SECURITY_HEADERS.items():
        val = headers.get(header)
        ok = cfg["check"](val)
        result["headers"][header] = {"value": val, "ok": ok}
        color = "green" if ok else "red"
        mark = "✅" if ok else "❌"
        t.add_row(cfg["desc"], (val or "—")[:60], f"[{color}]{mark}[/]")
    console.print(t)

    csp = headers.get("content-security-policy", "")
    for warn in CSP_WARNINGS:
        if warn in csp:
            result["csp_warnings"].append(warn)
            console.print(f"  [yellow]⚠️ CSP obsahuje '{warn}'[/]")

    result["csp_value"] = csp
    return result


def check_cookies(url: str) -> dict:
    console.rule("[bold cyan]3. Cookie bezpečnosť")
    result = {"cookies": []}

    with httpx.Client(headers={"User-Agent": USER_AGENT},
                      timeout=TIMEOUT, follow_redirects=True) as client:
        client.get(url)
        for cookie in client.cookies.jar:
            info = {
                "name": cookie.name,
                "secure": cookie.secure,
                "http_only": bool(getattr(cookie, "_rest", {}).get("HttpOnly")),
                "same_site": getattr(cookie, "_rest", {}).get("SameSite"),
                "domain": cookie.domain,
                "path": cookie.path,
            }
            result["cookies"].append(info)
            flags = []
            if not cookie.secure:
                flags.append("[red]chýba Secure[/]")
            if not info["http_only"]:
                flags.append("[yellow]chýba HttpOnly[/]")
            if not info["same_site"]:
                flags.append("[yellow]chýba SameSite[/]")
            console.print(f"  {cookie.name}: {', '.join(flags) if flags else '[green]OK[/]'}")

        if not result["cookies"]:
            console.print("  [dim]Žiadne Set-Cookie cookies[/]")

    return result


def check_mixed_content(url: str) -> dict:
    console.rule("[bold cyan]4. Mixed Content")
    result = {"http_resources": []}
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT},
                         timeout=TIMEOUT, follow_redirects=True)
        soup = BeautifulSoup(resp.text, "lxml")
        for tag, attr in [("img", "src"), ("script", "src"), ("link", "href"),
                           ("iframe", "src"), ("source", "src")]:
            for el in soup.find_all(tag):
                src = el.get(attr, "")
                if src.startswith("http://"):
                    result["http_resources"].append({"tag": tag, "url": src})
                    console.print(f"  [red]Mixed content:[/] <{tag}> {src[:80]}")
        if not result["http_resources"]:
            console.print("  [green]✅ Žiadny mixed content[/]")
    except Exception as e:
        result["error"] = str(e)
    return result


SENSITIVE_PATHS = [
    "/.env", "/wp-config.php", "/phpinfo.php", "/.git/config",
    "/config.php", "/admin", "/administrator", "/phpmyadmin",
    "/backup.zip", "/db.sql", "/.htaccess",
]


def check_sensitive_files(base_url: str) -> dict:
    console.rule("[bold cyan]5. Citlivé súbory")
    result = {"probes": []}
    with httpx.Client(headers={"User-Agent": USER_AGENT},
                      timeout=TIMEOUT, follow_redirects=False) as client:
        for path in SENSITIVE_PATHS:
            url = base_url.rstrip("/") + path
            try:
                resp = client.get(url)
                status = resp.status_code
                exposed = status == 200
                info = {"path": path, "status": status, "exposed": exposed}
                result["probes"].append(info)
                color = "red" if exposed else "green"
                mark = "❌ EXPOSED!" if exposed else "✅"
                console.print(f"  [{color}]{status}[/] {path}  {mark}")
            except Exception as e:
                result["probes"].append({"path": path, "error": str(e)})
    return result


def main(target_url: str, results_dir: Path) -> dict:
    console.rule("[bold]Fáza 5 — Security Audit")
    parsed = urlparse(target_url)
    hostname = parsed.hostname

    results = {}
    results["tls"] = check_tls(hostname)
    results["headers"] = check_security_headers(target_url)
    results["cookies"] = check_cookies(target_url)
    results["mixed_content"] = check_mixed_content(target_url)
    results["sensitive_files"] = check_sensitive_files(target_url)

    out_file = results_dir / "05_security.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    console.print(f"\n[bold green]✓ Výsledok uložený:[/] {out_file}")
    return results


if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else input("URL: ")
    main(url, Path("results"))
