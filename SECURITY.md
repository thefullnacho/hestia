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
ten scoped, non-arbitrary tools (`home, media, memory, records, recipe, reminder, search,
shopping, status, weather`) and nothing that runs free-form commands. The standing rule in
`brain/tools/__init__.py` is: **do not reintroduce a general shell tool.** If you fork and add one,
you own that risk — and you should not run that fork anywhere reachable by an untrusted network.

**The `search` tool's page fetch cannot read your network.** `fetch` is restricted to public,
globally-routable hosts (loopback, LAN, link-local and tailnet addresses are refused) and never
follows redirects. Without this, a prompt-injected web page could tell the model to fetch
unauthenticated internal endpoints (Ollama, the brain's own inbox, media admin panels) and repeat
the contents back.

## Tokens & secrets

- All secrets live in `secrets/` (HA token, `*arr`/media creds, ingest token, service hosts).
  `secrets/` is **gitignored** — keep it that way; never commit real tokens.
- **Scope the Home Assistant token to least privilege.** The brain only needs the entities it
  actually controls. A full-access long-lived token means a prompt-injected or misused brain can
  touch everything in HA.
- The photo/records **ingest endpoint requires a token** (`X-Ingest-Token` header or `Authorization:
  Bearer`). Set a strong `INGEST_TOKEN`; without one the endpoint refuses requests. This is still
  not a substitute for keeping the service on a private network.
- The **NFC capture endpoints** (`GET /nfc`, `POST /nfc/log`) require a `token` query/form param
  matching `NFC_TOKEN`. This path deliberately bypasses the model entirely — see `brain/nfc.py` —
  so a scanned tag's write can't be silently swallowed the way a chat-agent tool call can be.

## Data

- The records database (`data/`) and learned memories (`memory/`) hold personal data (people,
  pets, your home). Both are **gitignored** and never leave your boxes except via your own backups
  (see `deploy/backup/OFFSITE-RUNBOOK.md` — the off-site copy is encrypted at rest).

## Threat model

The trust model above says what Hestia assumes. This says who it assumes it is up against, and
where that assumption gets thin. If you are running this at home, read it once and then go verify
your own perimeter, because the checks at the bottom are worth more than the prose.

### Who actually attacks a house

Not a person. A sweep. Nobody is going to study you individually. What happens is that a
vulnerability gets published and something automated tries it against every reachable host on the
internet within hours, at effectively zero marginal cost per victim.

This matters because it inverts the intuition that being an uninteresting target is protective. It
is not, and it has not been for some time. **Uninteresting is not a defense. Unreachable is.**

Agentic tooling does not hand attackers capabilities that defense cannot answer. What it does is
compress two timelines, and only two:

- **Disclosure to mass exploitation**, from weeks to hours. This makes patch latency, and more
  importantly the *number of things you are obliged to patch*, the dominant variable. Every service
  you expose is a subscription to somebody else's vulnerability disclosures.
- **Foothold to full compromise.** Reconnaissance and lateral movement after a first device falls
  is the step that automates best. Assume that anything reachable from a compromised device is
  enumerated quickly rather than eventually.

Design accordingly: fewer reachable things, and smaller blast radius when one of them fails.

### The assumption this design rests on

Hestia trusts the network boundary in place of authentication. That is stated plainly above and it
is a reasonable trade for a home system. But it has a consequence worth making explicit, because
it is usually discovered rather than chosen:

**A typical homelab is one flat trusted segment.** Services get published to all interfaces
because nothing on the LAN was ever hostile. Media apps, indexers, dashboards and admin panels
commonly ship with weak or absent authentication precisely because they assume this.

So the realistic bad day is not somebody defeating your firewall. It is something ordinary landing
on one device inside it, a phone, a TV, an appliance running firmware that stopped receiving
updates years ago, and finding a completely flat network behind it. Under the automated-pivot
timeline above, that is the scenario that costs you.

Two cheap mitigations, in order of value per effort:

1. **Put anything that will never be patched on an isolated segment.** A guest network is a
   perfectly good first approximation and most consumer routers already have one. Appliances lose
   nothing. They also stop being a bridge to your infrastructure.
2. **Disable UPnP on the router** unless you know something requires it. UPnP lets applications
   open ports through your firewall without telling you, and it is the most common reason that
   "nothing of mine is exposed" turns out to be false. BitTorrent clients are enthusiastic users
   of it.

### Detection, because prevention eventually fails

If you are going to build one detection control for a home network, build it on DNS.

Almost everything hostile resolves a name before it does anything useful. Malware reaching a
command channel, an exfiltration tool, a compromised appliance joining a botnet: all of it touches
DNS first, and in a Hestia-style deployment you already own that choke point. It is the highest
signal observation point available without putting an agent on every endpoint.

Two things make it usable, and both are easy to get wrong:

- **Clients must resolve against your filtering resolver directly.** If the router forwards DNS on
  their behalf, every query arrives with the router's address and per-device attribution is gone.
  You will be able to see that something is wrong and not which device is wrong, which is the only
  question that matters during an incident. Many consumer routers cannot hand out a different
  resolver by DHCP; setting it per device is a valid fallback.
- **Encrypted DNS bypasses you entirely.** A client using DNS-over-HTTPS to a third-party resolver
  is invisible to filtering and to logging, no matter what your blocklists say. Check for lookups
  of known DoH bootstrap hostnames, and decide deliberately whether to allow them.

Alerting on this belongs in a timer comparing rows, not in the model. See invariant 1 in
`CLAUDE.md`. A threshold being crossed is not a judgement call.

### Backups under a compromise

`deploy/backup/OFFSITE-RUNBOOK.md` describes a deliberate choice worth restating here as a
security property: the off-site copy is **pulled, not pushed**. The home machines hold no
credentials to the off-site repository, so an attacker who owns your house cannot delete your
off-site history. That is the right direction to defend, and it is the direction most home setups
get wrong.

The residual risk runs the other way. If the repository and its password live on the same host,
then whoever compromises that host can read and delete the backups, and encryption at rest does
not help because the key is sitting next to the data. This matters most when the off-site host
does other, more exposed work, which off-site hosts usually do, since that is why you already had
one.

Judge it by asking what a compromise of that single host costs you. If the answer is the entire
off-site leg, that is survivable when the home copies are independent, and it is not a reason for
alarm. It is a reason to know it. If you want the stronger property, the repository has to live
somewhere the backing-up host can write but not delete, which means append-only credentials
against object storage or a REST backend, not a local path.

### Verify rather than assume

Exposure is a question with a factual answer, and reasoning about your router's configuration is
not how you get it. Scan yourself from outside.

- **Scan from an external vantage point**, not from inside your own LAN. NAT hairpinning makes
  results from inside meaningless. A cheap VPS, or any box you own on a different network, works.
- **Include a control port** that nothing could possibly be listening on. If it reports open, your
  scan is being answered by something in the middle, DDoS mitigation or a captive portal or your
  ISP, and every other result in that run is worthless. This failure mode is easy to miss because
  the output looks like a catastrophe rather than an error.
- **Compare against ground truth on the host**: listening sockets and firewall rules. A service
  bound to all interfaces but blocked by a host firewall is not exposed today, and is one rule
  change away from being exposed. Bind it to loopback or a private address instead and let the
  firewall be the second layer rather than the only one.
- **Re-run it after any change to router or firewall configuration.** This is a measurement, not a
  one-time audit.

## Reporting a vulnerability

This is a personal/self-hosted project, not a hosted service. If you find a security issue, please
open a **private** GitHub security advisory on the repository rather than a public issue.
