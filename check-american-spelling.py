#!/usr/bin/env python3
"""GATE: the lines a branch adds, and its commit messages, use American spelling.

WHAT IT CHECKS. With B = the merge-base of HEAD and --base (default
origin/main):

  * every line ADDED in `git diff B HEAD` (renames detected, so a moved file
    is not a rewritten one; binary and deleted files carry no added lines);
  * every line of every commit message in B..HEAD.

Existing text is not scanned: it is fixed when it is touched. `--all` scans
every tracked text file instead, REPORT-ONLY (always exit 0), for audits.
File names, PR titles and PR bodies are never seen.

DETECTION IS AN EXPLICIT WORD LIST, never a suffix rule. "-ise" rules flag
promise, exercise, otherwise, precise, enterprise, surprise, advise, revise,
comprise, expertise and many more; "-our" rules flag four, hour, your, tour;
"-re" rules flag more, there, where, were. The list lives in a data file,
words.txt beside this script (or --words <path>): one `british american` pair
per line, every inflection listed. A line is cut into letter runs (digits, `_`,
`-` and punctuation separate them), each run is cut again at camelCase
boundaries (bgColour -> bg Colour, HTMLColour -> HTML Colour), and each piece
is looked up whole, case-insensitively. So COLOURS, colour_for, bgColour and
my-colour are all found, and parameter never matches metre: only a whole piece
counts.

SUBSTRING STEMS, for compounds with no separator (colourpick, bgcolourx). A
short curated list of stems -- the `substring <stem> <american>` lines of the
word list, data like every other word -- is also matched INSIDE a piece that is
not itself a listed word. A stem qualifies only if it never occurs inside an
American word: colour, behaviour, favour and the like do; glamour (American
too) and every -ise, -re or -l stem do not. A stem that occurs inside an
American spelling in the list is a word-list error. A hit reads
`<piece> (contains <stem>) -> <piece with the American stem>`.

EXCEPTIONS, for verbatim quotes and fixed external identifiers (a library's
own British parameter name):

  * the marker `spelling: allow` anywhere on the line (any comment syntax, or
    prose) skips that line -- in a file or in a commit message;
  * allow files hold `<owner/repo> <path-glob> <word>` entries, each under a
    `#` comment giving its reason (one comment covers the entries after it, up
    to a blank line), for an identifier used on many lines. There are no
    per-repo allow files. Two central files are read:
      - allow.txt beside this script, the PUBLIC list: it names public
        repositories only, which check-allow-public.py enforces in CI;
      - a PRIVATE list kept outside this repository, for private
        repositories, read only when --private-allow <path> or the environment
        variable AMERICAN_SPELLING_PRIVATE_ALLOW names it. A named file that
        does not exist is exit 2.
    An entry applies only when its `owner/repo` is the repository being
    scanned, named by --repo-slug, or else derived from the scanned
    repository's `origin` remote URL; with neither, no entry applies and a note
    goes to stderr. Globs are repo-relative: `*` and `?` stay inside one
    directory, `**/` spans any number. An entry with no reason, a malformed
    line, or a word the gate can never flag (neither a listed word nor one
    containing a substring stem) is an error (exit 2), not a silent no-op. A
    substring hit is skipped by an entry naming its stem or the whole piece.
    Entries apply to files only, never to commit messages.
    (--allow-file <path> replaces allow.txt; it exists for the test suite.);
  * when the scanned repository is this gate's own (the script sits at its
    root), the script, words.txt, allow.txt and tests/run_tests.py are
    skipped -- they have to spell the British words to find them.

BASE. A base ref that does not resolve exits 2: comparing against nothing
would pass everything, and in CI that reads as green. A SHALLOW clone exits 2
too, checked before the merge-base, whether or not one is found: with no
merge-base it would compare against nothing, and with one whose parents are cut
off B..HEAD walks the whole history and fails on old commit messages.
`git fetch --depth=1` makes even a full clone shallow, so fetch the base
without --depth. A merge-base that cannot be found in a complete clone exits 2
as well.

Stdlib only, Python 3. ASCII only.

Run:  python check-american-spelling.py [--repo <path>] [--base <ref>]
                                        [--repo-slug <owner/repo>]
                                        [--private-allow <path>] [--words <path>]
      python check-american-spelling.py --all      (audit, report only)
Exit: 0 clean, 1 findings, 2 usage, word-list, allow-file, base, shallow or
      merge-base error.
"""
import argparse
import os
import re
import subprocess
import sys
from collections import Counter

__version__ = "1.0.3"

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_WORDS = os.path.join(HERE, "words.txt")
DEFAULT_ALLOW = os.path.join(HERE, "allow.txt")
PRIVATE_ALLOW_ENV = "AMERICAN_SPELLING_PRIVATE_ALLOW"
# Skipped only when the scanned repository is this gate's own: these files have
# to spell the British words to do their job.
SELF_SKIP = frozenset((
    "check-american-spelling.py",
    "words.txt",
    "allow.txt",
    "tests/run_tests.py",
))
MARKER = "spelling: allow"
BINARY_PROBE = 8000

LETTER_RUN = re.compile(r"[A-Za-z]+")
CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+")
WORD = re.compile(r"^[a-z]+$")
SLUG = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
# https://github.com/o/r(.git), git@github.com:o/r(.git), ssh://git@github.com/o/r(.git)
REMOTE_SLUG = re.compile(r"[:/]([A-Za-z0-9-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")


# ---- the word list --------------------------------------------------------------

class WordsError(Exception):
    pass


STEM_KEYWORD = "substring"


def parse_word_file(text):
    """({british: american}, {stem: american}), all lower case. The second holds the
    `substring <stem> <american>` lines. Raises WordsError on a bad line."""
    words, stems = {}, {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        table = words
        if parts[0] == STEM_KEYWORD:
            parts, table = parts[1:], stems
            if len(parts) != 2:
                raise WordsError("line %d: expected `%s <stem> <american>`, got %r"
                                 % (n, STEM_KEYWORD, raw))
        elif len(parts) != 2:
            raise WordsError("line %d: expected `<british> <american>`, got %r" % (n, raw))
        brit, amer = parts
        if not (WORD.match(brit) and WORD.match(amer)):
            raise WordsError("line %d: words must be lower case ASCII letters, got %r" % (n, raw))
        if brit == amer:
            raise WordsError("line %d: %r maps to itself" % (n, brit))
        if brit in table:
            raise WordsError("line %d: %r is listed twice" % (n, brit))
        table[brit] = amer
    if not words:
        raise WordsError("the word list is empty")
    # A stem inside an American spelling would flag the gate's own corrections.
    american = set(words.values()) | set(stems.values())
    for stem in sorted(stems):
        inside = sorted(a for a in american if stem in a)
        if inside:
            raise WordsError("substring stem %r occurs inside the American spelling %r"
                             % (stem, inside[0]))
    return words, stems


def parse_words(text):
    """{british: american}, both lower case. Raises WordsError on a bad line."""
    return parse_word_file(text)[0]


def load_word_file(path):
    with open(path, encoding="utf-8") as fh:
        return parse_word_file(fh.read())


def load_words(path):
    return load_word_file(path)[0]


# ---- scanning a line ------------------------------------------------------------

def pieces(text):
    """Yield every word piece of TEXT: letter runs cut at camelCase boundaries."""
    for run in LETTER_RUN.findall(text):
        for piece in CAMEL.findall(run):
            yield piece


def match_case(word, american):
    if word.isupper() and len(word) > 1:
        return american.upper()
    if word[:1].isupper():
        return american[:1].upper() + american[1:]
    return american


def replace_stem(piece, stem, american):
    """PIECE with every case-insensitive STEM replaced by AMERICAN, each in its case."""
    out, low, i = [], piece.lower(), 0
    while True:
        j = low.find(stem, i)
        if j < 0:
            return "".join(out) + piece[i:]
        out.append(piece[i:j] + match_case(piece[j:j + len(stem)], american))
        i = j + len(stem)


def stem_hits(piece, stems):
    """[(piece, american piece, stem)] for each substring stem inside PIECE."""
    low = piece.lower()
    found = [s for s in sorted(stems) if s in low]
    if not found:
        return []
    fixed = piece
    for s in found:
        fixed = replace_stem(fixed, s, stems[s])
    if low in stems:  # the piece IS a stem: report it as a plain word
        return [(piece, fixed, None)]
    return [(piece, fixed, s) for s in found]


def scan_line(text, words, stems=None):
    """[(word, american, stem)] for every British piece on one line; [] when marked.
    STEM is None for a whole-word hit, else the substring stem found inside WORD."""
    if MARKER in text.lower():
        return []
    hits = []
    for piece in pieces(text):
        amer = words.get(piece.lower())
        if amer is not None:
            hits.append((piece, match_case(piece, amer), None))
        elif stems:
            hits += stem_hits(piece, stems)
    return hits


def label(word, stem):
    """How a finding names its word: `word`, or `word (contains stem)`."""
    return word if stem is None else "%s (contains %s)" % (word, stem)


# ---- the allow file -------------------------------------------------------------

class AllowError(Exception):
    pass


def glob_to_regex(glob):
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def parse_allow(text, words, stems=None):
    """[(slug, glob regex, glob, word)], slug lower case. Every entry sits under a
    `#` reason comment; a blank line ends a reason. The word is a listed word or
    contains a substring stem: any other word could never be flagged."""
    stems = stems or {}
    entries, have_reason = [], False
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            have_reason = False
            continue
        if line.startswith("#"):
            have_reason = bool(line.lstrip("#").strip())
            continue
        parts = line.split()
        if len(parts) != 3:
            raise AllowError("line %d: expected `<owner/repo> <path-glob> <word>`, got %r" % (n, raw))
        slug, glob, word = parts[0], parts[1], parts[2].lower()
        if not SLUG.match(slug):
            raise AllowError("line %d: %r is not an `owner/repo` slug" % (n, slug))
        if not have_reason:
            raise AllowError("line %d: entry %r has no `#` reason comment above it" % (n, line))
        if word not in words and not any(s in word for s in stems):
            raise AllowError("line %d: %r is not in the gate's British word list and contains "
                             "no substring stem" % (n, word))
        entries.append((slug.lower(), glob_to_regex(glob), glob, word))
    return entries


def load_allow(path, words, stems=None):
    with open(path, encoding="utf-8") as fh:
        return parse_allow(fh.read(), words, stems)


def entries_for(entries, slug):
    """The (glob regex, word) pairs of the entries naming SLUG; none for no slug."""
    if not slug:
        return []
    s = slug.lower()
    return [(e[1], e[3]) for e in entries if e[0] == s]


def allowed(entries, path, word, stem=None):
    """True when an entry for PATH names WORD -- or, for a substring hit, its STEM."""
    keys = {word.lower(), stem} if stem else {word.lower()}
    return any(e[1] in keys and e[0].match(path) for e in entries)


def slug_from_url(url):
    """`owner/repo` from a remote URL, or None."""
    m = REMOTE_SLUG.search(url.strip())
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


# ---- git ------------------------------------------------------------------------

def git(repo, args):
    p = subprocess.run(["git", "-C", repo, "-c", "core.quotepath=false"] + args,
                       capture_output=True)
    return (p.returncode, p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace").strip())


HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _diff_path(header):
    """The path of a `+++ b/<path>` header line, or None for /dev/null."""
    name = header[4:].rstrip("\n")
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1].encode("latin-1", "replace").decode("unicode_escape", "replace")
    if name == "/dev/null":
        return None
    return name[2:] if name.startswith("b/") else name


def added_lines(diff_text):
    """Yield (path, line number, text) for every added line of a unified=0 diff."""
    path, lineno, in_hunk = None, 0, False
    for raw in diff_text.split("\n"):
        if raw.startswith("diff --git "):
            path, in_hunk = None, False
            continue
        if not in_hunk:
            if raw.startswith("+++ "):
                path = _diff_path(raw)
            m = HUNK.match(raw)
            if m:
                in_hunk, lineno = True, int(m.group(1))
            continue
        m = HUNK.match(raw)
        if m:
            lineno = int(m.group(1))
            continue
        if raw.startswith("+"):
            if path is not None:
                yield path, lineno, raw[1:].rstrip("\r")
            lineno += 1
        elif raw.startswith(" "):
            lineno += 1


def commit_messages(repo, rev_range):
    rc, out, err = git(repo, ["log", "--no-color", "--format=%H%x00%B%x1e", rev_range])
    if rc != 0:
        raise RuntimeError(err or "git log failed")
    for rec in out.split("\x1e"):
        rec = rec.lstrip("\n")
        if not rec:
            continue
        sha, _, body = rec.partition("\x00")
        yield sha.strip(), body


def _same_dir(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


# ---- the two modes --------------------------------------------------------------

def scan_branch(repo, base, words, allow, skip, stems=None):
    """(findings, merge-base) where findings is [(where, word, american)]; raises
    ValueError with the message for an exit-2 condition."""
    rc, _o, _e = git(repo, ["rev-parse", "--verify", "--quiet", base + "^{commit}"])
    if rc != 0:
        raise ValueError("the base ref %r does not resolve, so there is nothing to compare "
                         "against. Fetch it (git fetch origin main) or pass --base <ref>." % base)
    # Shallow is refused BEFORE the merge-base, not only when there is none: a merge-base can
    # exist with its parents cut off, and then B..HEAD walks the whole history down to the cut.
    rc, shallow, err = git(repo, ["rev-parse", "--is-shallow-repository"])
    if rc != 0:
        raise ValueError("git rev-parse --is-shallow-repository failed: %s" % err)
    if shallow.strip() == "true":
        raise ValueError("the clone is SHALLOW (a --depth fetch makes even a full clone shallow), "
                         "so the commits since the merge-base cannot be bounded. "
                         "git fetch --unshallow origin, then run again.")
    rc, mb, err = git(repo, ["merge-base", base, "HEAD"])
    mb = mb.strip()
    if rc != 0 or not mb:
        raise ValueError("no merge-base between %s and HEAD." % base)
    findings = []
    rc, diff, err = git(repo, ["diff", "--no-color", "--no-ext-diff", "--unified=0",
                               "--find-renames", "--src-prefix=a/", "--dst-prefix=b/", mb, "HEAD"])
    if rc != 0:
        raise ValueError("git diff failed: %s" % err)
    for path, lineno, text in added_lines(diff):
        if path in skip:
            continue
        for word, amer, stem in scan_line(text, words, stems):
            if not allowed(allow, path, word, stem):
                findings.append(("%s:%d" % (path, lineno), label(word, stem), amer))
    for sha, body in commit_messages(repo, "%s..HEAD" % mb):
        for n, text in enumerate(body.split("\n"), 1):
            for word, amer, stem in scan_line(text, words, stems):
                findings.append(("commit %s:%d" % (sha[:10], n), label(word, stem), amer))
    return findings, mb


def scan_all(repo, words, allow, skip, stems=None):
    rc, out, err = git(repo, ["ls-files", "-z"])
    if rc != 0:
        raise ValueError("git ls-files failed: %s" % err)
    findings = []
    for path in sorted(p for p in out.split("\x00") if p):
        if path in skip:
            continue
        full = os.path.join(repo, path)
        try:
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError:
            continue  # a tracked file deleted from the work tree
        if b"\x00" in data[:BINARY_PROBE]:
            continue
        for n, text in enumerate(data.decode("utf-8", "replace").split("\n"), 1):
            for word, amer, stem in scan_line(text, words, stems):
                if not allowed(allow, path, word, stem):
                    findings.append(("%s:%d" % (path, n), label(word, stem), amer))
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="check-american-spelling.py",
        description="Fail when the lines a branch adds, or its commit messages, use British "
                    "spelling.")
    ap.add_argument("--repo", default=".",
                    help="repository to check (default: the current directory)")
    ap.add_argument("--base", default="origin/main",
                    help="ref whose merge-base with HEAD bounds the check (default origin/main)")
    ap.add_argument("--repo-slug", default=None, metavar="OWNER/REPO",
                    help="the scanned repository's GitHub slug, which selects its entries in "
                         "allow.txt (default: derived from the `origin` remote URL)")
    ap.add_argument("--words", default=DEFAULT_WORDS,
                    help="the British -> American word list (default: words.txt beside this "
                         "script)")
    ap.add_argument("--private-allow", default=None, metavar="PATH",
                    help="a private allow file, same format as allow.txt, for private "
                         "repositories (default: $%s when set; otherwise none)"
                         % PRIVATE_ALLOW_ENV)
    ap.add_argument("--allow-file", default=DEFAULT_ALLOW,
                    help="TEST ONLY: replace the bundled allow.txt (default: allow.txt beside "
                         "this script)")
    ap.add_argument("--all", action="store_true",
                    help="scan every tracked text file; report only, always exit 0")
    ap.add_argument("--version", action="version", version="american-spelling " + __version__)
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0

    rc, top, err = git(os.path.abspath(args.repo), ["rev-parse", "--show-toplevel"])
    if rc != 0:
        print("american-spelling: BLOCKED: %s is not a git work tree (%s)"
              % (os.path.abspath(args.repo), err))
        return 2
    repo = os.path.normpath(top.strip())

    try:
        words, stems = load_word_file(args.words)
    except (WordsError, OSError) as exc:
        print("american-spelling: BLOCKED: word list %s: %s" % (args.words, exc))
        return 2

    if args.repo_slug is not None and not SLUG.match(args.repo_slug):
        print("american-spelling: BLOCKED: --repo-slug %r is not an `owner/repo` slug"
              % args.repo_slug)
        return 2
    allow_files = [args.allow_file]
    private = args.private_allow
    if private is None:
        private = os.environ.get(PRIVATE_ALLOW_ENV) or None
    if private is not None:
        allow_files.append(private)
    all_entries = []
    for path in allow_files:
        try:
            all_entries += load_allow(path, words, stems)
        except (AllowError, OSError) as exc:
            print("american-spelling: BLOCKED: allow file %s: %s" % (path, exc))
            return 2
    slug = args.repo_slug
    if slug is None:
        rc, url, _e = git(repo, ["remote", "get-url", "origin"])
        slug = slug_from_url(url) if rc == 0 else None
    if slug is None:
        sys.stderr.write("american-spelling: note: no --repo-slug and no `owner/repo` in the "
                         "origin remote URL, so no allow.txt entry applies.\n")
    allow = entries_for(all_entries, slug)
    skip = set(SELF_SKIP) if _same_dir(repo, HERE) else set()

    if args.all:
        try:
            findings = scan_all(repo, words, allow, skip, stems)
        except ValueError as exc:
            print("american-spelling: BLOCKED: %s" % exc)
            return 2
        for where, word, amer in findings:
            print("%s: %s -> %s" % (where, word, amer))
        counts = Counter(w.lower() for _w, w, _a in findings)
        files = len(set(where.rsplit(":", 1)[0] for where, _w, _a in findings))
        print("american-spelling --all: %d British word(s) in %d file(s) (report only)"
              % (len(findings), files))
        if counts:
            print("  top: " + ", ".join("%s %d" % (w, c) for w, c in counts.most_common(10)))
        return 0

    try:
        findings, mb = scan_branch(repo, args.base, words, allow, skip, stems)
    except (ValueError, RuntimeError) as exc:
        print("american-spelling: BLOCKED: %s" % exc)
        return 2
    print("american-spelling: lines added since %s (merge-base of %s and HEAD), plus their "
          "commit messages; %d British word(s) in the list, %d substring stem(s); "
          "%d allow entr(ies) for %s"
          % (mb[:10], args.base, len(words), len(stems), len(allow), slug or "(no slug)"))
    if not findings:
        return 0
    print("")
    print("FAIL: British spelling in what this branch adds:")
    for where, word, amer in findings:
        print("%s: %s -> %s" % (where, word, amer))
    print("")
    print("Use the American spelling. A verbatim quote or a fixed external identifier: put")
    print("`%s` on the line (the only exception a commit message can use). An" % MARKER)
    print("identifier used across files needs an `<owner/repo> <path-glob> <word>` entry: in")
    print("allow.txt of bilbospocketses/american-spelling for a public repository (a pull")
    print("request there and a new tag), or in the private allow file for a private one.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
