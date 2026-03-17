#!/usr/bin/env python3

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
DOCS_JSON = ROOT / "docs.json"
LOCALES = {
    "de": "de",
    "fr": "fr",
    "it": "it",
    "es": "es",
    "jp": "ja",
}
CONTENT_DIRS = ["introduction", "features", "integrations", "prompting", "tips-tricks"]
CONTENT_FILES = ["AGENTS.mdx", "changelog.mdx", "glossary.mdx"]
ATTR_RE = re.compile(r'(?P<attr>\b(?:title|label|description|tab|group)\b)="(?P<value>[^"]*)"')
CODE_SPAN_RE = re.compile(r"`[^`]+`")
RAW_TAG_RE = re.compile(r"&lt;.*?&gt;|<[^>\n]+>")
MARKDOWN_LINK_RE = re.compile(r'(!?\[[^\]]*\])\(([^)]+)\)')
MULTISPACE_RE = re.compile(r"[A-Za-z]")
PROTECTED_TERMS = [
    "ZenOpus Cloud",
    "ZenOpus Support",
    "Community Support",
    "Support policy",
    "Plan mode",
    "Agent mode",
    "Code mode",
    "Prompt ZenOpus",
    "prompt ZenOpus",
    "Workspace",
    "workspace",
    "ZenOpus",
    "Support",
    "support",
    "Business",
    "Pro",
    "Free",
]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=ROOT, check=True)


def yaml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def attr_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def slugify_anchor(text: str) -> str:
    value = html.unescape(text).strip().lower()
    value = value.replace("&amp;", "&")
    value = re.sub(r"[()\"'`]", "", value)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[?!.:;]", "", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value


def should_translate(text: str) -> bool:
    return bool(text.strip()) and bool(MULTISPACE_RE.search(text))


def mask_specials(text: str) -> tuple[str, dict[str, str]]:
    placeholders: dict[str, str] = {}

    def replace(pattern: re.Pattern[str], current: str, prefix: str) -> str:
        def repl(match: re.Match[str]) -> str:
            key = f"__{prefix}{len(placeholders)}__"
            placeholders[key] = match.group(0)
            return key

        return pattern.sub(repl, current)

    def replace_markdown_links(current: str) -> str:
        def repl(match: re.Match[str]) -> str:
            key = f"__LINK{len(placeholders)}__"
            placeholders[key] = match.group(2)
            return f"{match.group(1)}({key})"

        return MARKDOWN_LINK_RE.sub(repl, current)

    def replace_protected_terms(current: str) -> str:
        protected = current
        for term in sorted(PROTECTED_TERMS, key=len, reverse=True):
            pattern = re.compile(rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])")

            def repl(match: re.Match[str]) -> str:
                key = f"__TERM{len(placeholders)}__"
                placeholders[key] = match.group(0)
                return key

            protected = pattern.sub(repl, protected)
        return protected

    masked = text
    masked = replace(CODE_SPAN_RE, masked, "CODE")
    masked = replace_markdown_links(masked)
    masked = replace_protected_terms(masked)
    masked = replace(RAW_TAG_RE, masked, "TAG")
    return masked, placeholders


def unmask_specials(text: str, placeholders: dict[str, str]) -> str:
    restored = text
    for key, value in placeholders.items():
        restored = restored.replace(key, value)
    return restored


def chunked(seq: list[str], size: int) -> list[list[str]]:
    return [seq[idx : idx + size] for idx in range(0, len(seq), size)]


def google_translate_batch(batch: list[str], target: str) -> list[str]:
    marker = "\n__SEGSEP__\n"
    joined = marker.join(batch)
    response = requests.get(
        "https://translate.googleapis.com/translate_a/single",
        params={
            "client": "gtx",
            "sl": "en",
            "tl": target,
            "dt": "t",
            "q": joined,
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    translated = "".join(part[0] for part in payload[0])
    parts = translated.split(marker)
    if len(parts) != len(batch):
        raise RuntimeError("Unexpected batch split size from translation response")
    return parts


def batch_translate(texts: list[str], target: str) -> dict[str, str]:
    translated: dict[str, str] = {}
    unique = []
    seen = set()
    for text in texts:
        if not should_translate(text):
            translated[text] = text
            continue
        if text not in seen:
            unique.append(text)
            seen.add(text)

    current: list[str] = []
    batches: list[list[str]] = []
    current_len = 0
    for item in unique:
        extra = len(item) + len("\n__SEGSEP__\n")
        if current and (len(current) >= 12 or current_len + extra > 3500):
            batches.append(current)
            current = []
            current_len = 0
        current.append(item)
        current_len += extra
    if current:
        batches.append(current)

    for batch in batches:
        for attempt in range(3):
            try:
                results = google_translate_batch(batch, target)
                for source, result in zip(batch, results):
                    translated[source] = result
                break
            except Exception:
                if attempt == 2:
                    for source in batch:
                        translated[source] = google_translate_batch([source], target)[0]
                else:
                    time.sleep(1.5 * (attempt + 1))

    return translated


@dataclass
class LineOp:
    kind: str
    raw: str
    text: str | None = None
    prefix: str = ""
    indent: str = ""
    trailing_spaces: str = ""
    attrs: list[tuple[str, str]] | None = None


def split_text_prefix(line: str) -> tuple[str, str]:
    indent_match = re.match(r"^\s*", line)
    prefix = indent_match.group(0)
    remainder = line[len(prefix) :]

    quote_prefix = ""
    while remainder.startswith(">"):
        match = re.match(r"^>\s*", remainder)
        if not match:
            break
        quote_prefix += match.group(0)
        remainder = remainder[match.end() :]

    marker_match = re.match(r"^(?:[-*+]\s+\[[ xX]\]\s+|\[[ xX]\]\s+|\d+\.\s+|[-*+]\s+)", remainder)
    if marker_match:
        prefix += quote_prefix + marker_match.group(0)
        remainder = remainder[marker_match.end() :]
    else:
        prefix += quote_prefix

    return prefix, remainder


def parse_file(path: Path) -> tuple[list[LineOp], list[str]]:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    ops: list[LineOp] = []
    texts: list[str] = []
    in_frontmatter = False
    in_code = False
    code_language = ""
    translate_code_block = False
    frontmatter_started = False

    for idx, line in enumerate(lines):
        if idx == 0 and line == "---":
            in_frontmatter = True
            frontmatter_started = True
            ops.append(LineOp(kind="static", raw=line))
            continue
        if in_frontmatter and line == "---":
            in_frontmatter = False
            ops.append(LineOp(kind="static", raw=line))
            continue
        stripped_line = line.lstrip()
        if stripped_line.startswith("```"):
            if not in_code:
                in_code = True
                code_language = stripped_line[3:].strip().split()[0] if stripped_line[3:].strip() else ""
                translate_code_block = code_language == "text"
                attrs = [(match.group("attr"), html.unescape(match.group("value"))) for match in ATTR_RE.finditer(line)]
                if attrs:
                    ops.append(LineOp(kind="tag_attrs", raw=line, attrs=attrs))
                    texts.extend(value for _, value in attrs)
                else:
                    ops.append(LineOp(kind="static", raw=line))
            else:
                in_code = False
                code_language = ""
                translate_code_block = False
                ops.append(LineOp(kind="static", raw=line))
            continue
        if in_code:
            if translate_code_block and line.strip():
                trailing = line[len(line.rstrip(" ")) :]
                prefix, text = split_text_prefix(line.rstrip(" "))
                ops.append(LineOp(kind="text", raw=line, text=text, prefix=prefix, trailing_spaces=trailing))
                texts.append(text)
            else:
                ops.append(LineOp(kind="static", raw=line))
            continue

        if in_frontmatter:
            if line.startswith('title: "'):
                text = line[len('title: "') : -1]
                ops.append(LineOp(kind="yaml_title", raw=line, text=text))
                texts.append(text)
            elif line.startswith('description: "'):
                text = line[len('description: "') : -1]
                ops.append(LineOp(kind="yaml_description", raw=line, text=text))
                texts.append(text)
            else:
                ops.append(LineOp(kind="static", raw=line))
            continue

        if not line.strip():
            ops.append(LineOp(kind="static", raw=line))
            continue

        heading_match = re.match(r"^(#{1,6}\s+)(.*?)(\s*)$", line)
        if heading_match:
            text = heading_match.group(2)
            trailing = heading_match.group(3)
            ops.append(
                LineOp(
                    kind="heading",
                    raw=line,
                    text=text,
                    indent=heading_match.group(1),
                    trailing_spaces=trailing,
                )
            )
            texts.append(text)
            continue

        stripped = line.strip()
        if stripped.startswith("<") and stripped.endswith(">"):
            attrs = [(match.group("attr"), html.unescape(match.group("value"))) for match in ATTR_RE.finditer(line)]
            if attrs:
                ops.append(LineOp(kind="tag_attrs", raw=line, attrs=attrs))
                texts.extend(value for _, value in attrs)
            else:
                ops.append(LineOp(kind="static", raw=line))
            continue

        trailing = line[len(line.rstrip(" ")) :]
        prefix, text = split_text_prefix(line.rstrip(" "))
        ops.append(LineOp(kind="text", raw=line, text=text, prefix=prefix, trailing_spaces=trailing))
        texts.append(text)

    if not frontmatter_started:
        raise RuntimeError(f"Expected frontmatter in {path}")

    return ops, texts


def translate_text_value(text: str, translations: dict[str, str]) -> str:
    if not should_translate(text):
        return text
    masked, placeholders = mask_specials(text)
    translated_masked = translations.get(masked, masked)
    return unmask_specials(translated_masked, placeholders)


def build_anchor_map_from_ops(ops: list[LineOp], translations: dict[str, str]) -> dict[str, str]:
    anchors: dict[str, str] = {}
    for op in ops:
        if op.kind == "heading" and op.text:
            old_slug = slugify_anchor(op.text)
            new_slug = slugify_anchor(translate_text_value(op.text, translations))
            if old_slug and new_slug:
                anchors[old_slug] = new_slug
        if op.kind == "tag_attrs" and op.attrs and op.raw.strip().startswith("<Accordion"):
            for attr, value in op.attrs:
                if attr == "title":
                    old_slug = slugify_anchor(value)
                    new_slug = slugify_anchor(translate_text_value(value, translations))
                    if old_slug and new_slug:
                        anchors[old_slug] = new_slug
    return anchors


def render_file(ops: list[LineOp], translations: dict[str, str]) -> str:
    out: list[str] = []
    for op in ops:
        if op.kind == "static":
            out.append(op.raw)
        elif op.kind == "yaml_title":
            out.append(f'title: "{yaml_escape(translate_text_value(op.text or "", translations))}"')
        elif op.kind == "yaml_description":
            out.append(f'description: "{yaml_escape(translate_text_value(op.text or "", translations))}"')
        elif op.kind == "heading":
            translated = translate_text_value(op.text or "", translations)
            out.append(f"{op.indent}{translated}{op.trailing_spaces}")
        elif op.kind == "tag_attrs":
            line = op.raw
            for attr, value in op.attrs or []:
                translated = attr_escape(translate_text_value(value, translations))
                line = line.replace(f'{attr}="{attr_escape(value)}"', f'{attr}="{translated}"')
                line = line.replace(f'{attr}="{value}"', f'{attr}="{translated}"')
            out.append(line)
        elif op.kind == "text":
            translated = translate_text_value(op.text or "", translations)
            out.append(f"{op.prefix}{translated}{op.trailing_spaces}")
        else:
            out.append(op.raw)
    return "\n".join(out) + "\n"


def normalize_translated_content(locale: str, path: Path, content: str) -> str:
    normalized = content
    common_rules = [
        (r"\bsupport\b", "Support"),
        (r"\bworkspace\b", "Workspace"),
        (r"\bCommunity support\b", "Community Support"),
        (r"\bcommunity support\b", "Community Support"),
    ]
    for pattern, replacement in common_rules:
        normalized = re.sub(pattern, replacement, normalized)

    restore_rules = [
        (r"https://zenopus\.dev/Support", "https://zenopus.dev/support"),
        (r"\bSupport@zenopus\.dev\b", "support@zenopus.dev"),
        (r"mailto:Support@zenopus\.dev", "mailto:support@zenopus.dev"),
        (r"/Workspace(?=[#)\"'])", "/workspace"),
    ]
    for pattern, replacement in restore_rules:
        normalized = re.sub(pattern, replacement, normalized)

    locale_common_rules = {
        "de": [
            (r"\bUnterstützung\b", "Support"),
        ],
    }
    for pattern, replacement in locale_common_rules.get(locale, []):
        normalized = re.sub(pattern, replacement, normalized)

    if path.name == "changelog.mdx":
        changelog_frontmatter = {
            "de": (
                'title: "ZenOpus Changelog"',
                'description: "ZenOpus Changelog und Produktupdates. Bleiben Sie über neue Funktionen, Verbesserungen und Fehlerbehebungen in ZenOpus auf dem Laufenden."',
            ),
            "fr": (
                'title: "ZenOpus Changelog"',
                'description: "ZenOpus Changelog et mises à jour produit. Restez informé des nouvelles fonctionnalités, améliorations et corrections de bugs publiées dans ZenOpus."',
            ),
            "it": (
                'title: "ZenOpus Changelog"',
                'description: "ZenOpus Changelog e aggiornamenti di prodotto. Rimani aggiornato su nuove funzionalità, miglioramenti e correzioni di bug rilasciati in ZenOpus."',
            ),
            "es": (
                'title: "ZenOpus Changelog"',
                'description: "ZenOpus Changelog y actualizaciones de producto. Manténgase al día con las nuevas funciones, mejoras y correcciones de errores publicadas en ZenOpus."',
            ),
            "jp": (
                'title: "ZenOpus 変更ログ"',
                'description: "ZenOpus 変更ログと製品の更新。ZenOpus で公開された新機能、改善点、バグ修正に関する最新情報を確認できます。"',
            ),
        }
        title_line, description_line = changelog_frontmatter[locale]
        normalized = re.sub(r'^title: ".*"$', title_line, normalized, count=1, flags=re.MULTILINE)
        normalized = re.sub(r'^description: ".*"$', description_line, normalized, count=1, flags=re.MULTILINE)
        changelog_body_rules = {
            "de": [
                (r"Das Änderungsprotokoll wird jetzt hier in der Dokumentation und nicht mehr an verstreuten Stellen veröffentlicht\.", "Der Changelog wird jetzt hier in der Dokumentation und nicht mehr an verstreuten Stellen veröffentlicht."),
            ],
            "it": [
                (r"Il registro delle modifiche ora è pubblicato qui nella documentazione anziché in luoghi sparsi\.", "Il Changelog è ora pubblicato qui nella documentazione anziché in luoghi sparsi."),
            ],
            "es": [
                (r"El registro de cambios ahora se publica aquí en la documentación en lugar de en lugares dispersos\.", "El Changelog ahora se publica aquí en la documentación en lugar de en lugares dispersos."),
            ],
        }
        for pattern, replacement in changelog_body_rules.get(locale, []):
            normalized = re.sub(pattern, replacement, normalized)

    if path.as_posix().endswith("introduction/support-policy.mdx"):
        locale_rules = {
            "de": [
                (r'"Support policy"', '"Support-Richtlinie"'),
                (r"eine E-Mail an Support an ", "eine E-Mail an Support unter "),
            ],
            "fr": [
                (r'"Support policy"', '"Politique de Support"'),
                (r"réponses officielles Support", "réponses officielles du Support"),
                (r"e-mail à Support à ", "e-mail au Support à "),
                (r"communauté Support", "Community Support"),
            ],
            "it": [
                (r'"Support policy"', '"Policy di Support"'),
                (r"risposte ufficiali Support", "risposte ufficiali del Support"),
                (r"community Support", "Community Support"),
                (r"l'ufficialità del Support", "il Support ufficiale"),
            ],
            "es": [
                (r'"Support policy"', '"Política de Support"'),
                (r"respuestas oficiales Support", "respuestas oficiales de Support"),
                (r"correo electrónico a Support a ", "correo electrónico al Support en "),
                (r"comunidad Support", "Community Support"),
            ],
            "jp": [
                (r'"Support policy"', '"Support ポリシー"'),
                (r"コミュニティ Support", "Community Support"),
                (r"フォームが利用できない場合は、Support ", "フォームが利用できない場合は、Support へ "),
            ],
        }
        for pattern, replacement in locale_rules.get(locale, []):
            normalized = re.sub(pattern, replacement, normalized)

    return normalized


def route_for_file(path: Path) -> str:
    return "/" + str(path.relative_to(ROOT)).replace(".mdx", "").replace("\\", "/")


def update_localized_anchors(path: Path, anchors_by_route: dict[str, dict[str, str]]) -> None:
    content = path.read_text(encoding="utf-8")
    current_route = route_for_file(path)

    def replace_markdown(match: re.Match[str]) -> str:
        bang, label, target = match.groups()
        if not target.startswith("/") and not target.startswith("#"):
            return match.group(0)
        if "#" not in target:
            return match.group(0)
        if target.startswith("#"):
            route = current_route
            anchor = target[1:]
            prefix = ""
        else:
            route, anchor = target.split("#", 1)
            prefix = route
        translated = anchors_by_route.get(route or current_route, {}).get(anchor)
        if not translated:
            return match.group(0)
        new_target = f"#{translated}" if not prefix else f"{prefix}#{translated}"
        return f"{bang}[{label}]({new_target})"

    def replace_href(match: re.Match[str]) -> str:
        quote = match.group("quote")
        target = match.group("target")
        if "#" not in target:
            return match.group(0)
        if target.startswith("#"):
            route = current_route
            anchor = target[1:]
            prefix = ""
        elif target.startswith("/"):
            route, anchor = target.split("#", 1)
            prefix = route
        else:
            return match.group(0)
        translated = anchors_by_route.get(route or current_route, {}).get(anchor)
        if not translated:
            return match.group(0)
        new_target = f"#{translated}" if not prefix else f"{prefix}#{translated}"
        return f'href={quote}{new_target}{quote}'

    content = re.sub(r'(!?)\[(.*?)\]\((.*?)\)', replace_markdown, content)
    content = re.sub(r'href=(?P<quote>["\'])(?P<target>(?:/[^"\']*|#[^"\']*))(?P=quote)', replace_href, content)
    path.write_text(content, encoding="utf-8")


def localize_docs_json() -> None:
    docs = json.loads(DOCS_JSON.read_text(encoding="utf-8"))
    navigation = docs["navigation"]["languages"]
    navbar_links = docs.get("navbar", {}).get("links", [])

    for language in navigation:
        locale = language["language"]
        if locale == "en":
            continue
        target = LOCALES[locale]
        strings = []
        for tab in language["tabs"]:
            strings.append(tab["tab"])
            for group in tab.get("groups", []):
                strings.append(group["group"])
        strings.extend(link["label"] for link in navbar_links)

        masked_map: dict[str, tuple[str, dict[str, str]]] = {}
        masked_strings = []
        for text in strings:
            masked, placeholders = mask_specials(text)
            masked_map[text] = (masked, placeholders)
            masked_strings.append(masked)

        translations = batch_translate(masked_strings, target)

        for tab in language["tabs"]:
            masked, placeholders = masked_map[tab["tab"]]
            tab["tab"] = unmask_specials(translations.get(masked, masked), placeholders)
            for group in tab.get("groups", []):
                masked, placeholders = masked_map[group["group"]]
                group["group"] = unmask_specials(translations.get(masked, masked), placeholders)
        if navbar_links:
            language["navbar"] = {"links": []}
            for link in navbar_links:
                masked, placeholders = masked_map[link["label"]]
                language["navbar"]["links"].append(
                    {
                        "label": unmask_specials(translations.get(masked, masked), placeholders),
                        "href": link["href"],
                    }
                )

        changelog_label = "変更ログ" if locale == "jp" else "Changelog"
        for tab in language["tabs"]:
            pages = list(tab.get("pages", []))
            for group in tab.get("groups", []):
                pages.extend(group.get("pages", []))
            if any(page.endswith("changelog") for page in pages):
                tab["tab"] = changelog_label

    DOCS_JSON.write_text(f"{json.dumps(docs, indent=2)}\n", encoding="utf-8")


def collect_locale_files(locale: str) -> list[Path]:
    base = ROOT / locale
    files = [base / file_name for file_name in CONTENT_FILES]
    for directory in CONTENT_DIRS:
        files.extend(sorted((base / directory).rglob("*.mdx")))
    return files


def translate_locale(locale: str, target: str) -> None:
    files = collect_locale_files(locale)
    anchors_by_route: dict[str, dict[str, str]] = {}

    for index, path in enumerate(files, start=1):
        print(f"[{locale}] translating {index}/{len(files)}: {path.relative_to(ROOT)}", flush=True)
        ops, texts = parse_file(path)
        masked_texts = []
        masked_map: dict[str, tuple[str, dict[str, str]]] = {}
        for text in texts:
            masked, placeholders = mask_specials(text)
            masked_map[text] = (masked, placeholders)
            masked_texts.append(masked)

        translations = batch_translate(masked_texts, target)
        file_translations: dict[str, str] = {}
        for original, (masked, placeholders) in masked_map.items():
            file_translations[masked] = translations.get(masked, masked)
            file_translations[original] = unmask_specials(translations.get(masked, masked), placeholders)

        anchors_by_route[route_for_file(path)] = build_anchor_map_from_ops(ops, file_translations)
        rendered = render_file(ops, file_translations)
        path.write_text(normalize_translated_content(locale, path, rendered), encoding="utf-8")
        print(f"[{locale}] translated {index}/{len(files)}: {path.relative_to(ROOT)}", flush=True)

    for path in files:
        update_localized_anchors(path, anchors_by_route)


def main() -> None:
    run(["node", "scripts/setup_languages.js"])
    localize_docs_json()
    for locale, target in LOCALES.items():
        translate_locale(locale, target)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
