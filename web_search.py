# SPDX-License-Identifier: AGPL-3.0-or-later
"""Current-events web lookup (DuckDuckGo). Only triggered for questions that need
up-to-date facts; everything else stays fully local. Network failures return
None so the caller can explain that current information could not be verified."""
import re
import httpx
from bs4 import BeautifulSoup

# Questions matching these patterns need current information that the local
# model (training-data snapshot) cannot reliably answer. English and Spanish.
_TRIGGERS = [
    r"president", r"prime minister", r"presidente", r"primer ministro",
    r"\bnews\b", r"noticias", r"headline", r"happening in",
    r"weather", r"clima", r"temperatura", r"forecast",
    r"\btoday\b", r"\bthis week\b", r"\bthis month\b", r"\bthis year\b",
    r"\bright now\b", r"currently", r"actualmente", r"ahora mismo",
    r"latest", r"most recent", r"reciente", r"ultimo", r"último",
    r"who won", r"gan[oó]", r"score", r"resultado", r"standings",
    r"price of", r"stock", r"how much is", r"cu[aá]nto cuesta", r"cu[aá]nto vale",
    r"release date", r"cu[aá]ndo (sale|se estrena)",
    r"current ", r"election", r"elecci[oó]n",
]
_TRIGGERS_RE = re.compile("|".join("(?:" + t + ")" for t in _TRIGGERS), re.IGNORECASE)

# ...but these are personal-recall questions, not current-events ones.
_LOCAL_PATTERNS = [
    r"\b(my|mine|our|ours)\b", r"\bremember\b", r"\b(saved|recorded)\b",
    r"\b(conversation|transcript|meeting|chat)s?\b",
    r"\bwhat (?:did|have|had)\b.*\b(say|said|tell|told|discuss|agree|promise|decide)\b",
    r"\bwhat (?:i|we)\b", r"\b(i|we) (?:said|told|discussed|agreed|promised|decided)\b",
    r"\b(mi|mis|mío|mía|nuestro|nuestra)\b", r"recuerd", r"conversaci",
    r"\bqu[eé]\b.*\b(dijo|dije|dijimos|hablamos|acordamos|reuni[oó]n)\b",
    # Clock/calendar questions are answered from the machine's own time.
    r"what time", r"what (day|date|month|year) is", r"what's the (time|date)",
    r"qu[eé] hora", r"qu[eé] d[ií]a", r"cu[aá]l es la fecha",
]

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def needs_web(question):
    """True when the question asks for facts that may have changed recently."""
    q = " ".join(question.lower().split())
    if not _TRIGGERS_RE.search(q):
        return False
    return not any(re.search(p, q) for p in _LOCAL_PATTERNS)


def _ddg_results(query, limit=3):
    with httpx.Client(timeout=8, headers=_HEADERS, follow_redirects=True) as client:
        r = client.post("https://html.duckduckgo.com/html/", data={"q": query})
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for res in soup.select(".result")[:limit]:
        a = res.select_one(".result__a")
        if not a:
            continue
        url = a.get("href", "")
        m = re.search(r"uddg=([^&]+)", url)
        if m:
            from urllib.parse import unquote
            url = unquote(m.group(1))
        snippet = res.select_one(".result__snippet")
        out.append({"title": a.get_text(" ", strip=True), "url": url,
                    "snippet": snippet.get_text(" ", strip=True) if snippet else ""})
    return out


def _fetch_text(url, limit=2500):
    with httpx.Client(timeout=8, headers=_HEADERS, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split())
    return text[:limit]


def lookup(question):
    """Search the web for current facts. Returns (block, sources) or None.

    block: text to inject into the model prompt.
    sources: [{"title","url"}] for the sources listing."""
    try:
        results = _ddg_results(question)
        if not results:
            return None
        block = []
        sources = []
        # Fetch the best-looking page for detail; snippets cover the rest.
        fetch_candidates = [r for r in results
                            if any(k in r["url"] for k in ("wikipedia.org", "britannica.com",
                                                          "news", "reuters.com", "apnews.com"))]
        for r in results[:3]:
            block.append(f"- {r['title']}: {r['snippet']} ({r['url']})")
            sources.append({"title": r["title"], "url": r["url"]})
        if fetch_candidates:
            try:
                detail = _fetch_text(fetch_candidates[0]["url"])
                if len(detail) > 300:
                    block.append("DETAIL from " + fetch_candidates[0]["url"] + ":\n" + detail)
            except Exception:
                pass
        return "\n".join(block), sources
    except Exception:
        return None
