# Security Policy

AI_Engineering_Cockpit handles API keys, model outputs, and (depending on how
you configure it) potentially sensitive data flowing through the security,
governance, and compliance frameworks. We take security issues seriously and
appreciate responsible disclosure.

## Supported Versions

This project is pre-1.0 and evolving rapidly. Security fixes are applied to
the `main` branch only.

| Version        | Supported          |
| -------------- | ------------------- |
| `main` (latest) | :white_check_mark: |
| older tags/commits | :x:             |

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report it privately through
[GitHub Security Advisories](https://github.com/AshraHossain/AI_Engineering_Cockpit/security/advisories/new) for this repository.
That channel is private, notifies the maintainer directly, and gives us a
place to coordinate a fix and disclosure with you.

It is deliberately the only channel listed. A security policy that names an
unmonitored inbox is worse than one that names none: it routes a real report
somewhere nobody is reading.

When reporting, please include:

- A description of the vulnerability and its potential impact
- Steps to reproduce (proof-of-concept code or commands, if possible)
- The affected file(s)/module(s) (e.g. `cockpit/security/input_security.py`)
- Any suggested mitigation, if you have one

### What to expect

- **Acknowledgment:** within 3 business days
- **Initial assessment:** within 7 business days, including severity and
  next steps
- **Fix & disclosure:** we aim to ship a fix and coordinate disclosure within
  90 days of the report, sooner for critical issues. We will credit you in
  the fix's release notes unless you prefer to remain anonymous.

## Scope

In scope:

- The `cockpit/` core framework (testing, evaluation, red teaming, security,
  monitoring, governance modules)
- Example projects under `projects/`
- CI/CD workflows under `.github/workflows/`
- Setup and tooling scripts under `scripts/`

Out of scope:

- Vulnerabilities in third-party dependencies (please report those upstream;
  we welcome a heads-up so we can pin/patch, but the primary fix belongs
  with the upstream project)
- Issues that require an already-compromised machine or `.env` file
- Social engineering or physical security

## Secret Handling

If you find a **hardcoded credential, API key, or secret** committed to this
repository (including in git history), report it the same way — treat it as
a security vulnerability, not a bug. Do not open a public issue that
references the specific commit or file until it has been rotated/removed.

## Disclosure Policy

We follow coordinated disclosure: please give us a reasonable window to
investigate and ship a fix before any public disclosure. We will keep you
updated throughout the process.
