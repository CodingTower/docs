# ZenOpus Documentation

This repository contains the Mintlify-based documentation site for ZenOpus.

## Development

Install the Mintlify CLI:

```bash
npm i -g mint
```

Start the local preview from the project root:

```bash
mint dev
```

The local site runs at `http://localhost:3000`.

## Validation

Check the site before publishing:

```bash
mint validate
mint broken-links
```

## Structure

- `docs.json`: Mintlify site configuration
- `introduction/`, `features/`, `integrations/`, `prompting/`, `tips-tricks/`: documentation pages
- `changelog.mdx`: product changelog
- `images/zenopus/`: migrated documentation assets

## Migration tooling

The ZenOpus source import script lives in:

```bash
scripts/import_zenopus_docs.py
```

It is used to inventory and re-import the source documentation into this Mintlify project.
