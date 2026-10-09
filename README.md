# american-spelling

A CI gate that fails a pull request when the lines it adds, or its commit messages, use British
spelling instead of American spelling.

- **Stdlib-only Python 3**, one script, no install step. It needs `git` on the `PATH`.
- **An explicit word list, never suffix rules.** A rule such as "-ise is British" would flag
  promise, exercise, otherwise and surprise; "-our" would flag four, hour and your. The gate
  instead looks every word up in [`words.txt`](words.txt), which lists 405 British spellings
  and the American spelling of each, with every inflection written out.
- **Words inside identifiers are found.** A line is cut into letter runs (digits, `_`, `-` and
  punctuation separate them), and each run is cut again at camelCase boundaries, so
  `COLOURS`, `colour_for`, `bgColour`, `my-colour` and `HTMLColour` are all caught. <!-- spelling: allow -->
  Only whole pieces count, so `parameter` never matches `metre`. <!-- spelling: allow -->

## What it checks, and what it does not

With `B` = the merge-base of `HEAD` and `--base` (default `origin/main`), the gate reads:

- every line **added** in `git diff B HEAD`. Renames are detected, so a moved file is not
  a rewritten one, and binary and deleted files carry no added lines;
- every line of every **commit message** in `B..HEAD`.

Text that already existed is not scanned: it is fixed when someone touches it. A branch that
merges `main` is checked for its own lines and messages, never for `main`'s.

The gate never sees **file names, pull request titles or pull request bodies**. Those should
use American spelling too, but nothing here checks them.

`--all` scans every tracked text file instead. It is for audits: it reports and always exits 0.

## Exceptions

Exceptions are for verbatim quotes and fixed external identifiers, such as a library's own
parameter name, a protocol value, or a file name another system owns.

1. **The inline marker.** Put `spelling: allow` anywhere on the line, in any comment syntax
   or in prose, and that line is skipped. This works in files and in commit messages, and it
   is the **only** exception a commit message can use.
2. **The allow files**, for an identifier used on many lines. Each entry is
   `<owner/repo> <path-glob> <word>`. For example, a protocol value that external clients send
   is exempted by the line `example-org/example-repo src/protocol/** cancelled` under a comment such as `# Reason: a wire value external clients send.` <!-- spelling: allow -->

   - Every entry sits under a `#` comment giving its reason. One comment covers every entry
     that follows it, up to the next blank line.
   - `owner/repo` selects the repository the entry applies to (case-insensitive). Globs are
     relative to that repository's root: `*` and `?` stay inside one directory, `**/` spans
     any number of directories, and `**` on its own matches every path.
   - The word is lower case and must be in `words.txt`.
   - An entry with no reason, a malformed line or an unknown word stops the gate with exit 2.
     A mistake in an allow file is never a silent no-op.
   - Entries apply to files only, never to commit messages.

There are **no per-repository allow files**. The gate reads two central ones.

### Public repositories: `allow.txt`

[`allow.txt`](allow.txt) ships with the gate. It may name **public repositories only**.
Adding an exception is a pull request to this repository followed by a new tag, and a
consumer picks it up by bumping the tag it pins.

CI enforces the public-only rule with `check-allow-public.py`. It asks the GitHub API,
without authentication, about every repository named in `allow.txt`, and fails unless each
one answers as public. A 404 fails too: to an anonymous caller a private repository and a
missing one look the same. **Run it before you push a change to `allow.txt`:**

```
python check-allow-public.py
```

### Private repositories: a private allow file

A private repository's name must never appear in this public repository. Its exceptions live
in a private allow file kept outside this repository. It uses the same format and the same
validation, and the consumer's own CI passes its path to the gate with `--private-allow <path>`
or the `AMERICAN_SPELLING_PRIVATE_ALLOW` environment variable. When neither is set, no private
file is read. A path that is given but does not exist stops the gate with exit 2.

### Which repository is being scanned

Allow entries apply only to the repository they name. The gate takes the name from
`--repo-slug <owner/repo>`, or derives it from the scanned repository's `origin` remote URL
(HTTPS or SSH). If neither works, no allow entry applies, and the gate says so on stderr.

## Command line

```
python check-american-spelling.py [--repo PATH] [--base REF] [--repo-slug OWNER/REPO]
                                  [--private-allow PATH] [--words PATH] [--all] [--version]
```

| Flag | Default | Meaning |
|---|---|---|
| `--repo PATH` | the current directory | The repository to check. A subdirectory is fine; the whole repository is checked. |
| `--base REF` | `origin/main` | The ref whose merge-base with `HEAD` bounds the check. |
| `--repo-slug OWNER/REPO` | from the `origin` remote URL | Selects this repository's entries in the allow files. |
| `--private-allow PATH` | `$AMERICAN_SPELLING_PRIVATE_ALLOW`, else none | The private allow file. |
| `--words PATH` | `words.txt` beside the script | The word list. |
| `--all` | off | Scan every tracked text file. Report only; always exits 0. |
| `--version` | | Print `american-spelling 1.0.0` and exit. |

`--allow-file PATH` replaces the bundled `allow.txt`. It exists for the test suite only.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Clean (and always, with `--all`). |
| 1 | British spelling found. Each finding prints as `path:line: word -> american`, or `commit <sha>:<line>: ...` for a commit message. |
| 2 | Usage error; a word list or allow file that is missing or malformed; a base ref that does not resolve; a **shallow** clone; or no merge-base. |

A shallow clone is refused even when a merge-base exists. With the merge-base's parents cut
off, `B..HEAD` would walk the whole history and fail on old commit messages. Note that
`git fetch --depth=1` makes even a full clone shallow, so fetch the base branch without `--depth`.

## Use it in GitHub Actions

Pin the gate by tag or by full commit SHA. **The pin is the upgrade lever:** a new word, a new
`allow.txt` entry or a fix reaches your repository when you bump it.

```yaml
name: American spelling

on:
  pull_request:

permissions:
  contents: read

jobs:
  american-spelling:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0  # the gate refuses a shallow clone

      - name: Fetch the base branch
        run: git fetch --no-tags origin "+refs/heads/${BASE}:refs/remotes/origin/${BASE}"
        env:
          BASE: ${{ github.base_ref || 'main' }}

      - name: Check out the gate
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          repository: bilbospocketses/american-spelling
          ref: v1.0.0
          path: .american-spelling
          persist-credentials: false

      # Optional: the runner's own Python 3 works too.
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: '3.x'

      - name: Check American spelling
        run: >-
          python .american-spelling/check-american-spelling.py
          --repo .
          --base "origin/${BASE}"
          --repo-slug "${SLUG}"
        env:
          BASE: ${{ github.base_ref || 'main' }}
          SLUG: ${{ github.repository }}
```

The gate's checkout lands in `.american-spelling/`, which is untracked in your repository, so
it is never part of the diff. Make the job a required status check to block merges on it.

## Use it anywhere else

```
git clone --branch v1.0.0 https://github.com/bilbospocketses/american-spelling.git
git -C <your-repo> fetch origin main          # no --depth: the gate needs full history
python american-spelling/check-american-spelling.py --repo <your-repo> --base origin/main
```

If your clone is shallow, run `git -C <your-repo> fetch --unshallow origin` first. Pass
`--repo-slug` when your `origin` remote is not a GitHub URL.

## Development

```
python tests/run_tests.py       # the suite: real git fixtures, offline
python check-allow-public.py    # needs the network: every allow.txt repository is public
```

The suite builds real git repositories in a temporary directory. It also breaks the gate on
purpose in eleven ways (no camelCase cut, substring matching, diffing from `main`'s tip,
reading commit messages over all history, and more) and checks that each break turns a case
red. CI runs it on Linux and Windows, and runs the gate on this repository's own pull requests.
When the gate checks its own repository, it skips the files that have to spell the British
words: the script, `words.txt`, `allow.txt` and `tests/run_tests.py`.

## License

[MIT](LICENSE)
