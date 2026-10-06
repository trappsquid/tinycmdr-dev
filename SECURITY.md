# Security Policy

tinycmdr is an agent harness: it holds API tokens in `.env`, executes shell
commands, edits files, and serves a token-gated web page. I take reports about
these surfaces seriously.

## Reporting a Vulnerability

**Please do not open a public issue for a security report.**

**GitHub Private Vulnerability Reporting** is the sole channel: the repository's
*Security → Advisories → Report a vulnerability* button. If that button is not available to
you, open a normal issue saying only "I have a security report" and I
(`@trappsquid`) will open a private advisory to continue there - never post the details
publicly.

Include: the version or commit you tested, the affected component, a description
of the impact, and reproduction steps or a proof of concept. You will receive an
acknowledgement within a few days; I aim to confirm or reject within two weeks
and will coordinate disclosure timing with you.

## Scope

Especially interested in:

- **Token and secret handling** — anything that gets a token into a prompt, a
  log, a transcript, a chat message, or the web page
- **Path traversal / arbitrary file access** through the file tools, uploads,
  downloads, or the chat lanes
- **Web page auth** — token-gating bypasses, session fixation, cross-host
  exposure when bound to `0.0.0.0`
- **Command injection** through tool-call argument parsing and repair
- **Update flow** — the SHA256SUMS verification is checksumming, not signing;
  reports about the integrity model are welcome
- **Installer privilege handling** — anything that runs as root on Linux/macOS

## Out of scope

- Vulnerabilities in a self-hosted model you connect tinycmdr to
- Issues requiring physical access to the host
- The documented warning that a `0.0.0.0` bind sends the token in cleartext on
  the LAN — that is a stated, deliberate trade-off, not a bug

## Supported Versions

Only the latest release receives security fixes. Update with `tinycmdr update`.
