# STATUS

Running log of where Hestia actually is. Newest entry at the top. Append, do not rewrite.

Host addresses, router details and LAN topology stay out of this file on purpose. This repo
is public. Those live in the operator's private notes.

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

### In flight

- The resolver is now a hard dependency for all household DNS with no fallback, by design,
  and it is **not** in the watchdog probes. Tonight's outage is precisely what that probe
  exists to catch, and it was found by hand instead.
- DoH bypass unaddressed.
- Threat coverage still rests on a single aggregated feed.

### Next concrete action

Add the resolver to the watchdog probes, alongside the existing brain `/health` check,
firing into the ntfy topic already in use.

Then, in order: a full-day query-volume comparison against the trailing baseline to settle
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
