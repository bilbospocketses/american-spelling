# Security Policy

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Report them privately through GitHub's security advisory flow:

**[Report a vulnerability](https://github.com/bilbospocketses/american-spelling/security/advisories/new)**

This opens a private channel with the maintainer. Nothing is disclosed publicly until a fix is
ready.

## What to Include

- A description of the issue and its impact
- Steps to reproduce: the repository state, the command line, and the output
- The affected version (tag or commit)

## Response Expectations

- **Acknowledgment:** within 72 hours
- **Triage and initial assessment:** within one week
- **Fix and disclosure timeline:** agreed with the reporter, depending on severity

## Supported Versions

Fixes land on `main` and ship in a new tag. Only the latest tag is supported; consumers
upgrade by bumping the tag or commit they pin.

## Scope

In scope:

- a way to make the gate exit 0 on a branch that adds British spelling it should catch,
  outside the documented exceptions (for example, through crafted file names, diff content
  or commit messages);
- anything that makes the gate run code or write files in the repository it checks;
- a way to get a private repository's name into `allow.txt` past `check-allow-public.py`.

Out of scope: words missing from `words.txt` (open an issue or a pull request instead), and
issues that need write access to the consumer's CI configuration.
