"""NFC capture: a deterministic, no-LLM logging path for garden harvests and asset service.

Exists because the chat agent can (and did — 2026-09-01) tell the user something was logged
when it never called the tool. A tag scan can't afford that: the whole point is "take the
action in the moment, because I will forget I did it otherwise," so there is no human
backstop to notice a silent no-op later. This module never touches the model — a scan hits a
plain form, the form POSTs straight to `records_store`, and the reply says exactly what was
written (or exactly what was wrong), synchronously, in the response.

A tag encodes a URL like `/nfc?token=...&kind=harvest&subject=Bed+2` (garden beds),
`/nfc?token=...&kind=service&subject=Furnace+Filter` (assets — resets the `due` clock, see
`records_store.due_assets`), or `/nfc?token=...&kind=use&subject=Weedwhacker` (assets whose
maintenance isn't calendar-based — just a running log of minutes run, no due date implied), or
`/nfc?token=...&kind=watering&subject=Back+Fence&source=zone3&sprinkler=hi-rise` (a stake in the
ground where a hose-fed sprinkler gets set down).
`subject` is the bed/asset/place name; GET renders the capture form with it locked in, POST
/nfc/log does the write and renders the confirmation.

A watering tag carries its own `source` and `sprinkler` because the stake never moves and the
answers never change, which leaves one prefilled field between a scan and a logged run. That
matters more here than anywhere else: watering is the job most likely to be done with wet hands,
in a hurry, before coffee.
"""
from __future__ import annotations

import datetime as dt
import html

import records_store as store

UNITS = ["lb", "oz", "kg", "g", "each", "pint", "quart", "basket"]

# The standing cycle length. Prefilled so the common case is scan, glance, tap.
DEFAULT_WATERING_MINUTES = 15


def _page(body: str, title: str = "Hestia") -> str:
    # One shared shell: big tap targets and large text, meant to be read at arm's length
    # outdoors or read once and dismissed, not a UI anyone lingers in.
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #14171a; color: #eee;
         margin: 0; padding: 24px 20px 60px; }}
  h1 {{ font-size: 1.3rem; margin: 0 0 4px; }}
  .subject {{ color: #9ad; font-size: 1.1rem; margin-bottom: 20px; }}
  label {{ display: block; margin: 18px 0 6px; font-size: 1rem; color: #ccc; }}
  input, select {{ width: 100%; box-sizing: border-box; font-size: 1.4rem; padding: 14px;
                   border-radius: 10px; border: 1px solid #444; background: #1e2226; color: #fff; }}
  button {{ margin-top: 28px; width: 100%; font-size: 1.3rem; padding: 16px; border-radius: 10px;
           border: none; background: #3a7; color: #04120a; font-weight: 600; }}
  .big {{ font-size: 2.4rem; margin: 20px 0 8px; }}
  .warn {{ background: #5a3a10; color: #ffd699; padding: 14px; border-radius: 10px; margin-top: 18px; }}
  .err {{ background: #5a1010; color: #ffb3b3; padding: 14px; border-radius: 10px; }}
  .meta {{ color: #999; font-size: 0.95rem; margin-top: 6px; }}
</style></head>
<body>{body}</body></html>"""


def error_page(msg: str, status_hint: str = "") -> str:
    return _page(f'<div class="err"><strong>{status_hint or "Error"}</strong><br>{msg}</div>')


def bad_token_page() -> str:
    return error_page("Missing or bad token. Re-scan the tag, or check secrets/nfc.env.", "401")


def capture_form(kind: str, subject: str, token: str,
                 source: str = "", sprinkler: str = "") -> str:
    if kind == "harvest":
        fields = f"""
        <label for="crop">Crop</label>
        <input id="crop" name="crop" type="text" autofocus required autocomplete="off">
        <label for="qty">Amount</label>
        <input id="qty" name="qty" type="number" step="any" inputmode="decimal" required>
        <label for="unit">Unit</label>
        <select id="unit" name="unit">{"".join(f'<option value="{u}">{u}</option>' for u in UNITS)}</select>
        """
        button = "Log harvest"
    elif kind == "service":
        fields = """
        <label for="note">Note (optional)</label>
        <input id="note" name="note" type="text" placeholder="serviced" autocomplete="off">
        """
        button = "Log service"
    elif kind == "use":
        fields = """
        <label for="minutes">Minutes run</label>
        <input id="minutes" name="minutes" type="number" step="any" inputmode="decimal" autofocus required>
        <label for="note">Note (optional)</label>
        <input id="note" name="note" type="text" placeholder="e.g. front + back yard" autocomplete="off">
        """
        button = "Log run"
    elif kind == "watering":
        if sprinkler and sprinkler.lower() not in store.SPRINKLERS:
            return error_page(f"Tag names an unknown sprinkler '{html.escape(sprinkler)}'.", "400")
        # A tag that already knows its sprinkler asks nothing about it; one that doesn't
        # offers the choice rather than silently assuming a rate that was never applied.
        if sprinkler:
            picker = f'<input type="hidden" name="sprinkler" value="{html.escape(sprinkler)}">'
        else:
            options = "".join(
                f'<option value="{html.escape(k)}">{html.escape(v.get("label") or k)}</option>'
                for k, v in store.SPRINKLERS.items())
            picker = f"""
        <label for="sprinkler">Sprinkler</label>
        <select id="sprinkler" name="sprinkler">
          <option value="">None (drip or soaker)</option>{options}</select>"""
        fields = f"""
        <label for="minutes">Minutes</label>
        <input id="minutes" name="minutes" type="number" step="any" inputmode="decimal"
               value="{DEFAULT_WATERING_MINUTES}" autofocus required>{picker}
        <input type="hidden" name="source" value="{html.escape(source)}">
        """
        button = "Log watering"
    else:
        return error_page(f"Unknown kind '{html.escape(kind)}' — tag should encode "
                          "kind=harvest, kind=service, kind=use, or kind=watering.", "400")

    safe_subject = html.escape(subject)
    heading = {"harvest": "Harvest", "watering": "Watering"}.get(kind, "Service")
    return _page(f"""
    <h1>{heading}</h1>
    <div class="subject">{safe_subject}</div>
    <form method="post" action="/nfc/log">
      <input type="hidden" name="token" value="{html.escape(token)}">
      <input type="hidden" name="kind" value="{html.escape(kind)}">
      <input type="hidden" name="subject" value="{safe_subject}">
      {fields}
      <button type="submit">{button}</button>
    </form>
    """)


def _confirm(headline: str, detail: str, created: bool, subject: str) -> str:
    now = dt.datetime.now().strftime("%b %-d, %Y %-I:%M %p")
    safe_subject = html.escape(subject)
    warn = (f'<div class="warn">⚠ \'{safe_subject}\' wasn\'t a known entity — created it new. '
            f"If that's a mishear, fix it in the records DB.</div>" if created else "")
    return _page(f"""
    <div class="big">✅</div>
    <div><strong>{html.escape(headline)}</strong></div>
    <div class="meta">{html.escape(detail)}</div>
    <div class="meta">{now}</div>
    {warn}
    """)


def log_harvest_tag(subject: str, crop: str, qty: str, unit: str) -> tuple[str, int]:
    """Write the harvest and render the confirmation. Returns (html, status)."""
    crop = (crop or "").strip()
    if not crop:
        return error_page("Crop is required.", "400"), 400
    try:
        q = float(qty)
    except (TypeError, ValueError):
        return error_page("Amount must be a number.", "400"), 400
    if not q > 0:
        return error_page("Amount must be a positive number.", "400"), 400

    r = store.log_harvest(subject, crop, q, unit=unit)
    cls, canon = store.normalize_unit(unit)
    amount = f"{q:g}" + (f" {canon}" if cls != "count" else "")
    return _confirm(f"Logged {amount} of {crop}", subject, r.get("created", False), subject), 200


def log_service_tag(subject: str, note: str) -> tuple[str, int]:
    """Write the service event and render the confirmation. Returns (html, status)."""
    detail = (note or "").strip() or "serviced"
    r = store.log_event("service", subject=subject, action="serviced", detail=detail,
                        subject_kind="asset", strict_subject=True)
    return _confirm("Logged service", f"{subject} — {detail}", r.get("created", False), subject), 200


def log_watering_tag(subject: str, minutes: str, source: str = "",
                     sprinkler: str = "") -> tuple[str, int]:
    """Write a watering run for a place and render the confirmation. The derived depth and
    volume are shown back so an estimate is visibly an estimate at the moment it is made,
    not a number discovered later in a report. Returns (html, status)."""
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return error_page("Minutes must be a number.", "400"), 400
    if not m > 0:
        return error_page("Minutes must be a positive number.", "400"), 400
    sprinkler = (sprinkler or "").strip().lower()
    if sprinkler and sprinkler not in store.SPRINKLERS:
        return error_page(f"Unknown sprinkler '{html.escape(sprinkler)}'.", "400"), 400
    seconds = int(round(m * 60))
    inches, gallons = store.water_applied(seconds, sprinkler)
    r = store.log_watering(subject, seconds, source=(source or "").strip()[:32] or None,
                           sprinkler=sprinkler or None)
    detail = subject if inches is None else f"{subject} — {inches:g} in, {gallons:g} gal (estimated)"
    return _confirm(f"Logged {m:g} min watering", detail, r.get("created", False), subject), 200


def log_use_tag(subject: str, minutes: str, note: str) -> tuple[str, int]:
    """Write a runtime/usage event (no due-date implied — just a log for averages later,
    e.g. the weedwhacker: no service interval, just 'how long did this run'). Returns (html, status)."""
    try:
        m = float(minutes)
    except (TypeError, ValueError):
        return error_page("Minutes must be a number.", "400"), 400
    if not m > 0:
        return error_page("Minutes must be a positive number.", "400"), 400
    note = (note or "").strip()
    detail = f"{m:g} min" + (f" — {note}" if note else "")
    r = store.log_event("use", subject=subject, action="ran", detail=detail,
                        subject_kind="asset", strict_subject=True, attrs={"minutes": m})
    return _confirm(f"Logged {m:g} min run", subject, r.get("created", False), subject), 200
