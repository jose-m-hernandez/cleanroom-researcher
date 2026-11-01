You compare lines added to a project notes file against an inventory. Reply only with JSON that matches the schema.

Case id: {case_id}

Needed items (ID: fact, with the heading where it belongs):
{needed}

Not-needed items (ID: fact):
{not_needed}

Added lines, numbered, each shown with the heading it was added under:
{lines}

For every added line, in order, give:
- "line": its number.
- "kind": "needed" if it records a needed item, "not_needed" if it records a not-needed item, "unplanted" if it matches neither.
- "item_id": the matching item ID, or "" for unplanted.
- "placed_ok": for a needed line, true when the heading's topic fits the item, otherwise false; false for the other kinds.
- "quote": at most one short span copied verbatim from the line ("" if none).

Do not explain.
