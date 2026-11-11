"""Energy guides: Markdown files in webapp/articles/ with a small YAML header, served as web pages.

Add a guide by creating webapp/articles/<slug>.md:
---
title: ...
summary: one sentence shown in the list
updated: YYYY-MM-DD      (the date you last checked the facts)
order: 7                 (position in the list; optional)
related: [other-slug]    (optional)
---
Markdown body...
"""
from __future__ import annotations

import datetime as dt
import html
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import markdown
import yaml

ARTICLES_DIR = Path(__file__).parent / "articles"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class Article:
    slug: str
    title: str
    summary: str
    updated: dt.date
    related: tuple[str, ...]
    order: int
    body_html: str
    reading_minutes: int


def parse(path: Path) -> Article:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{path.name}: missing '---' header")
    _, header, body = text.split("---\n", 2)
    meta = yaml.safe_load(header)
    for key in ("title", "summary", "updated"):
        if not meta.get(key):
            raise ValueError(f"{path.name}: header needs '{key}'")
    slug = path.stem
    if not SLUG.match(slug):
        raise ValueError(f"{path.name}: file name must be lowercase words joined by hyphens")
    words = len(re.findall(r"\w+", body))
    return Article(
        slug=slug, title=str(meta["title"]), summary=str(meta["summary"]),
        updated=meta["updated"] if isinstance(meta["updated"], dt.date) else dt.date.fromisoformat(str(meta["updated"])),
        related=tuple(meta.get("related") or ()),
        order=int(meta.get("order", 999)),
        body_html=markdown.markdown(body, extensions=["tables", "sane_lists"]),
        reading_minutes=max(1, math.ceil(words / 200)),
    )


@lru_cache(maxsize=1)
def load_all() -> dict[str, Article]:
    articles = {a.slug: a for a in (parse(p) for p in sorted(ARTICLES_DIR.glob("*.md")))}
    for a in articles.values():
        missing = [r for r in a.related if r not in articles]
        if missing:
            raise ValueError(f"{a.slug}: related article(s) not found: {missing}")
    return dict(sorted(articles.items(), key=lambda kv: (kv[1].order, -kv[1].updated.toordinal(), kv[1].title)))


def _page(title: str, description: str, content: str, current: str) -> str:
    def nav_link(href, label, key):
        aria = ' aria-current="page"' if key == current else ""
        return f'<a class="tab" href="{href}"{aria}>{label}</a>'
    return f"""<!doctype html>
<html lang="en-GB">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{html.escape(title)} · Off-peak</title>
  <meta name="description" content="{html.escape(description, quote=True)}">
  <meta name="theme-color" content="#17323F">
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="icon" href="/static/icon-192.png">
  <link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=Source+Sans+3:wght@400;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header class="top">
    <a class="brand" href="/"><img src="/static/icon-192.png" alt="" width="28" height="28">Off-peak</a>
    <nav class="tabs" aria-label="Sections">
      {nav_link("/", "Today", "today")}{nav_link("/#solar", "Solar calculator", "solar")}{nav_link("/guides", "Guides", "guides")}
    </nav>
  </header>
  <main>
{content}
  </main>
  <footer class="foot">
    <p>Guides are general information, not financial advice. Rules and prices change; check gov.uk and your supplier before deciding. Not affiliated with Octopus Energy.</p>
  </footer>
</body>
</html>
"""


def _date(d: dt.date) -> str:
    return f"{d.day} {d:%B %Y}"


def index_page() -> str:
    items = "\n".join(
        f'<li><a href="/guides/{a.slug}"><h2>{html.escape(a.title)}</h2></a>'
        f'<p>{html.escape(a.summary)}</p><p class="meta">{a.reading_minutes} min read</p></li>'
        for a in load_all().values())
    content = f"""    <section class="hero"><p class="hero-lead">Guides</p>
      <h1 class="hero-time guides-title">Use energy when it's cheap</h1>
      <p class="hero-sub">Short, practical guides on smart tariffs, timing, solar, batteries and heat pumps.</p></section>
    <ul class="guides">
{items}
    </ul>"""
    return _page("Guides", "Practical guides on smart tariffs, solar, batteries and heat pumps.", content, "guides")


def article_page(slug: str) -> str | None:
    articles = load_all()
    a = articles.get(slug)
    if a is None:
        return None
    related = "".join(
        f'<li><a href="/guides/{r}">{html.escape(articles[r].title)}</a></li>' for r in a.related)
    content = f"""    <article class="article">
      <p class="meta"><a href="/guides">All guides</a></p>
      <h1>{html.escape(a.title)}</h1>
      <p class="lede">{html.escape(a.summary)}</p>
      <p class="meta">Last checked {_date(a.updated)} · {a.reading_minutes} min read</p>
{a.body_html}
      {f'<aside class="related"><h2>Read next</h2><ul>{related}</ul></aside>' if related else ''}
    </article>"""
    return _page(a.title, a.summary, content, "guides")


def not_found_page() -> str:
    content = """    <section class="hero"><h1 class="hero-time guides-title">Guide not found</h1>
      <p class="hero-sub">That guide doesn't exist or has moved. <a href="/guides">See all guides</a>.</p></section>"""
    return _page("Guide not found", "Guide not found", content, "guides")
