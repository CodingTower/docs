#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const root = process.cwd();
const docsPath = path.join(root, "docs.json");

const locales = [
  { code: "en", default: true },
  { code: "de" },
  { code: "fr" },
  { code: "it" },
  { code: "es" },
  { code: "jp" },
];

const contentDirs = ["introduction", "features", "integrations", "prompting", "tips-tricks"];
const contentFiles = ["AGENTS.mdx", "changelog.mdx", "glossary.mdx"];
const assetPrefixes = ["/images/", "/mintlify-assets/", "/favicon", "/logo/", "/assets/"];
const localePrefixPattern = new RegExp(`^/(${locales.map((locale) => locale.code).join("|")})(/|$)`);

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeJson(filePath, value) {
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

function prefixPage(page, locale) {
  return locale.default ? page : `${locale.code}/${page}`;
}

function prefixTabs(tabs, locale) {
  return tabs.map((tab) => {
    const next = { ...tab };
    if (tab.pages) {
      next.pages = tab.pages.map((page) => prefixPage(page, locale));
    }
    if (tab.groups) {
      next.groups = tab.groups.map((group) => ({
        ...group,
        pages: group.pages.map((page) => prefixPage(page, locale)),
      }));
    }
    return next;
  });
}

function prefixRedirectPath(route, locale) {
  if (!route.startsWith("/")) {
    return route;
  }
  return locale.default ? route : `/${locale.code}${route}`;
}

function buildLanguages(tabs) {
  return locales.map((locale) => {
    const entry = {
      language: locale.code,
      tabs: prefixTabs(tabs, locale),
    };
    if (locale.default) {
      entry.default = true;
    }
    return entry;
  });
}

function buildRedirects(redirects) {
  const seen = new Set();
  const next = [];

  for (const redirect of redirects) {
    const key = `${redirect.source}->${redirect.destination}`;
    if (!seen.has(key)) {
      seen.add(key);
      next.push(redirect);
    }
  }

  for (const locale of locales.filter((locale) => !locale.default)) {
    const homeRedirect = {
      source: `/${locale.code}`,
      destination: `/${locale.code}/introduction/welcome`,
    };
    const homeKey = `${homeRedirect.source}->${homeRedirect.destination}`;
    if (!seen.has(homeKey)) {
      seen.add(homeKey);
      next.push(homeRedirect);
    }

    for (const redirect of redirects) {
      const localized = {
        source: prefixRedirectPath(redirect.source, locale),
        destination: prefixRedirectPath(redirect.destination, locale),
      };
      const key = `${localized.source}->${localized.destination}`;
      if (!seen.has(key)) {
        seen.add(key);
        next.push(localized);
      }
    }
  }

  return next;
}

function getBaseTabs(docs) {
  if (docs.navigation.tabs) {
    return docs.navigation.tabs;
  }
  const defaultLanguage = (docs.navigation.languages || []).find((language) => language.default) || docs.navigation.languages?.[0];
  if (defaultLanguage?.tabs) {
    return defaultLanguage.tabs.map((tab) => {
      const next = { ...tab };
      if (tab.pages) {
        next.pages = tab.pages.map((page) => page.replace(/^en\//, ""));
      }
      if (tab.groups) {
        next.groups = tab.groups.map((group) => ({
          ...group,
          pages: group.pages.map((page) => page.replace(/^en\//, "")),
        }));
      }
      return next;
    });
  }
  throw new Error("Expected docs.json to contain navigation.tabs or navigation.languages");
}

function getBaseRedirects(redirects) {
  return (redirects || []).filter((redirect) => !localePrefixPattern.test(redirect.source));
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function copyEntry(source, destination) {
  fs.cpSync(source, destination, { recursive: true, force: true });
}

function shouldPrefixRoute(route) {
  return (
    route.startsWith("/") &&
    !assetPrefixes.some((prefix) => route.startsWith(prefix))
  );
}

function rewriteLocalizedLinks(content, localeCode) {
  const prefix = `/${localeCode}`;

  content = content.replace(/\]\((\/[^)\s]+)\)/g, (match, route) => {
    if (!shouldPrefixRoute(route)) {
      return match;
    }
    return `](${prefix}${route})`;
  });

  content = content.replace(/(href|to)="(\/[^"]+)"/g, (match, attr, route) => {
    if (!shouldPrefixRoute(route)) {
      return match;
    }
    return `${attr}="${prefix}${route}"`;
  });

  return content;
}

function localizeFile(filePath, localeCode) {
  const raw = fs.readFileSync(filePath, "utf8");
  const localized = rewriteLocalizedLinks(raw, localeCode);
  fs.writeFileSync(filePath, localized);
}

function localizeTree(localeCode) {
  const localeRoot = path.join(root, localeCode);
  fs.rmSync(localeRoot, { recursive: true, force: true });
  ensureDir(localeRoot);

  for (const file of contentFiles) {
    copyEntry(path.join(root, file), path.join(localeRoot, file));
  }

  for (const dir of contentDirs) {
    copyEntry(path.join(root, dir), path.join(localeRoot, dir));
  }

  const mdxFiles = [];
  const stack = [localeRoot];
  while (stack.length) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const fullPath = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(fullPath);
      } else if (entry.isFile() && fullPath.endsWith(".mdx")) {
        mdxFiles.push(fullPath);
      }
    }
  }

  for (const filePath of mdxFiles) {
    localizeFile(filePath, localeCode);
  }
}

function main() {
  const docs = readJson(docsPath);
  const baseTabs = getBaseTabs(docs);

  docs.navigation = {
    ...docs.navigation,
    languages: buildLanguages(baseTabs),
  };
  delete docs.navigation.tabs;

  docs.redirects = buildRedirects(getBaseRedirects(docs.redirects || []));
  writeJson(docsPath, docs);

  for (const locale of locales.filter((locale) => !locale.default)) {
    localizeTree(locale.code);
  }
}

main();
