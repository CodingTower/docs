#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


BASE_URL = "https://docs.zenopus.dev"
SITEMAP_URL = f"{BASE_URL}/sitemap.xml"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ASSETS = ROOT / "images" / "zenopus"
INVENTORY_DIR = ROOT / ".zenopus-import"
SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (compatible; docs-migration/1.0; +https://docs.zenopus.dev)",
    }
)

LINK_REWRITES = {
    f"{BASE_URL}/faq": "/introduction/faq",
    f"{BASE_URL}/features/project-analytics": "/features/analytics",
    f"{BASE_URL}/features/precision-edit#visual-edits": "/features/design",
    f"{BASE_URL}/features/precision-edit#knowledge-files": "/features/knowledge",
    f"{BASE_URL}/tips-tricks/prompting": "/prompting/prompting-one",
    f"{BASE_URL}/user-guides/quickstart#remix-an-existing-project:~:text=you%20to%20reuse-,the,-current%20state%20of": "/introduction/faq#how-do-i-copy-remix-a-project",
}


def fetch_text(url: str) -> str:
    response = SESSION.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def fetch_bytes(url: str) -> bytes:
    response = SESSION.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def get_sitemap_urls() -> list[str]:
    xml = fetch_text(SITEMAP_URL)
    return re.findall(r"<loc>(.*?)</loc>", xml)


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path or "index"


def safe_filename(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name or "asset"


def decode_escaped_json_fragment(fragment: str):
    return json.loads("{" + fragment + "}")


def extract_balanced_object(raw: str, marker: str) -> str:
    start = raw.find(marker)
    if start == -1:
        raise ValueError(f"Could not find marker: {marker}")
    i = start + len(marker)
    if raw[i] != "{":
        raise ValueError(f"Marker {marker} did not start with object")
    depth = 0
    out = []
    while i < len(raw):
        ch = raw[i]
        out.append(ch)
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return "".join(out)


def extract_balanced_array(raw: str, marker: str) -> str:
    start = raw.find(marker)
    if start == -1:
        raise ValueError(f"Could not find marker: {marker}")
    i = start + len(marker)
    if raw[i] != "[":
        raise ValueError(f"Marker {marker} did not start with array")
    depth = 0
    out = []
    while i < len(raw):
        ch = raw[i]
        out.append(ch)
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return "".join(out)


def unescape_json_fragment(fragment: str) -> str:
    return fragment.replace(r"\"", '"')


def extract_site_config(raw_html: str) -> dict:
    navigation = json.loads(unescape_json_fragment(extract_balanced_object(raw_html, r"\"navigation\":")))
    redirects = json.loads(unescape_json_fragment(extract_balanced_array(raw_html, r"\"redirects\":")))
    soup = BeautifulSoup(raw_html, "html.parser")

    logo_match = re.search(
        r'<img class="nav-logo[^"]*dark:hidden[^"]*" src="([^"]+)"[^>]*alt="light logo"/>'
        r'.*?<img class="nav-logo[^"]*dark:block[^"]*" src="([^"]+)"[^>]*alt="dark logo"/>',
        raw_html,
        re.S,
    )
    if not logo_match:
        raise ValueError("Could not extract logo URLs")

    navbar_links = []
    nav = soup.find("nav")
    if nav:
        for anchor in nav.find_all("a", href=True):
            label = clean_text(anchor.get_text(" ", strip=True))
            if label:
                navbar_links.append({"label": label, "href": anchor["href"]})

    socials = {}
    footer = soup.find(id="footer")
    if footer:
        for anchor in footer.find_all("a", href=True):
            sr = anchor.find("span", class_="sr-only")
            if sr:
                socials[clean_text(sr.get_text(" ", strip=True))] = anchor["href"]

    style_match = re.search(
        r"--primary:\s*([0-9 ]+);.*?--primary-light:\s*([0-9 ]+);.*?--primary-dark:\s*([0-9 ]+);",
        raw_html,
        re.S,
    )
    if not style_match:
        raise ValueError("Could not extract brand colors")

    def rgb_to_hex(value: str) -> str:
        parts = [int(part) for part in value.split()]
        return "#{:02X}{:02X}{:02X}".format(*parts)

    favicon_png_match = re.search(
        r'<link rel="icon" href="([^"]+favicon-32x32\.png[^"]*)".*?media="\(prefers-color-scheme: light\)"',
        raw_html,
    )
    favicon_match = re.search(r'<link rel="shortcut icon" href="([^"]+favicon\.ico[^"]*)"', raw_html)
    og_match = re.search(r'<meta property="og:image" content="([^"]+)"', raw_html)

    return {
        "navigation": navigation,
        "redirects": redirects,
        "navbar_links": navbar_links,
        "socials": socials,
        "colors": {
            "primary": rgb_to_hex(style_match.group(1)),
            "light": rgb_to_hex(style_match.group(2)),
            "dark": rgb_to_hex(style_match.group(3)),
        },
        "logo_light": html.unescape(logo_match.group(1)),
        "logo_dark": html.unescape(logo_match.group(2)),
        "favicon_png": html.unescape(favicon_png_match.group(1)) if favicon_png_match else "",
        "favicon": html.unescape(favicon_match.group(1)) if favicon_match else "",
        "og_image": html.unescape(og_match.group(1)) if og_match else "",
    }


def escape_yaml(value: str) -> str:
    return value.replace('"', '\\"')


def escape_jsx_attr(value: str) -> str:
    value = html.unescape(value).replace("\u200b", "")
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def escape_mdx_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\u200b", "")
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("{", "\\{").replace("}", "\\}")
    return text


def clean_text(text: str) -> str:
    text = html.unescape(text).replace("\u200b", "")
    return re.sub(r"\s+", " ", text).strip()


def normalize_href(href: str) -> str:
    href = html.unescape(href).strip()
    if not href:
        return ""
    if href in LINK_REWRITES:
        return LINK_REWRITES[href]
    if href.startswith("/zenopus-f9060f1e/"):
        return ""
    if href.startswith(BASE_URL):
        href = href[len(BASE_URL):] or "/"
        if not href.startswith("/"):
            href = "/" + href
    return href


def inline_text(node) -> str:
    if isinstance(node, NavigableString):
        return escape_mdx_text(str(node))
    if not isinstance(node, Tag):
        return ""

    if node.name == "br":
        return "  \n"
    if node.name in {"strong", "b"}:
        return f"**{''.join(inline_text(c) for c in node.children).strip()}**"
    if node.name in {"em", "i"}:
        return f"*{''.join(inline_text(c) for c in node.children).strip()}*"
    if node.name == "code":
        return f"`{clean_text(node.get_text())}`"
    if node.name == "a":
        href = normalize_href(node.get("href", ""))
        label = "".join(inline_text(c) for c in node.children).strip() or href
        return f"[{label}]({href})" if href else label
    if node.name == "img":
        alt = escape_mdx_text(node.get("alt", "").strip())
        src = node.get("src", "").strip()
        return f"![{alt}]({src})"

    return "".join(inline_text(c) for c in node.children)


def extract_lines_from_code(code_tag: Tag) -> str:
    lines = code_tag.select(".line")
    if lines:
        return "\n".join(line.get_text("", strip=False) for line in lines).rstrip()
    return code_tag.get_text("", strip=False).rstrip()


def to_markdown_table(table: Tag) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        rows.append(
            [
                escape_mdx_text(clean_text(cell.get_text(" ", strip=True))).replace("|", "\\|")
                for cell in cells
            ]
        )

    if not rows:
        return ""

    header = rows[0]
    body = rows[1:] or [["" for _ in header]]
    header_line = "| " + " | ".join(header) + " |"
    divider = "| " + " | ".join("---" for _ in header) + " |"
    body_lines = ["| " + " | ".join(row + [""] * (len(header) - len(row))) + " |" for row in body]
    return "\n".join([header_line, divider, *body_lines])


def download_asset(url: str, page_slug: str) -> str:
    if not url:
        return url

    url = urljoin(BASE_URL, url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return url

    path = Path(parsed.path)
    ext = path.suffix or ".bin"
    name = safe_filename(path.name or "asset" + ext)
    local_dir = OUTPUT_ASSETS / page_slug
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / name
    if not local_path.exists():
        local_path.write_bytes(fetch_bytes(url))
    return "/" + str(local_path.relative_to(ROOT)).replace(os.sep, "/")


def replace_embeds(raw_html: str, soup: BeautifulSoup, content: Tag) -> None:
    embeds = [
        embed.rstrip("\\")
        for embed in re.findall(r"https://www\.youtube\.com/embed/[^\"]+?\\", raw_html)
    ]
    placeholders = content.find_all(attrs={"data-as": "iframe"})
    for idx, placeholder in enumerate(placeholders):
        if idx >= len(embeds):
            continue
        iframe = soup.new_tag("iframe")
        iframe["src"] = embeds[idx]
        iframe["title"] = "YouTube video player"
        iframe["width"] = "100%"
        iframe["height"] = "315"
        iframe["frameBorder"] = "0"
        iframe["allow"] = "accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
        iframe["referrerPolicy"] = "strict-origin-when-cross-origin"
        iframe["allowFullScreen"] = "true"
        placeholder.replace_with(iframe)


def localize_images(content: Tag, page_slug: str) -> None:
    for img in content.find_all("img"):
        src = img.get("src")
        if not src:
            continue
        img["src"] = download_asset(src, page_slug)


def serialize_list(node: Tag, ordered: bool = False, depth: int = 0) -> str:
    lines = []
    marker = "1." if ordered else "-"
    for item in node.find_all("li", recursive=False):
        child_blocks: list[str] = []
        inline_parts: list[str] = []
        for child in item.children:
            if isinstance(child, NavigableString):
                if child.strip():
                    inline_parts.append(inline_text(child))
                continue
            if isinstance(child, Tag) and child.name in {"ul", "ol"}:
                child_blocks.append(serialize_list(child, ordered=child.name == "ol"))
            elif isinstance(child, Tag) and is_block_like(child):
                if child.name == "p" and not child_blocks:
                    text = "".join(inline_text(c) for c in child.children).strip()
                    if text:
                        inline_parts.append(text)
                    continue
                block = serialize_blocks([child], depth + 1).strip()
                if block:
                    child_blocks.append(block)
            else:
                inline_parts.append(inline_text(child))
        inline = "".join(inline_parts).strip()
        list_indent = "  " * depth
        child_indent = list_indent + "    "
        if inline:
            lines.append(f"{list_indent}{marker} {inline}")
        else:
            lines.append(f"{list_indent}{marker}")
        for block in child_blocks:
            if not block:
                continue
            for extra in block.splitlines():
                lines.append(f"{child_indent}{extra}" if extra else child_indent.rstrip())
    return "\n".join(lines)


def serialize_callout(node: Tag) -> str:
    kind = (node.get("data-callout-type") or "note").strip().lower()
    component = {
        "note": "Note",
        "tip": "Tip",
        "info": "Info",
        "warning": "Warning",
        "check": "Check",
    }.get(kind, "Note")
    body = serialize_blocks(node.select('[data-component-part="callout-content"]')[0].children).strip()
    return f"<{component}>\n{body}\n</{component}>"


def serialize_code_block(node: Tag) -> str:
    code = node.find("code")
    if not code:
        return ""
    language = code.get("language") or node.get("language") or "text"
    language = language.replace("shellscript", "bash")
    body = extract_lines_from_code(code)
    fence = "```"
    return f"{fence}{language}\n{body}\n{fence}"


def serialize_code_group(node: Tag) -> str:
    titles = [clean_text(tag.get_text(" ", strip=True)) for tag in node.select('[data-component-part="code-group-tab-bar"] [role="tab"]')]
    codes = node.select('[data-component-part="code-group-tab-content"] pre code')
    parts = ["<CodeGroup>"]
    for idx, code in enumerate(codes):
        language = (code.get("language") or "text").replace("shellscript", "bash")
        title = titles[idx] if idx < len(titles) else f"Example {idx + 1}"
        body = extract_lines_from_code(code)
        parts.append(f'```{language} title="{escape_yaml(title)}"')
        parts.append(body)
        parts.append("```")
    parts.append("</CodeGroup>")
    return "\n".join(parts)


def serialize_steps(node: Tag) -> str:
    parts = ["<Steps>"]
    for step in node.select(".step-container"):
        title_node = step.select_one('[data-component-part="step-title"]')
        content_node = step.select_one('[data-component-part="step-content"]')
        title = clean_text(title_node.get_text(" ", strip=True)) if title_node else "Step"
        body = serialize_blocks(content_node.children if content_node else []).strip()
        parts.append(f'<Step title="{escape_jsx_attr(title)}">')
        if body:
            parts.append(body)
        parts.append("</Step>")
    parts.append("</Steps>")
    return "\n".join(parts)


def serialize_accordion(details_nodes: list[Tag]) -> str:
    wrapper = len(details_nodes) > 1
    parts = ["<AccordionGroup>"] if wrapper else []
    for details in details_nodes:
        title_node = details.select_one('[data-component-part="accordion-title"]')
        content_node = details.select_one('[data-component-part="accordion-content"]')
        title = clean_text(title_node.get_text(" ", strip=True)) if title_node else "Details"
        body = serialize_blocks(content_node.children if content_node else []).strip()
        parts.append(f'<Accordion title="{escape_jsx_attr(title)}">')
        if body:
            parts.append(body)
        parts.append("</Accordion>")
    if wrapper:
        parts.append("</AccordionGroup>")
    return "\n".join(parts)


def serialize_tabs(node: Tag) -> str:
    titles = [clean_text(tag.get_text(" ", strip=True)) for tag in node.select('[data-component-part="tab-button"]')]
    panels = node.select('[data-component-part="tab-content"]')
    parts = ["<Tabs>"]
    for idx, panel in enumerate(panels):
        title = titles[idx] if idx < len(titles) else f"Tab {idx + 1}"
        body = serialize_blocks(panel.children).strip()
        parts.append(f'<Tab title="{escape_jsx_attr(title)}">')
        if body:
            parts.append(body)
        parts.append("</Tab>")
    parts.append("</Tabs>")
    return "\n".join(parts)


def serialize_card_group(nodes: list[Tag]) -> str:
    wrapper = len(nodes) > 1
    parts = ['<CardGroup cols={2}>'] if wrapper else []
    for node in nodes:
        href = node.get("href", "")
        title_node = node.select_one('[data-component-part="card-title"]')
        content_node = node.select_one('[data-component-part="card-content"]')
        title = clean_text(title_node.get_text(" ", strip=True)) if title_node else "Card"
        body = serialize_blocks(content_node.children if content_node else []).strip()
        attrs = [f'title="{escape_jsx_attr(title)}"']
        if href:
            attrs.append(f'href="{escape_jsx_attr(href)}"')
        parts.append(f"<Card {' '.join(attrs)}>")
        if body:
            parts.append(body)
        parts.append("</Card>")
    if wrapper:
        parts.append("</CardGroup>")
    return "\n".join(parts)


def serialize_update(node: Tag) -> str:
    label_node = node.select_one('[data-component-part="update-label"]')
    description_node = node.select_one('[data-component-part="update-description"]')
    content_node = node.select_one('[data-component-part="update-content"]')
    label = clean_text(label_node.get_text(" ", strip=True)) if label_node else ""
    description = clean_text(description_node.get_text(" ", strip=True)) if description_node else ""
    attrs = [f'label="{escape_jsx_attr(label)}"'] if label else []
    if description:
        attrs.append(f'description="{escape_jsx_attr(description)}"')
    body = serialize_blocks(content_node.children if content_node else []).strip()
    return "\n".join([f"<Update {' '.join(attrs)}>", body, "</Update>"]).strip()


def serialize_iframe(node: Tag) -> str:
    src = node.get("src", "")
    title = escape_jsx_attr(node.get("title", "Embedded content"))
    if not src:
        return ""
    return (
        f'<iframe src="{escape_jsx_attr(src)}" title="{title}" width="100%" height="{escape_jsx_attr(node.get("height", "315"))}" '
        'frameBorder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; '
        'picture-in-picture; web-share" referrerPolicy="strict-origin-when-cross-origin" allowFullScreen />'
    )


def is_block_like(node: Tag) -> bool:
    if not isinstance(node, Tag):
        return False
    classes = " ".join(node.get("class", []))
    return (
        node.name in {"p", "div", "section", "ul", "ol", "table", "pre", "details", "iframe", "img", "blockquote"}
        or "callout" in classes
        or "tabs" in classes
        or "steps" in classes
        or "code-group" in classes
        or "update-container" in classes
        or "card" in classes
    )


def serialize_block(node: Tag) -> str:
    classes = " ".join(node.get("class", []))

    if node.name in {"h2", "h3", "h4", "h5", "h6"}:
        level = int(node.name[1])
        text = clean_text(node.get_text(" ", strip=True))
        return f'{"#" * level} {text}'
    if node.name in {"p"} or (node.name == "span" and node.get("data-as") == "p"):
        text = "".join(inline_text(c) for c in node.children).strip()
        return text
    if node.name == "ul":
        return serialize_list(node, ordered=False)
    if node.name == "ol":
        return serialize_list(node, ordered=True)
    if node.name == "table":
        return to_markdown_table(node)
    if node.name == "pre":
        return serialize_code_block(node.parent if "code-block" in classes else node)
    if node.name == "iframe":
        return serialize_iframe(node)
    if node.name == "img":
        alt = escape_mdx_text(node.get("alt", "").strip())
        src = node.get("src", "").strip()
        return f"![{alt}]({src})"
    if node.name == "blockquote":
        text = serialize_blocks(node.children).strip().replace("\n", "\n> ")
        return f"> {text}"
    if node.name == "details" and "accordion" in classes:
        return serialize_accordion([node])
    if node.name == "div" and "callout" in classes:
        return serialize_callout(node)
    if node.name == "div" and "code-block" in classes:
        return serialize_code_block(node)
    if node.name == "div" and "code-group" in classes:
        return serialize_code_group(node)
    if node.name == "div" and "steps" in classes:
        return serialize_steps(node)
    if node.name == "div" and "tabs" in classes:
        return serialize_tabs(node)
    if node.name == "div" and "update-container" in classes:
        return serialize_update(node)
    if node.name == "a" and "card" in classes:
        return serialize_card_group([node])
    if node.name == "div" and node.get("data-component-part") == "code-block-root":
        return serialize_code_block(node)
    if node.name == "div":
        if node.get("data-floating-buttons") == "true":
            return ""
        table = node.find("table", recursive=False)
        if table:
            return to_markdown_table(table)
        return serialize_blocks(node.children).strip()

    return serialize_blocks(node.children).strip()


def serialize_blocks(nodes: Iterable, depth: int = 0) -> str:
    parts: list[str] = []
    nodes = [node for node in nodes if not (isinstance(node, NavigableString) and not node.strip())]
    i = 0
    while i < len(nodes):
        node = nodes[i]
        if isinstance(node, NavigableString):
            text = clean_text(str(node))
            if text:
                parts.append(escape_mdx_text(text))
            i += 1
            continue
        if not isinstance(node, Tag):
            i += 1
            continue

        classes = " ".join(node.get("class", []))

        if node.name == "details" and "accordion" in classes:
            group = [node]
            j = i + 1
            while j < len(nodes):
                sibling = nodes[j]
                if isinstance(sibling, Tag) and sibling.name == "details" and "accordion" in " ".join(sibling.get("class", [])):
                    group.append(sibling)
                    j += 1
                    continue
                if isinstance(sibling, NavigableString) and not sibling.strip():
                    j += 1
                    continue
                break
            parts.append(serialize_accordion(group))
            i = j
            continue

        if node.name == "a" and "card" in classes:
            group = [node]
            j = i + 1
            while j < len(nodes):
                sibling = nodes[j]
                if isinstance(sibling, Tag) and sibling.name == "a" and "card" in " ".join(sibling.get("class", [])):
                    group.append(sibling)
                    j += 1
                    continue
                if isinstance(sibling, NavigableString) and not sibling.strip():
                    j += 1
                    continue
                break
            parts.append(serialize_card_group(group))
            i = j
            continue

        rendered = serialize_block(node).strip()
        if rendered:
            parts.append(rendered)
        i += 1

    return "\n\n".join(part for part in parts if part)


@dataclass
class PageResult:
    url: str
    slug: str
    title: str
    description: str
    body: str
    images: list[str]


CONNECTOR_NAMES = {
    "contentful": "Contentful",
    "eleven-labs": "ElevenLabs",
    "firecrawl": "Firecrawl",
    "linear": "Linear",
    "perplexity": "Perplexity",
    "slack": "Slack",
    "telegram": "Telegram",
    "twilio": "Twilio",
    "twitch": "Twitch",
}


def indefinite_article(noun: str) -> str:
    return "an" if noun[:1].lower() in {"a", "e", "i", "o", "u"} else "a"


def postprocess_body(body: str, slug: str, title: str) -> str:
    connector = CONNECTOR_NAMES.get(Path(slug).name)
    if connector:
        article = indefinite_article(connector)
        body = body.replace("How to unlink projects from a  connection", f"How to unlink projects from {article} {connector} connection")
        body = body.replace("How to delete a  connection", f"How to delete {article} {connector} connection")
        body = body.replace("select  .", f"select **{connector}**.")
        body = body.replace(
            "When unlinked, those projects will no longer have access to   through this connection. If a project needs   again, you can link it to any available connection.",
            f"When unlinked, those projects will no longer have access to **{connector}** through this connection. If a project needs **{connector}** again, you can link it to any available connection.",
        )
        body = body.replace(
            "Workspace admins and owners can delete   connections.",
            f"Workspace admins and owners can delete **{connector}** connections.",
        )
    return body


def parse_page(url: str) -> PageResult:
    raw = fetch_text(url)
    soup = BeautifulSoup(raw, "html.parser")
    title_node = soup.find(id="page-title")
    title = clean_text(title_node.get_text(" ", strip=True)) if title_node else clean_text(soup.title.get_text().replace(" - ZenOpus Documentation", ""))

    desc_meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    description = desc_meta.get("content", "").strip() if desc_meta else ""

    content = soup.find(id="content")
    if not content:
        raise ValueError(f"Could not find content container for {url}")

    replace_embeds(raw, soup, content)
    page_slug = slug_from_url(url)
    localize_images(content, page_slug)

    body = serialize_blocks(content.children).strip()
    body = postprocess_body(body, page_slug, title)
    images = [img.get("src") for img in content.find_all("img") if img.get("src")]
    return PageResult(url=url, slug=page_slug, title=title, description=description, body=body, images=images)


def write_page(page: PageResult) -> None:
    path = ROOT / f"{page.slug}.mdx"
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = [
        "---",
        f'title: "{escape_yaml(page.title)}"',
        f'description: "{escape_yaml(page.description)}"',
        "---",
        "",
    ]
    path.write_text("\n".join(frontmatter) + page.body.strip() + "\n", encoding="utf-8")


def write_site_files(config: dict) -> None:
    OUTPUT_ASSETS.mkdir(parents=True, exist_ok=True)

    logo_light = download_asset(config["logo_light"], "_site")
    logo_dark = download_asset(config["logo_dark"], "_site")
    favicon_source = config.get("favicon_png") or config.get("favicon")
    favicon = download_asset(favicon_source, "_site") if favicon_source else "/favicon.ico"

    docs = {
        "$schema": "https://mintlify.com/docs.json",
        "theme": "mint",
        "name": "ZenOpus Documentation",
        "colors": config["colors"],
        "favicon": favicon,
        "navigation": config["navigation"],
        "logo": {"light": logo_light, "dark": logo_dark},
        "navbar": {"links": config["navbar_links"]},
        "footer": {"socials": config["socials"]},
        "redirects": config["redirects"],
    }
    (INVENTORY_DIR / "docs.generated.json").write_text(json.dumps(docs, indent=2), encoding="utf-8")


def run_inventory() -> None:
    INVERT = INVENTORY_DIR
    INVERT.mkdir(parents=True, exist_ok=True)
    urls = get_sitemap_urls()
    root_html = fetch_text(f"{BASE_URL}/introduction/welcome")
    config = extract_site_config(root_html)
    write_site_files(config)
    pages = []
    for url in urls:
        page = parse_page(url)
        pages.append(
            {
                "url": page.url,
                "slug": page.slug,
                "title": page.title,
                "description": page.description,
                "images": page.images,
            }
        )
    (INVERT / "pages.json").write_text(json.dumps(pages, indent=2), encoding="utf-8")


def run_import(limit: int | None = None) -> None:
    INVERT = INVENTORY_DIR
    INVERT.mkdir(parents=True, exist_ok=True)
    urls = get_sitemap_urls()
    root_html = fetch_text(f"{BASE_URL}/introduction/welcome")
    config = extract_site_config(root_html)
    write_site_files(config)
    selected = urls[:limit] if limit else urls
    imported = []
    for url in selected:
        page = parse_page(url)
        write_page(page)
        imported.append({"url": page.url, "slug": page.slug, "title": page.title})
    (INVERT / "imported.json").write_text(json.dumps(imported, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["inventory", "import"])
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    if args.mode == "inventory":
        run_inventory()
    else:
        run_import(limit=args.limit)


if __name__ == "__main__":
    main()
