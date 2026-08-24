# Code of Conduct

## The short version

Be decent. Argue with the code, not the person who wrote it.

This is a small project. It does not need a governance structure, and pretending
otherwise would be theatre — but it does need people to know where the line is
and who to tell when someone crosses it, so this is written down rather than
assumed.

## What is expected

- **Criticise the work, specifically.** "This drops the result when two
  commands share a delivery id, here is the sequence" is useful. "This is
  garbage" is not, and neither is any version of it aimed at the author rather
  than the patch.
- **Assume the other person has context you do not.** Much of this code looks
  wrong until you know what it is defending against, which is why the comments
  say why rather than what. Ask before concluding.
- **Take a correction as information.** Being wrong in public is the ordinary
  cost of working in the open, and it is cheaper than being wrong in private
  for longer.
- **Disclose an agent's involvement** where [`CONTRIBUTING.md`](CONTRIBUTING.md)
  asks for it. Using one is fine; quietly passing its unverified claims off as
  checked is not, and that is a conduct problem rather than a technical one.

## What is not acceptable

Harassment, personal attacks, demeaning comments about someone's identity or
background, sexualised content, deliberate intimidation, sustained disruption,
publishing someone's private information, and continuing any of the above after
being asked to stop.

Also not acceptable, because this project's subject matter invites it: using an
issue, a pull request or a proof-of-concept as cover for attacking someone
else's systems. Daedalus drives a real browser with real credentials. Report
what it does wrong; do not use it against a third party and file the result
here.

## Scope

Anywhere the project happens — issues, pull requests, commit messages, code
review, discussions and release notes — and to anyone taking part, maintainers
included. Conduct outside these spaces is in scope only when it is directed at
someone because of their participation here.

## Reporting

Report it privately to the repository owner, through
[a GitHub security advisory](https://github.com/Nitjsefnie-Harness-Commons/daedalus/security/advisories/new)
if you want it confidential, or by contacting
[@Nitjsefnie](https://github.com/Nitjsefnie) directly. Do not open a public
issue about someone's conduct — that escalates it before anyone has heard both
sides.

A report will be read by a person, not a process. You will get a reply. If the
report is about the maintainer, say so and it will still be read; there is no
third party to escalate to on a project this size, and pretending there is
would be worse than saying so plainly.

## Enforcement

Proportionate and stated. In rough order: a private word, a public correction,
edited or removed content, a temporary block, a permanent one. Which applies
depends on severity, on whether it continued after being raised, and on whether
the person is arguing in good faith and getting it wrong or is not.

The decision and the reason will be given to the person it affects. It will not
be relitigated repeatedly.

## Attribution

Written for this project rather than adopted wholesale, but it owes its
structure to the Contributor Covenant, which is the right starting point for
most projects and worth reading if you are drafting one. It is not linked here
because `test_no_deployment_strings_in_tree` allows only `github.com` inside an
`https://` URL anywhere in the tree, and weakening that guard for a
documentation link would be the wrong trade.
