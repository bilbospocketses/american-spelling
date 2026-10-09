# Contributing

Thanks for helping. This is a small, solo-maintained project, so the process is short.

## Before you open a pull request

1. **Run the suite.** `python tests/run_tests.py` must end with `N/N passed`. It needs `git` and
   Python 3, and nothing else.
2. **If you changed `allow.txt`, run `python check-allow-public.py`.** Every repository named
   in `allow.txt` must be public. Exceptions for a private repository never go in this
   repository: they belong in that repository owner's private allow file (see the README).
3. **Write in American spelling.** CI runs the gate on your pull request's added lines and
   commit messages. Use `spelling: allow` on a line only for a verbatim quote.

## Adding words to `words.txt`

- One `british american` pair per line, both lower case, and **every inflection on its own
  line** (`-ise`, `-ises`, `-ised`, `-ising`, ...). There are no suffix rules.
- Before adding a word, make sure it is not also an American spelling of something else.
  `analyses` (the plural of analysis), `licensed` and `practice` are all American and must
  not be listed.

## Adding an `allow.txt` entry

An entry is `<owner/repo> <path-glob> <word>` under a `#` comment that says why the word
cannot be changed: a protocol value, a library's parameter name, an upstream file. Name the
narrowest glob that covers the uses. A new entry ships with the next tag.

## Pull requests

- Branch from `main`, keep each pull request to one concern, and add a line under
  `## [Unreleased]` in `CHANGELOG.md`.
- Commits must be signed; `main` only accepts signed commits through a pull request.
- Pull requests are squash-merged once CI is green.
