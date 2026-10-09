#!/usr/bin/env python3
"""GATE: every repository named in allow.txt is PUBLIC.

allow.txt ships in a public repository, so a private repository's name must
never appear in it. For every distinct `owner/repo` in the file this asks the
GitHub API, UNAUTHENTICATED, for https://api.github.com/repos/<owner/repo> and
fails unless the answer is HTTP 200 with `"private": false`. A 404 fails too:
to an anonymous caller a private repository and a missing one look the same,
and neither belongs in the file.

The file is parsed with the gate's own parser against words.txt, so a
malformed allow.txt fails here as well.

Stdlib only, Python 3. ASCII only.

Run:  python check-allow-public.py [--allow-file <path>] [--words <path>]
Exit: 0 every repository is public, 1 any repository is private, missing or
      not answered as public, 2 usage, allow-file or network error (including
      the anonymous API rate limit).
"""
import argparse
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://api.github.com/repos/"


def _gate():
    spec = importlib.util.spec_from_file_location(
        "_american_spelling_gate", os.path.join(HERE, "check-american-spelling.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch(slug):
    """(HTTP status, parsed JSON body or None) for the repository SLUG, unauthenticated.
    Raises OSError on a network failure."""
    req = urllib.request.Request(API + slug, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "american-spelling-allow-check"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, None


def verdict(slug, fetcher=fetch):
    """'public', 'not-public' (private or missing) or 'error: <why>' for SLUG."""
    try:
        status, body = fetcher(slug)
    except (OSError, ValueError) as exc:
        return "error: %s" % exc
    if status == 200 and isinstance(body, dict) and body.get("private") is False:
        return "public"
    if status == 200 and isinstance(body, dict) and body.get("private") is True:
        return "not-public"
    if status == 404:
        return "not-public"
    return "error: HTTP %s" % status


def slugs_in(allow_path, words_path):
    gate = _gate()
    words, stems = gate.load_word_file(words_path)
    entries = gate.load_allow(allow_path, words, stems)
    return sorted(set(e[0] for e in entries))


def main(argv=None, fetcher=fetch):
    ap = argparse.ArgumentParser(prog="check-allow-public.py",
                                 description="Fail unless every repository named in allow.txt "
                                             "is public.")
    ap.add_argument("--allow-file", default=os.path.join(HERE, "allow.txt"))
    ap.add_argument("--words", default=os.path.join(HERE, "words.txt"))
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        slugs = slugs_in(args.allow_file, args.words)
    except Exception as exc:  # the gate's AllowError/WordsError, or OSError
        print("allow-public: BLOCKED: %s: %s" % (args.allow_file, exc))
        return 2
    worst = 0
    for slug in slugs:
        v = verdict(slug, fetcher)
        print("%s: %s" % (slug, v))
        if v == "not-public":
            worst = max(worst, 1)
        elif v != "public":
            worst = 2
    if worst == 1:
        print("FAIL: allow.txt names a repository that is not public. Private repositories "
              "belong in the private allow file, never here.")
    elif worst == 2:
        print("BLOCKED: could not confirm every repository is public.")
    else:
        print("allow-public: %d repository(ies), all public" % len(slugs))
    return worst


if __name__ == "__main__":
    sys.exit(main())
