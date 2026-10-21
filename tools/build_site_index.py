#!/usr/bin/env python3
"""Build the 1Z99 site search index using only the standard library."""

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote


DOCS = Path(__file__).resolve().parents[1] / "docs"
VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())
IGNORED_TAGS = frozenset(("script", "style", "nav", "button", "svg"))


class Element:
    def __init__(self, tag, attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.walk()

    def text(self):
        if self.tag in IGNORED_TAGS or "shot-placeholder" in self.attrs.get("class", "").split():
            return ""
        return " ".join(child.text() if isinstance(child, Element) else child
                        for child in self.children)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element("document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        element = Element(tag, attrs)
        self.stack[-1].children.append(element)
        if tag not in VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def build_index(docs_dir=DOCS):
    """Return entries in filename and document order; never read optional files."""
    entries = []
    docs_dir = Path(docs_dir)
    pages = sorted(docs_dir.rglob("*.html"))
    if not pages:
        raise ValueError("No documentation HTML pages found")
    for path in pages:
        parser = PageParser()
        parser.feed(path.read_text(encoding="utf-8"))
        parser.close()
        elements = list(parser.root.walk())
        title = next((node.text() for node in elements if node.tag == "title"), "")
        title_parts = [" ".join(part.split()) for part in title.split("|")]
        page = " / ".join(reversed(title_parts[:-1])) if len(title_parts) > 2 else title_parts[0]
        if not page:
            raise ValueError("{}: missing page title".format(path.name))
        seen = set()
        for section in elements:
            if section.tag != "section" or "data-search-section" not in section.attrs:
                continue
            anchor = section.attrs.get("id", "")
            if not anchor or anchor in seen:
                raise ValueError("{}: missing or duplicate search section ID {!r}".format(path.name, anchor))
            seen.add(anchor)
            heading = next((node for node in section.walk() if node.tag in ("h2", "h1")), None)
            title = heading.text() if heading else re.sub(r"[-_]+", " ", anchor).title()
            entries.append({
                "page": page,
                "title": " ".join(title.split()),
                "url": "{}#{}".format(quote(path.relative_to(docs_dir).as_posix(), safe="/"), quote(anchor, safe="")),
                "text": " ".join(section.text().split()),
            })
    return entries


def render_index(entries):
    """Canonical ASCII JSON, including a final newline."""
    return json.dumps(entries, ensure_ascii=True, indent=2) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the index is missing or stale; do not write")
    args = parser.parse_args(argv)
    output = DOCS / "search-index.json"
    try:
        expected = render_index(build_index()).encode("ascii")
        if args.check:
            if not output.is_file() or output.read_bytes() != expected:
                print("Search index is missing or stale. Run python3 tools/build_site_index.py", file=sys.stderr)
                return 1
            print("Search index is up to date.")
        else:
            output.write_bytes(expected)
            print("Wrote {}".format(output))
    except (OSError, ValueError) as error:
        print("Cannot build search index: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
