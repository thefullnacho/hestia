# Security

Read this before you run Hestia. The brain is an LLM that can take real actions in your home,
so its security model is about **where you run it**, not just the code.

## Trust model — the one thing that matters

**The brain has no authentication of its own.** It is designed to sit on a private network
(Tailscale tailnet or LAN) and trusts anything that can reach its port. Whoever can talk to the
brain can ask it to do anything its tools allow — including **controlling your Home Assistant
devices** (lights, switches, anything you've exposed) via your HA token.

So:

- **Never expose the brain (default `:8730`) to the public internet.** No port-forward, no
  public reverse proxy. Reach it remotely over Tailscale/WireGuard/VPN only.
- Bind it to a private address (a tailnet/LAN IP or `127.0.0.1`), **never `0.0.0.0`** on a host
  with a public interface.
- Treat the network boundary as the auth boundary. If your tailnet is compromised, so is your house.

## What's deliberately NOT here

**There is no shell / `bash` tool, by design.** An unauthenticated brain with arbitrary shell
access would be a far bigger liability than a denylist could contain, so the production toolset is
eight scoped, non-arbitrary tools (`home, media, memory, records, reminder, search, status,
weather`) and nothing that runs free-form commands. The standing rule in
`brain/tools/__init__.py` is: **do not reintroduce a general shell tool.** If you fork and add one,
you own that risk — and you should not run that fork anywhere reachable by an untrusted network.

## Tokens & secrets

- All secrets live in `secrets/` (HA token, `*arr`/media creds, ingest token, service hosts).
  `secrets/` is **gitignored** — keep it that way; never commit real tokens.
- **Scope the Home Assistant token to least privilege.** The brain only needs the entities it
  actually controls. A full-access long-lived token means a prompt-injected or misused brain can
  touch everything in HA.
- The photo/records **ingest endpoint requires a token** (`X-Ingest-Token` header or `Authorization:
  Bearer`). Set a strong `INGEST_TOKEN`; without one the endpoint refuses requests. This is still
  not a substitute for keeping the service on a private network.

## Data

- The records database (`data/`) and learned memories (`memory/`) hold personal data (people,
  pets, your home). Both are **gitignored** and never leave your boxes except via your own backups
  (see `deploy/backup/OFFSITE-RUNBOOK.md` — the off-site copy is encrypted at rest).

## Reporting a vulnerability

This is a personal/self-hosted project, not a hosted service. If you find a security issue, please
open a **private** GitHub security advisory on the repository rather than a public issue.
