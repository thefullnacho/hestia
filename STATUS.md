# STATUS

Running log of where Hestia actually is. Newest entry at the top. Append, do not rewrite.

Host addresses, router details and LAN topology stay out of this file on purpose. This repo
is public. Those live in the operator's private notes.

---

## 2026-08-17 — Tool-layer audit: eleven fixes, and an alert layer that had never once worked

A full audit of `brain/tools/` against the design invariants, then every finding fixed
test-first. Seventeen commits, and the suite went from 148 tests to 200. Work was split
across two agents on file-disjoint sets and merged without a conflict.

**Invariant 1 held cleanly.** Nothing in the tool layer hands scheduling, counting,
thresholding or date math to the model. Reminder parsing, shopping splits, harvest totals,
release selection and weather thresholds are all computed in code. The thesis is intact.

**The headline: NWS alerts have been dead since the day they were written.**
`api.weather.gov` accepts at most four decimal places and 301s anything longer. The
configured lat/lon carries seven, and httpx raises on an unfollowed redirect, so the call
failed every single time. The old code swallowed that and returned an empty list, which
rendered as a confident "No active National Weather Service alerts", in the tool and in the
7:10 briefing alike. The frost and freeze warning layer, the one safety-relevant readout in
the whole tool, had been reporting all-clear while never reaching the service. It only
became visible because the same session made outages announce themselves. Coordinates are
now rounded and both weather.gov calls follow redirects; verified end to end against the
live service, resolving zone CTZ012.

**The other fixes, by shape.**

*Failures that reported success.* Alerts as above. The `home` tool blamed a whole action
when only its confirmation read-back flaked, so the model would apologise for a light that
had in fact changed and might toggle it back. `media` hid a dead Lidarr behind a bare
`except: pass` and just omitted music from the download list.

*Failures that took down more than themselves.* `dispatch` caught only `TypeError`, so any
store or filesystem error from `memory`, `recipe` or `reminder` escaped as a bare HTTP 500
with no answer at all. One `[N/A]` column from `nvidia-smi` raised out of `snapshot()` and
cost the entire health readout plus a 500 on `/status`. An Ollama restart 500'd the client
because only `asyncio.TimeoutError` was guarded. All three now degrade to an answer.

*A real security hole.* The `search` tool's fetch took any model-supplied URL with
redirects followed and no host restriction, on a box that runs unauthenticated internal
services by design. An injected page could have steered the model into reading loopback,
LAN or tailnet endpoints and speaking the contents back. Fetch now resolves every address a
host maps to and refuses anything not globally routable, including Tailscale's `100.64/10`,
which Python's `is_global` would otherwise pass. Redirects are refused outright.

*Wrong answers.* "Tonight at 9" fired at 9 the next morning, a twelve-hour miss delivered
with a cheerful confirmation. Bare hours after "tonight" now read as PM across 4 to 11, with
an explicit am/pm always winning. ISO input with `Z` or an offset was stored in the wrong
timezone and then string-compared against naive rows; everything is naive local now.

*Unvalidated model input.* `records` accepted any `qty`, so a negative harvest could poison
permanent season totals, and silently dropped malformed `attrs` rather than refusing.
`shopping` filed "milk, milk" twice.

**Deploy correctness.** The tracked brain unit shipped `--host 127.0.0.1` beneath a comment
describing a Tailscale bind. Deployed verbatim it starts cleanly and cuts off the phone, HA,
hl-relay and the Voice PE, the same failure family as the placeholder backup host and as the
two `hestiactl` defects recorded on 2026-08-16. It now carries a placeholder that fails
loudly until substituted. README separately claimed the brain binds `0.0.0.0`, contradicting
invariant 3 and SECURITY.md in the same public repo.

**Invariant 5 got an honest exception.** The weather tool has always called Open-Meteo and
api.weather.gov while the README claimed the brain never phones home. Both are keyless and
take only a lat/lon, and a forecast cannot be computed locally. That is now stated in
CLAUDE.md and README rather than quietly contradicted. `CLAUDE.md` is also tracked for the
first time, so the invariants survive a clone.

**One audit finding was wrong and is recorded as such.** The claim that `limit=-1` would
dump the whole event log was false: `records_store.py` already clamps to `[1, 200]`, so the
worst case was 200 rows. The `qty` and `attrs` halves of that finding were real.

**In flight.**

- The work sits on `fix/tool-layer-hardening`, merged to main and pushed as part of this
  entry. The brain has been restarted onto it and verified.
- `[non-production]` Now that `CLAUDE.md` is tracked on a public repo, it names the private
  docs by filename. Contents are not exposed, only the names. Worth a decision.

**Next concrete action.** Nothing here is load-bearing. The nearest useful follow-on is the
class of bug this session kept finding: a component that fails silently and reports success.
The watchdog already covers the brain being down, but nothing covers a subsystem that is up
and lying, which is exactly what the alert layer did for months.

---

## 2026-08-16 — Plex "library won't load" traced to dead remote access, not DNS

Reported symptom: the TV would show the server but never populate the library. Suspicion was
split between Plex and household DNS. It was neither.

**The server was healthy throughout.** `/identity` answered in about a millisecond, the library
mounts were all bound read-write and populated, and the scanner was actively generating chapter
thumbnails while the TV was failing. The direct LAN path was verified end to end, including a TLS
connection over the exact secure hostname a client is supposed to use, which returned 200.

**Root cause: remote access has been dead since 2026-07-29.** Plex self-tests its own public
address hourly and had failed 828 consecutive times, refusing immediately rather than timing out.
The server was relying on an automatic UPnP port mapping that no longer exists, which is
consistent with the fully-closed WAN verified in the 2026-08-11 exposure audit. The server keeps
publishing that dead endpoint as a valid way in, so any client that reaches for it instead of the
direct LAN route hangs and never loads the library.

Deliberately **not** reopening the port. Invariant 5 and the closed-WAN posture win over a client
sitting on the same LAN as the server. The fix belongs on the client side, plus turning off remote
access so the dead endpoint stops being advertised at all.

**Ruled out, recorded so none of it gets re-hunted.**

1. *AdGuard is not blocking Plex.* The apex `plex.direct` record returns a null address from
   Cloudflare and Google identically. That is Plex publishing it, not a filter rule. No blocklist
   on the resolver contains the domain. Only a Plex telemetry host is genuinely blocked, which is
   intentional and harmless.
2. *DNS rebinding protection is not in play.* The per-server secure hostname, which encodes a
   private address, resolves correctly through the household resolver, the router, and public
   resolvers alike.
3. *The GDM discovery errors in the Plex log are stale*, last seen 2026-07-29, zero in the
   trailing two hours. The server is on host networking with the discovery ports listening.

**Two side findings, both unresolved.**

- *The relay host does not use its own DNS filter.* Its resolver is Tailscale MagicDNS, so the box
  hosting AdGuard resolves around it. Plex logged 14 transient failures to resolve a public
  hostname between 2026-08-01 and 2026-08-16, roughly one every day or two. Nothing watches this.
- *The VPN sidecar is marked unhealthy* on its own internal resolver timing out, while the tunnel
  itself is intact and the kill-switch verified (egress address still differs from the WAN
  address). Torrent traffic is unaffected in practice, which is why it went unnoticed.

**Two defects in `deploy/hestiactl`, found and not yet fixed.**

1. The remote target falls back to a **placeholder** SSH user, so every remote subcommand fails
   until the operator already knows the real one. Same failure family as the scrub-broke-backups
   incident: a placeholder shipped as a default.
2. The brain URL defaults to loopback while the brain correctly binds its private address per
   invariant 3, so `hestiactl status` reports the brain unreachable **permanently** while it is up
   and serving every tool. A status board that cries wolf every single time is worse than none,
   because the one real outage gets skimmed past.

**Next concrete action:** confirm which subnet the TV is on `[non-production]`, already queued.
Then turn off Plex remote access so the dead endpoint stops being advertised, and fix the two
`hestiactl` defaults.

---

## 2026-08-11 — DNS layer audit and remediation

Triggered by a question about AdGuard blocking traffic from a phone app. The answer was
boring, the things found underneath it were not.

**The original question, answered.** The blocked domains were analytics, log upload, event
collection and device-fingerprinting endpoints. Every block in the trailing 19 days was a
blocklist rule match, none were threat detections. The app's functional endpoints resolved
normally throughout, and the blocked hosts were re-queried at a low steady rate rather than
in retry storms, which is what a genuinely broken dependency looks like. No action needed.

**Three real gaps found in the DNS layer.**

1. *Blocks cannot mean "malware" on this deployment.* SafeBrowsing and parental filtering
   are both off, so every block is a list match by construction. Deliberately not enabling
   SafeBrowsing: it sends a hash prefix of every domain to a third party, which breaks
   invariant 5. The gap should close with local lists instead.
2. *No per-device attribution.* The router forwards DNS on behalf of clients, so every
   query in the log carries the router's address. Questions of the form "which device did
   this" are unanswerable as configured. The router's firmware exposes no LAN DHCP DNS
   option, so the only routes are per-device manual DNS or moving DHCP to the resolver.
3. *DNS-over-HTTPS bypass is open.* A client is actively bootstrapping encrypted DNS to a
   third-party resolver, 850 lookups in the window. Any client on DoH is invisible to
   filtering entirely. Unresolved.

**Incident, caused and fixed in-session.** Pointing the router's secondary resolver at
AdGuard unmasked a latent reverse-DNS loop: private PTR resolution was configured to use the
host's system resolver, which is the router, which now forwarded back to AdGuard. Each
lookup hung for two seconds and drained the worker pool until real queries timed out. The
external secondary had been the accidental escape hatch. Compounding it, the per-client rate
limit was being applied to the whole household, because the household presents as one
client. Both settings corrected via the AdGuard UI, resolution and filtering verified.

Lesson recorded: read a service's own upstream and PTR configuration before changing what
points at it.

**Correction.** The change was recommended on the grounds that the external secondary was
leaking a share of household browsing unfiltered. That was asserted from general resolver
behavior and never verified. Measured afterwards, steady-state query rate returned to its
prior baseline rather than stepping up, which is not what a real leak produces. The claim is
withdrawn. Pointing both entries at the local resolver is still correct, but for the smaller
reason of removing ambiguity. Note the vantage problem: the resolver cannot observe queries
that never reach it, so this remains weakly evidenced in both directions.

### Done same night: the watchdog gap

The off-site watchdog now runs three independently-tracked probes instead of one, on the
existing 5 minute timer and the existing ntfy channel:

| Probe | Catches | Priority |
|---|---|---|
| `brain` | house dark, as before | urgent |
| `dns` | resolver up but not resolving, the exact failure from tonight | urgent |
| `dns-filtering` | resolution fine, blocklists silently not applied | high |

Each keeps its own state, so one cannot mask another. The filtering check is skipped when
resolution is down, because a dead resolver answers nothing for everything and would
otherwise read as a false pass. Both failure and recovery transitions were fired against a
throwaway ntfy topic and a scratch state directory before being trusted, so this is a tested
alert rather than a written one.

Also documented: `SECURITY.md` gained a threat model, and external exposure was measured
rather than assumed. The home perimeter is fully closed; the off-site host exposes only the
ports its firewall intends.

### The root blocker, found last

Attempted to route tailnet DNS at the resolver to finally get per-device attribution. It
does not work, and the reason explains an earlier dead end too.

The resolver runs on a Docker **bridge** network. Queries arriving over the tailnet are
source-NAT'd to the bridge gateway, so every tailnet device logs as one indistinguishable
client. Proven by timing: the off-site watchdog fired at 00:32:56 UTC from its own tailnet
address, and the resolver recorded that exact query pair as the gateway address. LAN queries
are unaffected, because they arrive through published-port DNAT and keep their source.

This is the same reason the resolver cannot act as a DHCP server. One change fixes both:
host or macvlan networking, which first requires dealing with the host's stub resolver on
port 53. That is the unlock, and it is a daylight job.

What still works today: setting a LAN device's resolver manually. One device on the network
has been doing this all evening and shows up correctly attributed, which is the proof.

Two methodology notes, both of which cost real time tonight:

- The on-disk query log buffers in memory and can sit byte-identical for an hour at low query
  rates, while the web UI shows current data. Do not diagnose a stuck logger from the file.
  Nearly restarted a healthy container over this.
- `tailscale set --accept-dns=false` also disables MagicDNS. Applying it to the off-site box
  silently broke hostname resolution for the nightly backup pull, which was compensated with
  a static hosts entry. That entry is the better arrangement anyway, since it survives DNS
  and coordination-server problems entirely.

### In flight

- DoH bypass unaddressed. A client is using encrypted DNS to a third-party resolver, which
  no amount of local filtering or logging can see.
- Threat coverage still rests on a single aggregated feed.
- Per-device DNS attribution still missing, so the new probes can say the house is broken
  but not which device is misbehaving.
- On the off-site host, the panel database listens on all interfaces and is not exposed only
  because the firewall says so. It should bind to loopback.

### Next concrete action

Move the resolver to host networking. It is the single change that unlocks per-device
attribution and the DHCP option together, and everything else on this list is worth less
until it lands. Deal with the host stub resolver on port 53 first, recreate the container,
verify resolution and filtering before walking away.

Then: a full-day query-volume comparison against the trailing baseline to settle
the leak question properly `[non-production]`; local phishing and malware-distribution
lists, added one at a time so false positives are attributable; and `dns-watch`, a
timer-driven deterministic feed into the existing ntfy channel and `snapshot()`. `dns-watch`
is worth more after per-device DNS lands, so it is sequenced last. Detection stays in
timers and row comparisons, never the model.

### Non-production, queued

- `[non-production]` Per-device DNS on the machines worth attributing. Router cannot hand
  it out.
- `[non-production]` Passwordless sudo across the boxes. Scoped NOPASSWD for container and
  service control is the lighter option.
