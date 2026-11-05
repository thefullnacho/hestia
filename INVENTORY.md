# Inventory

What the house has, how much, how old, and where it was last put. Built 2026-11-05
(`brain/inventory_store.py`, the `inventory` tool). No model in any of the arithmetic: the model
relays what the person said, and code reads the amount, keeps the count and judges the age.

## The shape

- **Items** are records entities of kind `thing` (the roster's existing rows), matched by name,
  alias or the words of the name ("neem" finds "Neem oil"). Using something never recorded does
  not create it.
- **Every change is an event** (kind `stock`): bought, used, used up, counted. The count on hand
  is replayed from those events, so nothing is overwritten and the history explains every number.
- **Purchase date** comes from the person or, later, a receipt. An item added by hand is dated the
  day it was added and marked as an estimate, and reads back as "added", not "bought".
- **Location is "last noted", never "is"**, and never required: kept with the date it was noted
  and read back as "last noted in the shed on Nov 5". Things move and nobody updates a system
  for that, so it is a hint, updated only when it comes up ("the neem oil is in the garage now").

## Amounts as people say them

| Said | Kept as | Read back |
|---|---|---|
| "2 bags", "1.5 lb", "1/2 gallon" | that number and unit | "2 bags" |
| "a couple", "a few", "a handful", "half a dozen" | 2, 3, 5, 6, marked approximate | "about 3" |
| "some", "a bit", "some in a bag", "plenty" | no number, the words as said | "some (no count given)" |

A vague amount is a real fact (the house has it) and is never turned into a number. Hestia does
not ask for a better amount when something is logged: logging has to stay one breath. It asks
only when an answer depends on the amount ("do we have enough mulch for three beds?" against a
record that says "some").

Uses come off the oldest purchase first. A use with no count, or in a unit that can't be
subtracted (a pound from bags), makes the count fuzzy and says so instead of guessing.

## Old stock

A table in code (`SHELF_LIFE`) puts an item in a category by its name (seeds, medicine, garden
chemical, fertilizer, batteries, canned food, pantry) with the years it usually keeps. The warning
looks at the oldest purchase still on hand: seeds bought in 2023 and again in 2026 warn about the
2023 packets until those are used. An item in no category gets no warning, never a guessed one.

## Receipts

Built 2026-11-05 (`brain/receipts.py`, `brain/receipts_api.py`). The phone reads a receipt with
Apple's on-device text recognition and sends only its lines (PHONE-APP.md, "Receipts"). Code
reads them, no model:

- Item lines are the ones that end in a price; totals, tax, payment, cashier and thank-you lines
  are skipped. A count or weight on the line below ("3 @ 3.49", "2.31 lb @ 0.54/lb") belongs to
  the item above, whether or not that line had its own price. Product codes and tax flags come
  off the name; sizes read plainly ("16 oz"). The same item rung up twice is one proposal.
- The receipt's own date dates the purchase (not an estimate); the store is the first header line.
- Each item waits on the Today page: Keep, Edit (name and amount), Not this, and Keep all for a
  long receipt. Nothing reaches the inventory until a person keeps it. Keep adds to the item on
  record whose words are all in the line ("NEEM OIL 16OZ" adds to "Neem oil"); the card says
  which, or "new item".
- An abbreviation the store printed ("BNLS CHKN BRST") stays as printed for the person to fix.
  A model could expand it, but a wrong guess would be filed as a fact, so v1 leaves it to Edit.

## Not yet

- **The grounded answer:** library passages joined with what is on hand ("how do I treat pests
  on garlic" answered from the extension leaflets and the neem oil in the garage).
