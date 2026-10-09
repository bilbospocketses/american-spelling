#!/usr/bin/env python3
"""Tests for check-american-spelling.py, the American spelling gate.

WHAT THIS PINS, and the direction in which each defect would read as a pass:

  * an ADDED line with a British word fails, named as path:line: word -> american,
    and the line number is the line in the new file;
  * text that was already there is NOT scanned: an untouched British line in a
    file the branch edits, a line the branch REMOVES, a file the branch only
    RENAMES, and a line main deleted after the branch point (a diff against
    main's tip instead of the merge-base would blame the branch for it);
  * identifiers are cut into words: COLOURS, colour_for, bgColour, my-colour,
    HTMLColour and a word between digits are all found;
  * the words a suffix rule would flag are not: promise, exercise, otherwise,
    precise, enterprise, surprise, advise, revise, comprise, expertise, rise,
    wise, licensed, analyses (the American plural of analysis), cancellation,
    parameter, programmer, greyhound, four, hour, more;
  * the marker `spelling: allow` skips its line, in a file and in a commit
    message; an allow entry (public allow.txt, or the private file named by
    --private-allow or $AMERICAN_SPELLING_PRIVATE_ALLOW) skips a word only for
    the repository its slug names (--repo-slug, else the origin remote URL),
    only on the paths its glob matches, never in a commit message; with no
    slug no entry applies and stderr says so; an entry with no reason, a
    malformed line, or a word not in the list, and a named private file that
    does not exist, are exit 2 rather than a silent no-op;
  * check-allow-public.py passes only a 200 with "private": false, and fails
    a private repo, a 404, a rate limit or a network error (the API is mocked:
    the suite stays offline);
  * the word list is the data file (words.txt, or --words): a word it lists is
    found, a word it does not list is not, and a malformed, duplicated,
    self-mapping, empty or missing list is exit 2;
  * the gate's own files are skipped only in the gate's own repository; a
    consumer's files that happen to share their names are scanned;
  * a commit message with a British word fails; the base and main commits carry
    British messages, so a gate reading messages over all history (not
    merge-base..HEAD) fails every clean scenario; a branch that MERGES main is
    flagged for its own lines and messages and never for main's;
  * a base ref that does not resolve is exit 2, and so is a SHALLOW clone --
    with no merge-base (it would compare against nothing) and with one whose
    parents are cut off (it would read history down to the cut);
  * --all scans every tracked text file, skips binary files, and always exits 0;
  * MUTANTS: the gate is loaded by path and broken on purpose -- no camelCase
    cut, `_` or `-` kept inside a word, substring matching instead of whole
    words, context and removed lines counted as added, the diff taken from
    main's tip, commit messages read over all history -- and each mutant must
    turn at least one of these cases red.
    A green suite is then evidence that the cases are sensitive to the gate,
    not merely compatible with it.

Every fixture is a real git repository under this run's temp root, removed on
exit; the base is a real ref (refs/remotes/origin/main), because the gate reads
it with git.

Stdlib only, Python 3. ASCII only.

Run:  python tests/run_tests.py
Exit: 0 all passed, 1 any failure.
"""
import atexit
import contextlib
import importlib.util
import io
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
GATE = os.path.join(REPO, "check-american-spelling.py")
WORDS = os.path.join(REPO, "words.txt")

# One temp root for the whole run, removed on every normal exit path. Set before
# anything below can create a temp path, so every mkdtemp lands inside it.
TMP_ROOT = tempfile.mkdtemp(prefix="american-spelling-tests-")
tempfile.tempdir = TMP_ROOT


def _make_writable_and_retry(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)  # git writes read-only object files; Windows refuses to delete them
    func(path)


def _cleanup():
    tempfile.tempdir = None
    if sys.version_info >= (3, 12):
        shutil.rmtree(TMP_ROOT, onexc=_make_writable_and_retry)
    else:
        shutil.rmtree(TMP_ROOT, onerror=_make_writable_and_retry)


atexit.register(_cleanup)

# A private allow file named by the environment of the machine running the suite must not
# leak into any case; the cases that test the variable set it explicitly.
PRIVATE_ENV = "AMERICAN_SPELLING_PRIVATE_ALLOW"
os.environ.pop(PRIVATE_ENV, None)

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))


def load(modname, path=GATE):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo, *args):
    p = subprocess.run(["git", "-C", repo] + list(args), capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), p.stderr.decode("utf-8", "replace")))
    return p.stdout.decode("utf-8", "replace")


def in_tmp_root(path):
    """PATH relative to this run's temp root. Every fixture write goes through it, so a
    test can never write outside TMP_ROOT -- it raises instead."""
    rel = os.path.relpath(os.path.abspath(path), TMP_ROOT)
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        raise RuntimeError("refusing to write outside the test temp root: %r" % path)
    return rel


def write(repo, rel, text, mode="w"):
    """Write TEXT (str or bytes) to REPO/REL, which must lie inside the temp root."""
    inside = in_tmp_root(os.path.join(repo, rel))
    assert tempfile.gettempdir() == TMP_ROOT, "tempfile.tempdir no longer points at the run's root"
    os.makedirs(os.path.join(tempfile.gettempdir(), os.path.dirname(inside)), exist_ok=True)
    if isinstance(text, bytes):
        with open(os.path.join(tempfile.gettempdir(), inside), "wb") as fh:
            fh.write(text)
    else:
        with open(os.path.join(tempfile.gettempdir(), inside), mode, encoding="utf-8",
                  newline="\n") as fh:
            fh.write(text)


BASE_MESSAGE = "base: organise the neighbour list"
MAIN_MESSAGE = "main: normalise the centre handling"
OLD_WORDS = ("organise", "neighbour", "normalise", "centre", "favourite", "colour")


def new_repo(base_files):
    """A repo whose first commit is the base, marked as refs/remotes/origin/main,
    with branch `feat` checked out on it. Returns the repo path."""
    repo = tempfile.mkdtemp(prefix="spell-")
    subprocess.run(["git", "-C", repo, "init", "-q", "-b", "main"], check=True, capture_output=True)
    for key, val in (("user.email", "t@example.invalid"), ("user.name", "t"),
                     ("commit.gpgsign", "false"), ("tag.gpgsign", "false"),
                     ("core.hooksPath", os.devnull), ("core.autocrlf", "false")):
        git(repo, "config", key, val)
    for rel, text in base_files.items():
        write(repo, rel, text)
    git(repo, "add", ".")
    # A BRITISH message on purpose: the base is not the branch's, so a gate that read commit
    # messages over all history (not merge-base..HEAD) turns every exit-0 scenario red.
    git(repo, "commit", "-q", "--allow-empty", "-m", BASE_MESSAGE)
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "-b", "feat")
    return repo


def commit(repo, msg, files=None, remove=(), rename=()):
    for rel, text in (files or {}).items():
        write(repo, rel, text)
    for rel in remove:
        git(repo, "rm", "-q", rel)
    for old, new in rename:
        os.makedirs(os.path.dirname(os.path.join(repo, new)), exist_ok=True)
        git(repo, "mv", old, new)
    git(repo, "add", ".")
    git(repo, "commit", "-q", "--allow-empty", "-m", msg)


def run_cli(repo, *extra, gate=GATE, cwd=None, env=None):
    args = [sys.executable, gate] + (["--repo", repo] if repo is not None else []) + list(extra)
    p = subprocess.run(args, capture_output=True, cwd=cwd, env=env)
    return p.returncode, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")


def run_mod(mod, repo, *extra):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = mod.main(["--repo", repo] + list(extra))
    return code, out.getvalue()


def findings(out):
    """The `where: word -> american` lines of a gate run."""
    return [ln.strip() for ln in out.splitlines() if " -> " in ln and not ln.startswith("  top:")]


# ---- the scenarios, each a function of a gate runner --------------------------
# Each returns [(label, ok, detail)], so the same scenario runs against the real
# gate (as a CLI) and against every mutant (in process). A scenario's repository
# is built ONCE (cached) and only read by every run after it: the gate never
# writes to the repository it checks, and git setup is the slow part.

_REPOS = {}


def cached(build):
    if build not in _REPOS:
        _REPOS[build] = build()
    return _REPOS[build]


def _build_added():
    repo = new_repo({"a.txt": "nothing here\n"})
    commit(repo, "add text", {"a.txt": "nothing here\nthe colour of the sky\n",
                              "docs-x/new.md": "# Title\n\nOur favourite behaviour.\n"})
    return repo


def sc_added_flagged(run):
    code, out = run(cached(_build_added))
    got = findings(out)
    return [("added British words fail (exit 1)", code == 1, out),
            ("...named path:line: word -> american, the line in the NEW file",
             "a.txt:2: colour -> color" in got and "docs-x/new.md:3: favourite -> favorite" in got
             and "docs-x/new.md:3: behaviour -> behavior" in got, got)]


def _build_untouched():
    repo = new_repo({"old.txt": "line one\nthe colour stays\nline three\n"})
    commit(repo, "edit around the old text",
           {"old.txt": "line one\nthe colour stays\nline three\nline four is new\n"})
    return repo


def sc_untouched_not_flagged(run):
    code, out = run(cached(_build_untouched))
    return [("an untouched British line in an edited file is not scanned (exit 0)",
             code == 0 and not findings(out), out)]


def _build_removed():
    repo = new_repo({"gone.txt": "keep\nthe behaviour goes\n"})
    commit(repo, "drop a line", {"gone.txt": "keep\n"})
    return repo


def sc_removed_not_flagged(run):
    code, out = run(cached(_build_removed))
    return [("a British line the branch REMOVES is not scanned (exit 0)",
             code == 0 and not findings(out), out)]


def _build_renamed():
    repo = new_repo({"move.txt": "the honour of moving\nand more lines\nso the rename\nis detected\n"})
    commit(repo, "move a file", rename=(("move.txt", "moved/move.txt"),))
    return repo


def sc_renamed_not_flagged(run):
    code, out = run(cached(_build_renamed))
    return [("a file the branch only RENAMES is not scanned (exit 0)",
             code == 0 and not findings(out), out)]


def _build_main_moved():
    # main deletes a British line AFTER the branch point; the branch still has it.
    # Diffed from main's tip, that line reads as ADDED by the branch.
    repo = new_repo({"shared.txt": "alpha\nthe neighbour line\nomega\n"})
    commit(repo, "branch work", {"branch.txt": "plain\n"})
    git(repo, "checkout", "-q", "main")
    commit(repo, MAIN_MESSAGE, {"shared.txt": "alpha\nomega\n"})
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "feat")
    return repo


def sc_main_moved(run):
    code, out = run(cached(_build_main_moved))
    return [("a line main deleted after the branch point is not blamed on the branch (exit 0)",
             code == 0 and not findings(out), out)]


def _build_merges_main():
    # The branch works, main moves on (British lines AND a British message), the branch
    # merges main, then works again. The merge-base is now main's tip: main's lines and
    # messages are behind it; the branch's, before and after the merge, are not.
    repo = new_repo({"shared.txt": "alpha\n"})
    commit(repo, "feat: first branch work", {"b1.txt": "plain\n"})
    git(repo, "checkout", "-q", "main")
    commit(repo, MAIN_MESSAGE, {"m.txt": "the favourite colour\n"})
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "feat")
    git(repo, "merge", "-q", "--no-ff", "--no-edit", "main")
    commit(repo, "feat: tidy the behaviour", {"b2.txt": "the grey cat\n"})
    return repo


def sc_merges_main(run):
    code, out = run(cached(_build_merges_main))
    got = findings(out)
    return [("branch merges main: the branch's British words fail (exit 1)", code == 1, out),
            ("branch merges main: the branch's added line is flagged", "b2.txt:1: grey -> gray" in got, got),
            ("branch merges main: the branch's commit message is flagged",
             any(re.match(r"commit [0-9a-f]{10}:1: behaviour -> behavior", g) for g in got), got),
            ("branch merges main: main's added lines are not flagged",
             not any(g.startswith("m.txt:") for g in got), got),
            ("branch merges main: main's and the base's commit messages are not flagged",
             not any(w in g for g in got for w in OLD_WORDS), got),
            ("branch merges main: exactly those two findings", len(got) == 2, got)]


IDENT_LINES = ("COLOURS = 1\n", "def colour_for(x): pass\n", "bgColour = 2\n",
               ".my-colour { }\n", "HTMLColour = 3\n", "x1colour2 = 4\n")


def _build_identifiers():
    repo = new_repo({"z.txt": "z\n"})
    commit(repo, "identifiers", {"ids.py": "".join(IDENT_LINES)})
    return repo


def sc_identifiers(run):
    code, out = run(cached(_build_identifiers))
    got = findings(out)
    want = ["ids.py:1: COLOURS -> COLORS", "ids.py:2: colour -> color", "ids.py:3: Colour -> Color",
            "ids.py:4: colour -> color", "ids.py:5: Colour -> Color", "ids.py:6: colour -> color"]
    rows = [("identifiers: all six shapes fail (exit 1)", code == 1, out)]
    for w in want:
        rows.append(("identifiers: %s" % w, w in got, got))
    return rows


FALSE_POSITIVES = ("promise exercise otherwise precise enterprise surprise advise revise comprise "
                   "expertise rise wise licensed analyses cancellation parameter programmer greyhound "
                   "four hour more there where were tour your colorful behavior gray center "
                   "Promise_Exercise otherwiseMode")


def _build_false_positives():
    repo = new_repo({"z.txt": "z\n"})
    commit(repo, "plain American text", {"fp.txt": FALSE_POSITIVES + "\n"})
    return repo


def sc_false_positives(run):
    code, out = run(cached(_build_false_positives))
    return [("words a suffix or substring rule would flag are not flagged (exit 0)",
             code == 0 and not findings(out), out)]


def _build_marker():
    repo = new_repo({"z.txt": "z\n"})
    commit(repo, "quoted", {"q.py": "x = 'colour'  # spelling: allow\n",
                            "q.md": "He wrote \"colour\". <!-- spelling: allow -->\n"
                                    "Spelling: Allow the verbatim honour quote\n"})
    return repo


def sc_marker(run):
    code, out = run(cached(_build_marker))
    return [("the inline marker skips its line, any syntax, any case (exit 0)",
             code == 0 and not findings(out), out)]


def _build_commit_message():
    repo = new_repo({"z.txt": "z\n"})
    commit(repo, "fix: normalise the colour handling\n\nBody line with whilst.\n", {"c.txt": "c\n"})
    commit(repo, "docs: quote the old name centre (spelling: allow)", {"d.txt": "d\n"})
    return repo


def sc_commit_message(run):
    code, out = run(cached(_build_commit_message))
    got = findings(out)
    return [("a commit message with British words fails (exit 1)", code == 1, out),
            ("...each named commit <sha>:<line>",
             any(re.match(r"commit [0-9a-f]{10}:1: normalise -> normalize", g) for g in got)
             and any(re.match(r"commit [0-9a-f]{10}:3: whilst -> while", g) for g in got), got),
            ("...and the marker skips a message line", not any("centre" in g for g in got), got)]


SCENARIOS = (sc_added_flagged, sc_untouched_not_flagged, sc_removed_not_flagged,
             sc_renamed_not_flagged, sc_main_moved, sc_merges_main, sc_identifiers, sc_false_positives,
             sc_marker, sc_commit_message)


def run_scenarios(run, prefix):
    rows = []
    for sc in SCENARIOS:
        try:
            rows += sc(run)
        except Exception as exc:  # a crashing scenario is a failed row, never a pass
            rows.append(("%s raised %s" % (sc.__name__, type(exc).__name__), False, repr(exc)))
    return [(prefix + label, ok, detail) for label, ok, detail in rows]


def test_real_gate():
    for label, ok, detail in run_scenarios(run_cli, ""):
        check(label, ok, detail)


# ---- the cases that are not scenarios -----------------------------------------

def run_cli_split(repo, *extra, gate=GATE):
    """(exit code, stdout, stderr) of a gate run."""
    args = [sys.executable, gate] + (["--repo", repo] if repo is not None else []) + list(extra)
    p = subprocess.run(args, capture_output=True)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def allow_file(text):
    """A test-only allow file holding TEXT, outside every fixture repository."""
    folder = tempfile.mkdtemp(prefix="allow-")
    write(folder, "allow.txt", text)
    return os.path.join(folder, "allow.txt")


ACME_ALLOW = ("# the lib's own draw() keyword\n"
              "acme/widgets lib/** colour\n"
              "\n"
              "# another repository's identifier: never applies to acme/widgets\n"
              "other/repo other/** colour\n")


def _build_draw():
    repo = new_repo({"z.txt": "z\n"})
    commit(repo, "external identifier", {
        "lib/draw.py": "draw(colour=1)\ndraw(colour=2)\n",
        "other/draw.py": "draw(colour=3)\n"})
    return repo


def test_allow_slug():
    repo = cached(_build_draw)
    allow = allow_file(ACME_ALLOW)
    code, out = run_cli(repo, "--allow-file", allow, "--repo-slug", "acme/widgets")
    check("allow: the scanned repo's entry skips its glob's paths, another path is not skipped, and "
          "another repo's entry for that path is ignored (exit 1, one finding)",
          code == 1 and findings(out) == ["other/draw.py:1: colour -> color"], out)
    code, out = run_cli(repo, "--allow-file", allow, "--repo-slug", "ACME/Widgets")
    check("allow: slugs match case-insensitively",
          code == 1 and findings(out) == ["other/draw.py:1: colour -> color"], out)
    code, out = run_cli(repo, "--allow-file", allow, "--repo-slug", "other/repo")
    check("allow: under another slug, only that slug's entries apply",
          code == 1 and findings(out) == ["lib/draw.py:1: colour -> color",
                                          "lib/draw.py:2: colour -> color"], out)
    code, out = run_cli(repo, "--allow-file", allow, "--repo-slug", "nobody/else")
    check("allow: a slug with no entries allows nothing (three findings)",
          code == 1 and len(findings(out)) == 3, out)
    code, out = run_cli(repo, "--repo-slug", "not-a-slug")
    check("--repo-slug that is not owner/repo is exit 2", code == 2 and "not-a-slug" in out, out)


def test_allow_slug_from_remote():
    allow = allow_file(ACME_ALLOW)
    for url in ("https://github.com/acme/widgets.git", "https://github.com/acme/widgets",
                "git@github.com:acme/widgets.git", "ssh://git@github.com/acme/widgets.git"):
        repo = _build_draw()
        git(repo, "remote", "add", "origin", url)
        code, out, err = run_cli_split(repo, "--allow-file", allow)
        check("slug derived from the origin remote %s" % url,
              code == 1 and findings(out) == ["other/draw.py:1: colour -> color"]
              and "acme/widgets" in out and "note" not in err, out + err)
    repo = _build_draw()
    git(repo, "remote", "add", "origin", "https://github.com/acme/widgets.git")
    code, out = run_cli(repo, "--allow-file", allow, "--repo-slug", "other/repo")
    check("--repo-slug overrides the slug in the origin remote",
          code == 1 and findings(out) == ["lib/draw.py:1: colour -> color",
                                          "lib/draw.py:2: colour -> color"], out)
    repo = cached(_build_draw)  # no origin remote at all
    code, out, err = run_cli_split(repo, "--allow-file", allow)
    check("no --repo-slug and no origin remote: no entry applies, and stderr says so",
          code == 1 and len(findings(out)) == 3 and "no allow.txt entry applies" in err, out + err)
    repo = _build_draw()
    git(repo, "remote", "add", "origin", "not a url")
    code, out, err = run_cli_split(repo, "--allow-file", allow)
    check("an origin remote with no owner/repo in it: no entry applies, and stderr says so",
          code == 1 and len(findings(out)) == 3 and "no allow.txt entry applies" in err, out + err)


def test_allow_file_errors():
    repo = cached(_build_draw)
    for label, text, needle in (
            ("an entry with no `#` reason above it", "acme/widgets lib/** colour\n", "reason"),
            ("a blank line between the reason and the entry", "# why\n\nacme/widgets lib/** colour\n",
             "reason"),
            ("a word the list does not know", "# why\nacme/widgets lib/** promise\n", "promise"),
            ("an entry with no slug", "# why\nlib/** colour\n", "owner/repo"),
            ("an entry with four fields", "# why\nacme/widgets lib/** colour extra\n", "owner/repo"),
            ("a slug that is not owner/repo", "# why\nacme lib/** colour\n", "slug")):
        code, out = run_cli(repo, "--allow-file", allow_file(text), "--repo-slug", "acme/widgets")
        check("allow file: %s is exit 2" % label, code == 2 and needle in out, out)
    code, out = run_cli(repo, "--allow-file", os.path.join(TMP_ROOT, "no-such-allow.txt"))
    check("allow file: a missing file is exit 2, named", code == 2 and "no-such-allow" in out, out)
    # An entry for ANOTHER repository is still validated: a bad line fails every consumer.
    code, out = run_cli(repo, "--allow-file", allow_file("# why\nother/repo ** promise\n"),
                        "--repo-slug", "acme/widgets")
    check("allow file: a bad entry for another repository is still exit 2", code == 2, out)


def test_allow_never_in_commit_messages():
    repo = new_repo({"z.txt": "z\n"})
    commit(repo, "config: keep the colour keyword", {"a.txt": "colour\n"})
    code, out = run_cli(repo, "--allow-file", allow_file("# reason\nacme/widgets ** colour\n"),
                        "--repo-slug", "acme/widgets")
    got = findings(out)
    check("an allow entry skips files but never a commit message",
          code == 1 and len(got) == 1 and re.match(r"commit [0-9a-f]{10}:1: colour -> color", got[0]),
          got)


def test_bundled_allow():
    gate = load("_spell_bundled")
    words = gate.load_words(WORDS)
    entries = gate.load_allow(os.path.join(REPO, "allow.txt"), words)
    ws = set((e[2], e[3]) for e in entries if e[0] == "bilbospocketses/ws-scrcpy-web")
    check("bundled allow.txt parses against words.txt and holds the classified ws-scrcpy-web entries",
          len(entries) == 27 and len(ws) == 27
          and ("src/common/ScanMessage.ts", "cancelled") in ws
          and ("src/server/pairing/qr.ts", "centred") in ws
          and ("src/app/player/h265-utils.ts", "colour") in ws, sorted(ws))
    check("bundled allow.txt names no repository but the public ws-scrcpy-web",
          set(e[0] for e in entries) == {"bilbospocketses/ws-scrcpy-web"}, entries)
    repo = new_repo({"z.txt": "z\n"})
    commit(repo, "scan events", {"src/common/ScanMessage.ts": "type: 'scan.cancelled';\n",
                                 "src/other.ts": "type: 'scan.cancelled';\n"})
    code, out = run_cli(repo, "--repo-slug", "bilbospocketses/ws-scrcpy-web")
    check("the bundled allow.txt is read by default: an entry's path may use its word, "
          "another path may not",
          code == 1 and findings(out) == ["src/other.ts:1: cancelled -> canceled"], out)
    code, out = run_cli(repo, "--repo-slug", "bilbospocketses/other")
    check("...and no other repository may", code == 1 and len(findings(out)) == 2, out)


PRIVATE_ALLOW = "# a private repo's fixed identifier\nacme/secret lib/** colour\n"


def test_private_allow():
    repo = new_repo({"z.txt": "z\n"})
    commit(repo, "identifiers", {"lib/a.py": "colour = 1\n", "pub/b.py": "grey = 1\n",
                                 "other/c.py": "colour = 2\n"})
    public = allow_file("# a public identifier\nacme/secret pub/** grey\n")
    private = allow_file(PRIVATE_ALLOW)
    code, out = run_cli(repo, "--allow-file", public, "--private-allow", private,
                        "--repo-slug", "acme/secret")
    check("--private-allow: public and private entries both apply (one finding left)",
          code == 1 and findings(out) == ["other/c.py:1: colour -> color"], out)
    code, out = run_cli(repo, "--allow-file", public, "--repo-slug", "acme/secret")
    check("without the private file its entries do not apply",
          code == 1 and sorted(findings(out)) == ["lib/a.py:1: colour -> color",
                                                  "other/c.py:1: colour -> color"], out)
    code, out = run_cli(repo, "--private-allow", private, "--repo-slug", "acme/other")
    check("--private-allow: entries for another slug do not apply",
          code == 1 and len(findings(out)) == 3, out)
    code, out = run_cli(repo, "--private-allow", os.path.join(TMP_ROOT, "no-private.txt"))
    check("--private-allow <missing path> is exit 2, named", code == 2 and "no-private" in out, out)
    code, out = run_cli(repo, "--private-allow", allow_file("acme/secret lib/** colour\n"))
    check("--private-allow: an entry with no reason is exit 2", code == 2 and "reason" in out, out)

    env = dict(os.environ)
    env[PRIVATE_ENV] = private
    code, out = run_cli(repo, "--repo-slug", "acme/secret", env=env)
    check("$%s names the private file when --private-allow is not given" % PRIVATE_ENV,
          code == 1 and sorted(findings(out)) == ["other/c.py:1: colour -> color",
                                                  "pub/b.py:1: grey -> gray"], out)
    env[PRIVATE_ENV] = os.path.join(TMP_ROOT, "no-private.txt")
    code, out = run_cli(repo, "--repo-slug", "acme/secret", env=env)
    check("$%s naming a missing file is exit 2" % PRIVATE_ENV, code == 2 and "no-private" in out, out)
    code, out = run_cli(repo, "--repo-slug", "acme/secret", "--private-allow", private, env=env)
    check("--private-allow wins over $%s" % PRIVATE_ENV, code == 1 and len(findings(out)) == 2, out)
    env[PRIVATE_ENV] = ""
    code, out = run_cli(repo, "--repo-slug", "acme/secret", env=env)
    check("$%s empty: no private file is read" % PRIVATE_ENV, code == 1 and len(findings(out)) == 3, out)
    code, out = run_cli(repo, "--repo-slug", "acme/secret")
    check("$%s unset: no private file is read" % PRIVATE_ENV, code == 1 and len(findings(out)) == 3, out)


def _fake_fetcher(table):
    def fetcher(slug):
        answer = table[slug]
        if isinstance(answer, Exception):
            raise answer
        return answer
    return fetcher


def test_allow_public_check():
    checker = load("_allow_public", os.path.join(REPO, "check-allow-public.py"))
    allow = allow_file("# a\npub/one ** colour\npub/one lib/** grey\n"
                       "# b\npriv/two ** colour\n# c\ngone/three ** colour\n")
    table = {"pub/one": (200, {"private": False}), "priv/two": (200, {"private": True}),
             "gone/three": (404, None)}
    fetcher = _fake_fetcher(table)
    check("public check: a 200 with private false is public",
          checker.verdict("pub/one", fetcher) == "public")
    check("public check: a 200 with private true is not public",
          checker.verdict("priv/two", fetcher) == "not-public")
    check("public check: a 404 (private or missing) is not public",
          checker.verdict("gone/three", fetcher) == "not-public")
    check("public check: a 403 (rate limit) is an error, never a pass",
          checker.verdict("x/y", _fake_fetcher({"x/y": (403, None)})).startswith("error"))
    check("public check: a 200 with no `private` field is an error, never a pass",
          checker.verdict("x/y", _fake_fetcher({"x/y": (200, {})})).startswith("error"))
    check("public check: a network failure is an error",
          checker.verdict("x/y", _fake_fetcher({"x/y": OSError("offline")})).startswith("error"))
    seen = []

    def counting(slug):
        seen.append(slug)
        return table[slug]
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = checker.main(["--allow-file", allow], fetcher=counting)
    check("public check: a file naming a private and a missing repo fails (exit 1), each slug "
          "asked once", code == 1 and sorted(seen) == ["gone/three", "priv/two", "pub/one"],
          out.getvalue())
    with contextlib.redirect_stdout(io.StringIO()):
        code = checker.main(["--allow-file", allow_file("# a\npub/one ** colour\n")],
                            fetcher=fetcher)
    check("public check: a file naming only public repos passes (exit 0)", code == 0)
    with contextlib.redirect_stdout(io.StringIO()):
        code = checker.main(["--allow-file", allow_file("# a\nx/y ** colour\n")],
                            fetcher=_fake_fetcher({"x/y": (500, None)}))
    check("public check: an unconfirmed repo is exit 2", code == 2)
    with contextlib.redirect_stdout(io.StringIO()):
        code = checker.main(["--allow-file", allow_file("pub/one ** colour\n")], fetcher=fetcher)
    check("public check: a malformed allow file is exit 2", code == 2)
    bundled = []
    with contextlib.redirect_stdout(io.StringIO()):
        checker.main([], fetcher=lambda s: bundled.append(s) or (200, {"private": False}))
    check("public check: by default it reads the bundled allow.txt",
          bundled == ["bilbospocketses/ws-scrcpy-web"], bundled)


def test_words_file():
    repo = new_repo({"z.txt": "z\n"})
    commit(repo, "words", {"w.txt": "the zorp and the colour\n"})
    wdir = tempfile.mkdtemp(prefix="words-")

    def words_run(text, *extra):
        name = "w%d.txt" % len(os.listdir(wdir))
        write(wdir, name, text)
        path = os.path.join(wdir, name)
        if "--allow-file" not in extra:
            # The bundled allow.txt names words a custom list does not hold, which is exit 2.
            extra = extra + ("--allow-file", allow_file("# no entries\n"))
        return run_cli(repo, "--words", path, *extra)

    code, out = words_run("# a comment\n\nzorp zap\n")
    check("--words: a word the custom list holds is found, a word it does not hold is not",
          code == 1 and findings(out) == ["w.txt:1: zorp -> zap"], out)
    check("--words: the run reports the custom list's size", "1 British word(s) in the list" in out, out)
    code, out = run_cli(repo)
    check("default list: the bundled words.txt is used (colour found, zorp not)",
          code == 1 and findings(out) == ["w.txt:1: colour -> color"], out)
    for label, text in (("a line with three words", "zorp zap zip\n"),
                        ("a line with one word", "zorp\n"),
                        ("a word listed twice", "zorp zap\nzorp zip\n"),
                        ("a word mapped to itself", "zorp zorp\n"),
                        ("an upper-case word", "Zorp zap\n"),
                        ("a word with a digit", "zorp2 zap\n"),
                        ("a list with no words at all", "# only a comment\n")):
        code, out = words_run(text)
        check("--words: %s is exit 2" % label, code == 2 and "word list" in out, out)
    code, out = run_cli(repo, "--words", os.path.join(wdir, "missing.txt"))
    check("--words <missing path> is exit 2", code == 2 and "missing.txt" in out, out)
    allow = allow_file("# reason\nacme/widgets ** zorp\n")
    code, out = words_run("zorp zap\n", "--allow-file", allow, "--repo-slug", "acme/widgets")
    check("--words + allow file: an allow entry is checked against the CUSTOM list and applies",
          code == 0 and not findings(out), out)
    code, out = run_cli(repo, "--allow-file", allow, "--repo-slug", "acme/widgets")
    check("an allow entry naming a word only a custom list knows is exit 2 under the default list",
          code == 2 and "zorp" in out, out)


def _gate_repo_fixture():
    """A repository laid out like the gate's own: the script, words.txt and allow.txt at its root."""
    repo = new_repo({"README.md": "readme\n"})
    for name in ("check-american-spelling.py", "words.txt", "allow.txt"):
        with open(os.path.join(REPO, name), encoding="utf-8") as fh:
            write(repo, name, fh.read())
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "add the gate")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def test_self_skip():
    # A CONSUMER repository whose files share the gate's names: all scanned.
    repo = new_repo({"z.txt": "z\n"})
    commit(repo, "files named like the gate's own", {
        "check-american-spelling.py": "BRITISH = {'colour': 'color'}\n",
        "words.txt": "colour color\n",
        "allow.txt": "# why\nacme/widgets ** colour\n",
        "tests/run_tests.py": "FIXTURE = 'behaviour'\n"})
    code, out = run_cli(repo)
    check("in a consumer repo, files named like the gate's own are scanned (four findings)",
          code == 1 and sorted(findings(out)) == ["allow.txt:2: colour -> color",
                                                  "check-american-spelling.py:1: colour -> color",
                                                  "tests/run_tests.py:1: behaviour -> behavior",
                                                  "words.txt:1: colour -> color"], out)

    # The gate's OWN repository: the script, words.txt, allow.txt and the suite are skipped,
    # and the word list and allow file read are the ones beside the script.
    repo = _gate_repo_fixture()
    gate_copy = os.path.join(repo, "check-american-spelling.py")
    write(repo, "words.txt", "tumour tumor\n", mode="a")
    write(repo, "allow.txt", "\n# a fixture identifier, with a British reason: honour\n"
                             "acme/gate docs2.md honour\n", mode="a")
    with open(gate_copy, encoding="utf-8") as fh:
        gate_text = fh.read()
    commit(repo, "the gate's own files", {
        "check-american-spelling.py": gate_text + "# colour\n",
        "tests/run_tests.py": "FIXTURE = 'behaviour'\n",
        "tests/other.py": "FIXTURE = 'behaviour'\n",
        "docs.md": "a tumour\n",
        "docs2.md": "the honour\n"})
    want = ["docs.md:1: tumour -> tumor", "tests/other.py:1: behaviour -> behavior"]
    code, out = run_cli(repo, "--repo-slug", "acme/gate", gate=gate_copy)
    check("in the gate's own repo, the script, words.txt, allow.txt and tests/run_tests.py are "
          "skipped; any other file is not; the words.txt and allow.txt beside the script are read",
          code == 1 and sorted(findings(out)) == want, out)
    code, out = run_cli(repo, "--all", "--repo-slug", "acme/gate", gate=gate_copy)
    check("--all in the gate's own repo skips the same four files",
          code == 0 and sorted(findings(out)) == want, out)


def test_repo_option():
    repo = cached(_build_added)
    code, out = run_cli(None, cwd=repo)
    check("--repo defaults to the current directory", code == 1
          and "a.txt:2: colour -> color" in findings(out), out)
    code, out = run_cli(os.path.join(repo, "docs-x"))
    check("--repo <subdirectory> checks the whole repository, paths repo-relative",
          code == 1 and "a.txt:2: colour -> color" in findings(out)
          and "docs-x/new.md:3: favourite -> favorite" in findings(out), out)


def test_cli_basics():
    code, out = run_cli(None, "--version")
    check("--version prints the version and exits 0",
          code == 0 and out.strip() == "american-spelling 1.0.0", out)
    code, out = run_cli(None, "--no-such-flag")
    check("an unknown flag is exit 2", code == 2, out)
    code, out = run_cli(None, "--help")
    check("--help exits 0 and names every flag",
          code == 0 and all(f in out for f in ("--repo", "--base", "--words", "--all",
                                               "--version")), out)


def test_binary_and_deleted():
    repo = new_repo({"old.txt": "the colour\n"})
    commit(repo, "binary in, text out", {"img.bin": b"\x00\x01colour\x00\n"}, remove=("old.txt",))
    code, out = run_cli(repo)
    check("a binary file and a deleted file carry no added lines (exit 0)",
          code == 0 and not findings(out), out)


def test_missing_base():
    repo = new_repo({"z.txt": "z\n"})
    code, out = run_cli(repo, "--base", "origin/no-such-branch")
    check("a base ref that does not resolve is exit 2, named", code == 2 and "no-such-branch" in out, out)
    code, out = run_cli(tempfile.mkdtemp(prefix="not-a-repo-"))
    check("a directory that is not a git work tree is exit 2", code == 2, out)


def test_shallow_clone():
    up = new_repo({"z.txt": "z\n"})
    commit(up, "feat work", {"f.txt": "f\n"})
    git(up, "checkout", "-q", "main")
    commit(up, "main moves on", {"m.txt": "m\n"})
    git(up, "checkout", "-q", "feat")
    parent = tempfile.mkdtemp(prefix="shallow-")
    url = pathlib.Path(up).as_uri()
    subprocess.run(["git", "-C", parent, "-c", "core.hooksPath=" + os.devnull, "clone", "-q",
                    "--depth", "1", "--branch", "feat", url, "ws"], check=True, capture_output=True)
    ws = os.path.join(parent, "ws")
    git(ws, "fetch", "-q", "--no-tags", "--depth=1", "origin",
        "+refs/heads/main:refs/remotes/origin/main")
    code, out = run_cli(ws)
    check("a shallow clone with no merge-base is exit 2, and says shallow",
          code == 2 and "SHALLOW" in out, out)

    # A shallow clone of a branch that MERGED main, deep enough that the merge-base (main's
    # tip) is present but its parents are cut off. The merge-base EXISTS, so a gate that only
    # looks at shallowness when the merge-base fails would scan on and, on a real history,
    # read every commit message down to the cut.
    up = new_repo({"z.txt": "z\n"})
    commit(up, "feat work", {"f.txt": "f\n"})
    git(up, "checkout", "-q", "main")
    commit(up, MAIN_MESSAGE, {"m.txt": "m\n"})
    commit(up, "main moves again", {"m2.txt": "m2\n"})
    git(up, "checkout", "-q", "feat")
    git(up, "merge", "-q", "--no-ff", "--no-edit", "main")
    commit(up, "feat after the merge", {"g.txt": "g\n"})
    parent = tempfile.mkdtemp(prefix="shallow-merged-")
    subprocess.run(["git", "-C", parent, "-c", "core.hooksPath=" + os.devnull, "clone", "-q",
                    "--depth", "3", "--branch", "feat", pathlib.Path(up).as_uri(), "ws"],
                   check=True, capture_output=True)
    ws = os.path.join(parent, "ws")
    git(ws, "fetch", "-q", "--no-tags", "--depth=1", "origin",
        "+refs/heads/main:refs/remotes/origin/main")
    fixture_ok = (git(ws, "rev-parse", "--is-shallow-repository").strip() == "true"
                  and bool(git(ws, "merge-base", "origin/main", "HEAD").strip()))
    check("fixture: the merged-main clone is shallow AND has a merge-base", fixture_ok)
    code, out = run_cli(ws)
    check("a shallow clone of a branch that merged main (merge-base present) is exit 2, says shallow",
          code == 2 and "SHALLOW" in out, out)

    # Unshallowed, the same clone is accepted: the refusal is about depth, not about clones.
    git(ws, "fetch", "-q", "--unshallow", "origin")
    git(ws, "fetch", "-q", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main")
    code, out = run_cli(ws)
    check("the same clone after `git fetch --unshallow` is checked normally (exit 0)",
          code == 0 and not findings(out), out)


def test_all_mode():
    repo = new_repo({"old.txt": "the colour\nthe grey cat\n", "img.bin": b"\x00colour\x00"})
    commit(repo, "plain", {"new.txt": "the behaviour\n"})
    code, out = run_cli(repo, "--all")
    got = findings(out)
    check("--all: exit 0 even with findings (report only)", code == 0, out)
    check("--all: old text, new text, no binary",
          sorted(got) == ["new.txt:1: behaviour -> behavior", "old.txt:1: colour -> color",
                          "old.txt:2: grey -> gray"], got)
    check("--all: a summary with the count and the top words",
          "3 British word(s) in 2 file(s)" in out and "top: " in out, out)


def test_units():
    gate = load("_spell_units")
    words = gate.load_words(WORDS)
    check("pieces: camel, snake, kebab, digits and acronyms are cut",
          list(gate.pieces("HTMLColour bgColour colour_for my-colour x1colour2 COLOURS"))
          == ["HTML", "Colour", "bg", "Colour", "colour", "for", "my", "colour", "x", "colour", "COLOURS"],
          list(gate.pieces("HTMLColour bgColour colour_for my-colour x1colour2 COLOURS")))
    check("match_case keeps lower, Title and UPPER",
          [gate.match_case(w, "color") for w in ("colour", "Colour", "COLOUR")] == ["color", "Color", "COLOR"])
    check("words.txt maps every required stem",
          all(words.get(w) == a for w, a in (
              ("colour", "color"), ("behavioural", "behavioral"), ("favourites", "favorites"),
              ("organisation", "organization"), ("analysing", "analyzing"), ("centred", "centered"),
              ("greyed", "grayed"), ("cancelled", "canceled"), ("marshalling", "marshaling"),
              ("dialogue", "dialog"), ("licence", "license"), ("judgement", "judgment"),
              ("fulfilment", "fulfillment"), ("enrol", "enroll"), ("whilst", "while"),
              ("amongst", "among"), ("practised", "practiced"), ("speciality", "specialty"))))
    check("words.txt holds no word that is also American",
          not set(words) & {"analyses", "cancellation", "licensed", "controlled", "promise",
                            "programmer", "parameter", "practice"})
    check("words.txt: no British word is also an American spelling in the list",
          not set(words) & set(words.values()), sorted(set(words) & set(words.values())))
    with open(GATE, encoding="utf-8") as fh:
        code_lines = fh.read().split('"""', 2)[2].splitlines()  # everything after the docstring
    in_code = [(n, hit) for n, ln in enumerate(code_lines, 1) for hit in gate.scan_line(ln, words)]
    check("the gate's code (outside its docstring) spells no listed word: the list is data only",
          not in_code, in_code)
    rx = gate.glob_to_regex("lib/**/*.py")
    check("glob: **/ spans zero or more directories, * stays in one",
          bool(rx.match("lib/a.py")) and bool(rx.match("lib/x/y/a.py")) and not rx.match("lib/a.txt")
          and not gate.glob_to_regex("lib/*.py").match("lib/x/a.py"))
    for path in (GATE, WORDS, os.path.abspath(__file__)):
        with open(path, "rb") as fh:
            data = fh.read()
        check("%s is ASCII with LF line endings" % os.path.basename(path),
              all(b < 128 for b in data) and b"\r" not in data)


# ---- mutants -------------------------------------------------------------------

def mutant_runner(patch):
    def run(repo, *extra):
        mod = load("_spell_mutant")
        patch(mod)
        return run_mod(mod, repo, *extra)
    return run


def _no_camel(mod):
    mod.CAMEL = re.compile(r"[A-Za-z]+")


def _kept_inside(chars):
    """A tokenizer that keeps CHARS inside a word. Both regexes change together: the
    camelCase cut skips every non-letter on its own, so widening only the letter
    run would be an EQUIVALENT mutant that proves nothing."""
    def patch(mod):
        mod.LETTER_RUN = re.compile(r"[A-Za-z%s]+" % chars)
        mod.CAMEL = re.compile(r"[A-Z{c}]+(?=[A-Z][a-z])|[A-Z{c}]?[a-z{c}]+|[A-Z{c}]+".format(c=chars))
    return patch


def _substring(mod):
    def scan_line(text, words):
        if mod.MARKER in text.lower():
            return []
        low = text.lower()
        return [(w, a) for w, a in sorted(words.items()) if w in low]
    mod.scan_line = scan_line


def _diff_rewrite(prefix_to_plus, unified):
    """Count diff lines starting with PREFIX_TO_PLUS as added, at --unified=UNIFIED."""
    def patch(mod):
        real = mod.git

        def git(repo, args):
            if args[:1] != ["diff"]:
                return real(repo, args)
            args = ["--unified=%d" % unified if a == "--unified=0" else a for a in args]
            rc, out, err = real(repo, args)
            fixed = []
            for ln in out.split("\n"):
                if ln.startswith(prefix_to_plus) and not ln.startswith("---"):
                    ln = "+" + ln[1:]
                fixed.append(ln)
            return rc, "\n".join(fixed), err
        mod.git = git
    return patch


def _git_arg(old, new):
    def patch(mod):
        real = mod.git

        def git(repo, args):
            if args[:1] == ["diff"]:
                args = [new if a == old else a for a in args]
            return real(repo, args)
        mod.git = git
    return patch


def _messages_all_history(mod):
    real = mod.git

    def git(repo, args):
        if args[:1] == ["log"]:
            args = args[:-1] + ["HEAD"]
        return real(repo, args)
    mod.git = git


def _diff_from_tip(mod):
    real = mod.git

    def git(repo, args):
        if args[:1] == ["diff"]:
            args = args[:-2] + ["origin/main", "HEAD"]
        return real(repo, args)
    mod.git = git


def _no_marker(mod):
    mod.MARKER = "\x00never on a line\x00"


# (name, patch, the row it must turn red)
MUTANTS = (
    ("no camelCase cut", _no_camel, "identifiers: ids.py:3: Colour -> Color"),
    ("`_` kept inside a word", _kept_inside("_"), "identifiers: ids.py:2: colour -> color"),
    ("`-` kept inside a word", _kept_inside("-"), "identifiers: ids.py:4: colour -> color"),
    ("digits kept inside a word", _kept_inside("0-9"), "identifiers: ids.py:6: colour -> color"),
    ("substring match instead of whole words", _substring, "words a suffix or substring rule"),
    ("context lines counted as added", _diff_rewrite(" ", 3), "an untouched British line"),
    ("removed lines counted as added", _diff_rewrite("-", 0), "a British line the branch REMOVES"),
    ("rename detection off", _git_arg("--find-renames", "--no-renames"), "a file the branch only RENAMES"),
    ("diff taken from main's tip, not the merge-base", _diff_from_tip, "a line main deleted"),
    ("commit messages read over all history", _messages_all_history,
     "branch merges main: main's and the base's commit messages"),
    ("the inline marker ignored", _no_marker, "the inline marker skips its line"),
)


def test_mutants():
    # The unmutated module, in process, must pass every scenario first: a red row
    # under a mutant is only evidence if the same row is green without it.
    control = run_scenarios(mutant_runner(lambda m: None), "")
    check("in-process control: every scenario passes against the unmutated gate",
          all(ok for _l, ok, _d in control), [l for l, ok, _d in control if not ok])
    for name, patch, row in MUTANTS:
        rows = run_scenarios(mutant_runner(patch), "")
        red = [l for l, ok, _d in rows if not ok]
        killed = any(l.startswith(row) for l in red)
        print("mutant %-48s %s (%d row(s) red)" % (name, "KILLED" if killed else "SURVIVED", len(red)))
        check("mutant killed: %s -- turns red: %r" % (name, row), killed, red)


def main():
    for t in (test_units, test_cli_basics, test_real_gate, test_allow_slug,
              test_allow_slug_from_remote, test_allow_file_errors,
              test_allow_never_in_commit_messages, test_bundled_allow, test_private_allow,
              test_allow_public_check, test_words_file,
              test_self_skip, test_repo_option, test_binary_and_deleted, test_missing_base,
              test_shallow_clone, test_all_mode, test_mutants):
        try:
            t()
        except Exception as exc:  # a crashing test is a failed row, never a pass
            check("%s raised %s" % (t.__name__, type(exc).__name__), False, repr(exc))
    failed = [r for r in RESULTS if not r[1]]
    for name, _ok, detail in failed:
        print("FAIL  %s\n      %s" % (name, str(detail)[:1500]))
    print("american-spelling: %d/%d passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
