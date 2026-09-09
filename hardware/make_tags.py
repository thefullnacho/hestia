"""Turn a list of watering positions into printable stakes and the URLs to write on them.

One position, one stake, one tag. The name is the stake's label, the tag's `subject`, and
the entity in records, so they cannot drift apart by being typed three times.

The URLs embed the NFC token, so the output file is a credential: written owner-only, never
printed. Reads NFC_TOKEN from the usual secret bundle.

    python hardware/make_tags.py --positions data/irrigation-positions.json \\
        --base-url https://<brain host> --stl-dir data/stakes --url-file data/stake-urls.txt
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "brain"))

SCAD = Path(__file__).resolve().parent / "nfc-stake.scad"
# Past this many characters a single embossed line drops under about 5mm and stops being
# readable standing up, so the name goes on two lines instead.
ONE_LINE_MAX = 10


def split_label(name: str) -> tuple[str, str]:
    """Break a long name at the space that leaves the two halves most even."""
    label = name.upper()
    if len(label) <= ONE_LINE_MAX or " " not in label:
        return label, ""
    # Ties go to the later space, so the first line carries more: "TREE OF / LIFE"
    # reads better than "TREE / OF LIFE".
    best = min((i for i, ch in enumerate(label) if ch == " "),
               key=lambda i: (max(i, len(label) - i - 1), -i))
    return label[:best], label[best + 1:]


def slug(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


def render(name: str, out: Path) -> None:
    line1, line2 = split_label(name)
    subprocess.run(
        ["openscad", "-o", str(out), "-D", 'part="stake"',
         "-D", f'name="{line1}"', "-D", f'name2="{line2}"', str(SCAD)],
        check=True, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--positions", required=True, type=Path)
    parser.add_argument("--base-url", required=True,
                        help="Where the brain answers, e.g. https://host:8730")
    parser.add_argument("--url-file", required=True, type=Path,
                        help="Owner-only output; holds the token, so keep it out of git")
    parser.add_argument("--stl-dir", type=Path, help="Render a stake per position here")
    args = parser.parse_args()

    import config
    config.load_secrets()
    token = os.environ.get("NFC_TOKEN", "")
    if not token:
        print("No NFC_TOKEN in the secret bundle. Nothing written.")
        return 1

    positions = json.loads(args.positions.read_text())
    lines = []
    for position in positions:
        name = position["name"].strip()
        query = {"token": token, "kind": "watering", "subject": name}
        if position.get("source"):
            query["source"] = position["source"]
        # Absent on purpose for anything not sprinkler-watered: no rate means the run logs
        # its minutes and claims no depth, which beats inventing inches for a hose at a
        # tree's base.
        if position.get("sprinkler"):
            query["sprinkler"] = position["sprinkler"]
        lines.append(f"{name}\t{args.base_url.rstrip('/')}/nfc?{urlencode(query)}")

    args.url_file.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(args.url_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w") as out:
        out.write("\n".join(lines) + "\n")

    rendered = 0
    if args.stl_dir:
        args.stl_dir.mkdir(parents=True, exist_ok=True)
        for position in positions:
            render(position["name"], args.stl_dir / f"{slug(position['name'])}.stl")
            rendered += 1

    # Deliberately no URL output: the token is in every one of them.
    print(json.dumps({"positions": len(positions), "urls_written": str(args.url_file),
                      "stls_rendered": rendered}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
