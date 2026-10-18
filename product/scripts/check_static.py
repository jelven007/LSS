#!/usr/bin/env python3
"""Reject a frontend bundle whose entry page references missing local files."""
import argparse
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


class ResourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.resources.append(values["src"])
        if tag == "link" and values.get("href") and values.get("rel") in {
            "stylesheet", "icon", "modulepreload", "preload",
        }:
            self.resources.append(values["href"])


def check(root: Path):
    root = root.resolve()
    parser = ResourceParser()
    parser.feed((root / "index.html").read_text())
    for resource in parser.resources:
        url = urlsplit(resource)
        if url.scheme or url.netloc:
            continue
        path = (root / unquote(url.path).lstrip("/")).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"frontend resource missing or outside bundle: {resource}")
    return len(parser.resources)


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("root", type=Path)
    args = cli.parse_args()
    print(f"Frontend entry verified: {check(args.root)} resources")
