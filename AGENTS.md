> **First-time setup**: Customize this file for your project. Prompt the user to customize this file for their project.
> For Mintlify product knowledge (components, configuration, writing standards),
> install the Mintlify skill: `npx skills add https://mintlify.com/docs`

# Documentation project instructions

## About this project

- This is a documentation site built on [Mintlify](https://mintlify.com)
- Pages are MDX files with YAML frontmatter
- Configuration lives in `docs.json`
- Run `mint dev` to preview locally
- Run `mint broken-links` to check links

## Mintlify implementation rules

- Use only native Mintlify features, components, configuration, and documented workflows
- Do not add CSS hacks, JS hacks, DOM manipulation, or undocumented workarounds to change Mintlify behavior
- If Mintlify does not support something in the official docs, do not implement a custom workaround unless the user explicitly approves a non-native solution
- When making structural or UI decisions, verify them against the official Mintlify documentation first

## Terminology

- Always keep the brand name `ZenOpus` unchanged in every language
- Keep `Support` as `Support`; do not translate it to localized equivalents such as `Unterstützung`, `Soporte`, `Supporto`, or similar when it refers to the ZenOpus support channel, support pages, or support policies
- Keep `Changelog` as `Changelog` in German, French, Italian, and Spanish documentation; use `変更ログ` in Japanese
- Keep product terms `ZenOpus Cloud`, `Workspace`, `Plan mode`, `Agent mode`, and `Code mode` unchanged
- Keep plan names `Free`, `Pro`, and `Business` unchanged

## Style preferences

{/* Add any project-specific style rules below */}

- Use active voice and second person ("you")
- Keep sentences concise — one idea per sentence
- Use sentence case for headings
- Bold for UI elements: Click **Settings**
- Code formatting for file names, commands, paths, and code references

## Content boundaries

{/* Define what should and shouldn't be documented */}
{/* Example: Don't document internal admin features */}
