# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-09

### Added

- `check-american-spelling.py`: fails when the lines a branch adds since its merge-base, or
  the commit messages in that range, use British spelling. Stdlib-only Python 3.
- `words.txt`: the word list as data, 405 British -> American pairs with every inflection
  listed. No suffix rules.
- Words are found inside identifiers: letter runs are cut at digits, `_`, `-`, punctuation
  and camelCase boundaries, and only whole pieces count.
- Exceptions: the inline `spelling: allow` marker (files and commit messages), the public
  `allow.txt` for public repositories, and a private allow file passed by `--private-allow`
  or `AMERICAN_SPELLING_PRIVATE_ALLOW`. Entries are `<owner/repo> <path-glob> <word>` under
  a reason comment, selected by `--repo-slug` or the `origin` remote.
- `check-allow-public.py`: fails unless every repository named in `allow.txt` is public.
- `--all` audit mode (report only), `--words`, `--version`.
- Exit 2 for a missing base ref, a shallow clone, no merge-base, or a malformed word list
  or allow file, so a misconfigured run can never read as a pass.
- Test suite on real git fixtures, with mutation checks, run on Linux and Windows.

[Unreleased]: https://github.com/bilbospocketses/american-spelling/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/bilbospocketses/american-spelling/releases/tag/v1.0.0
