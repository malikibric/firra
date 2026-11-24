# Security policy

Firra handles sensitive client communication, so security reports are taken seriously.

**Please do not open a public issue for a vulnerability.** Use GitHub's private
[security advisory form](https://github.com/malikibric/fira/security/advisories/new) for this repository.

Include what you found, how to reproduce it, and the impact you expect. You will get an acknowledgement
as soon as the maintainer can respond; this is an early-stage project run by a very small team.

In scope: the engine (`services/worker`), the Mac app (`apps/mac`), the control plane and relay (`apps/api`),
the MCP server, and the database policies in `supabase/`. The design goal is that client data never leaves the
user's Mac; any path that breaks that is a high-severity bug.

Never include real customer data in a report. Use the synthetic dataset.
