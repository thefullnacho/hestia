# The network guard

The brain, and everything else bound to a Hestia box's Tailscale address, has no login: the
tailnet is the access control (CLAUDE.md, invariant 3). Binding to that address is not quite
enough on its own. Linux answers for any of its addresses on any interface, so a device on the
LAN that adds a route for 100.64.0.0/10 through the box could reach the brain without being on
the tailnet. `hestia-guard.nft` closes that: traffic for the tailnet addresses is dropped unless
it came in through `tailscale0` or from the box itself (`tailscale serve` proxies over `lo`).

It is its own nftables table, replaced whole on each load, so it never touches Tailscale's
rules or Docker's, and loading it again changes nothing. One catch: a Docker container on the
same box reaching the brain at its tailnet address comes in through a bridge and is dropped.
Point such a container at the brain another way, or add its bridge to the accepted interfaces.

## Install (the operator's box; the pilot setup does this itself)

    sudo install -D -m 644 ~/hestia/deploy/firewall/hestia-guard.nft /etc/hestia/hestia-guard.nft
    sudo install -m 644 ~/hestia/deploy/firewall/hestia-guard.service /etc/systemd/system/
    sudo nft -c -f /etc/hestia/hestia-guard.nft      # checks the rules without loading them
    sudo systemctl daemon-reload && sudo systemctl enable --now hestia-guard

## Check it

    sudo nft list table inet hestia_guard            # the counters show anything it dropped
    curl -s http://<tailnet ip>:8730/health          # still answers, from the box and over Tailscale

To turn it off: `sudo systemctl disable --now hestia-guard` (removes the table).
