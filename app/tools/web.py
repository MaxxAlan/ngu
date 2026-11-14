"""Web search tool using DuckDuckGo Lite (no API key required)."""
from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request

from .common import ToolError, clip


def web_search(query: str, max_results: int = 5) -> str:
    """Search DuckDuckGo for a query. Returns formatted results with title, snippet and URL.
    No API key required. Uses DuckDuckGo Lite HTML endpoint.
    """
    if not query or not query.strip():
        raise ToolError("query must not be empty")
    max_results = max(1, min(int(max_results), 20))
    encoded = urllib.parse.urlencode({"q": query})
    url = f"https://lite.duckduckgo.com/lite/?{encoded}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "vi,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise ToolError(f"web_search failed to fetch results: {exc}") from exc

    results = _parse_ddg_lite(raw, max_results)
    if not results:
        # Fallback: try instant answer JSON API
        try:
            results = _ddg_instant(query, max_results)
        except Exception:
            pass

    if not results:
        return f"No results found for: {query}"

    lines = [f"### Web Search: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"**{i}. {r['title']}**")
        if r.get("url"):
            lines.append(f"🔗 {r['url']}")
        if r.get("snippet"):
            lines.append(r["snippet"])
        lines.append("")
    return clip("\n".join(lines), 6000)


def _parse_ddg_lite(html_text: str, max_results: int) -> list[dict]:
    """Parse DuckDuckGo Lite HTML results."""
    results: list[dict] = []
    # DuckDuckGo Lite result structure: <tr> blocks with links and snippets
    # Find result links
    link_pattern = re.compile(
        r'<a[^>]+class="[^"]*result-link[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        re.S | re.I,
    )
    snippet_pattern = re.compile(
        r'<td[^>]+class="[^"]*result-snippet[^"]*"[^>]*>(.*?)</td>',
        re.S | re.I,
    )

    links = link_pattern.findall(html_text)
    snippets = snippet_pattern.findall(html_text)

    for i, (href, title_html) in enumerate(links[:max_results]):
        title = _strip_tags(title_html).strip()
        url = _resolve_ddg_url(href)
        snippet = _strip_tags(snippets[i]).strip() if i < len(snippets) else ""
        if title or url:
            results.append({"title": title or url, "url": url, "snippet": snippet})

    # Fallback: generic link extraction
    if not results:
        for m in re.finditer(r'href="(https?://[^"]+)"[^>]*>\s*<b>(.*?)</b>', html_text, re.S):
            if len(results) >= max_results:
                break
            url = m.group(1)
            title = _strip_tags(m.group(2)).strip()
            results.append({"title": title or url, "url": url, "snippet": ""})

    return results


def _ddg_instant(query: str, max_results: int) -> list[dict]:
    """DuckDuckGo Instant Answer API (JSON)."""
    encoded = urllib.parse.urlencode({
        "q": query,
        "format": "json",
        "no_redirect": "1",
        "no_html": "1",
        "skip_disambig": "1",
    })
    url = f"https://api.duckduckgo.com/?{encoded}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "local-agents/1.0 (web_search tool)"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        import json
        data = json.loads(resp.read().decode("utf-8", errors="replace"))

    results = []
    abstract = data.get("AbstractText") or ""
    abstract_url = data.get("AbstractURL") or ""
    abstract_title = data.get("Heading") or query
    if abstract:
        results.append({
            "title": abstract_title,
            "url": abstract_url,
            "snippet": abstract[:500],
        })

    for item in (data.get("RelatedTopics") or [])[:max_results]:
        if isinstance(item, dict) and item.get("Text"):
            results.append({
                "title": item.get("Text", "")[:80],
                "url": item.get("FirstURL", ""),
                "snippet": item.get("Text", "")[:300],
            })
            if len(results) >= max_results:
                break

    return results


def _strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    clean = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(clean)


def _resolve_ddg_url(href: str) -> str:
    """Resolve DuckDuckGo redirect URLs to actual URLs."""
    if href.startswith("//duckduckgo.com/l/?"):
        m = re.search(r"[?&]uddg=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))
    if href.startswith("http"):
        return href
    return href
