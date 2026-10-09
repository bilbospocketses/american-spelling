# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.1] - 2026-10-09

### Added

- Substring stems: a curated list of 16 British -our stems (the British forms of color,
  behavior, favor, honor, neighbor, flavor, harbor, humor, rumor, labor, vapor, savor, odor,
  armor, clamor and endeavor) is also matched inside a piece that is not a listed word, so a
  compound with no separator is caught. A hit prints as
  `<piece> (contains <stem>) -> <american piece>`. The stems are data: the
  `substring <stem> <american>` lines of `words.txt`. A stem that occurs inside an American
  spelling in the list is a word-list error (exit 2). `glamour`, American too, is excluded.
- Allow entries may name a substring stem or a whole compound piece; either exempts the hit.
  The inline marker skips substring hits like any other.
- `words.txt`: 47 new pairs, 452 in all: the British spellings of math, analog(s), the full
  -ize sets of rasterize, parallelize, localize, virtualize and generalize, and recolor with
  its inflections.
- `allow.txt`: entries for the public `bilbospocketses/streamflex` (a vendored SVG color
  keyword table, and a Prime Video feature name).
- README: "Adopting on an existing branch", since the gate also checks a branch's commit
  messages.

### Changed

- README: the Actions snippet takes the base branch from the repository
  (`github.base_ref || github.event.repository.default_branch`) instead of assuming `main`,
  and pins the gate by full commit SHA with the tag in a trailing comment, which is now the
  recommended pin.

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

[Unreleased]: https://github.com/bilbospocketses/american-spelling/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/bilbospocketses/american-spelling/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/bilbospocketses/american-spelling/releases/tag/v1.0.0
