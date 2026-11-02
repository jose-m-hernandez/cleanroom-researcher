# The owner's own web addresses are listed in a checked file

Date: 2026-11-01

> **The rule.** Rule A6 also accepts a web address listed in `docs/owner-addresses.md`, matched exactly, with no prefix, host or normalised match. Each row of the list must be well formed, with an `https` address and a non-empty description, and each address may appear once. A listed address that no other file uses is a finding.

## Why

The maintainer line planned for the home page links to the maintainer's own website. That address is not a source, and it must not pretend to be one by getting a source record.

A6 exists so that every web address in the repository is either evidence (the address of a source record) or harmless (a reserved example host). The list is a third category that stays checked: it is public and short, every entry has to be used somewhere, and an address matches only when it is written exactly as listed.

The list ships empty. Its first entry arrives together with the line that uses it, because an entry nothing uses is itself a finding.
