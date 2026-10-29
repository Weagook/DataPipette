# ADR-0050: Table-and-link dataset board

- Status: Accepted
- Date: 2026-10-29

## Context

ADR-0046 introduced a node canvas that drew the dataset as a processing pipeline: table nodes, join nodes, and fixed Select Columns, Rename & Format, and Dataset Output nodes, configured through a side inspector. Users who are not data engineers found it hard to read: columns were hidden behind inspector panels, links were configured with drop-down lists rather than drawn, and the canvas shared space with technical panels. Creating and loading a dataset also took four separate steps (save, validate, preview or impact, publish).

The product goal is that a non-technical user can assemble a dataset with the mouse and understand what each action does.

## Decision

- Replace the pipeline nodes with one card per table. Each card lists its columns with checkboxes and plain type badges; ticking a column adds an ordinary visual field.
- Tables come from a searchable list beside the board, by drag-and-drop or an add button. The first table is the main table. A new table is linked automatically when a visible foreign key connects it to a table already in the dataset; otherwise it is shown as an unlinked, dashed card that is not part of the saved definition until the user draws a line from one of its columns to a compatible column of a linked table.
- A line between two directly linked tables adds another equality condition to their join. A line that would introduce a second path between tables is refused with an explanation, preserving the accepted rooted acyclic join tree. Incompatible column type families are refused before the server is asked.
- Clicking a line explains the join in plain words: “all rows from the first table” (`LEFT JOIN`) or “only rows that match” (`INNER JOIN`). Unlinking a table, or deleting its last matching column pair, removes it and every table linked through it from the definition, but keeps those tables on the board as unlinked cards so work is not lost.
- Result-column labels, formats, and visibility are edited in a strip below the board; stable output names are not exposed there.
- Canvas metadata stores only table positions (`relation:<uuid>`) and the viewport. Saved metadata from the pipeline canvas is still accepted; its extra node positions are ignored and dropped on the next save.
- The board is the default editor on desktop-width screens for new and existing visual datasets. The step-by-step editor and SQL remain available.
- The existing-dataset command bar gains **Save and load data**, which runs the existing public operations in order: save the draft, validate, analyze impact when a snapshot is already published, and request the refresh. It stops and opens the relevant tab on validation findings or blocking chart dependencies. No new API or backend rule is introduced.

## Consequences

- The visual definition contract, server validation, authorization, and publication rules are unchanged; external API clients are unaffected.
- Unlinked cards live only in the browser session and are not saved. This is intentional because the definition cannot represent a disconnected table.
- Joined tables now start with no selected columns, so adding a lookup table does not flood the result with columns the user did not ask for.
- The one-click flow is client-side orchestration of public operations, so other clients can reproduce it. Blocking chart dependencies remain advisory in the server's refresh request, as before.
- The canvas runtime and editor stay in the lazily loaded chunk within the existing 70 KiB gzip budget.

## Alternatives considered

- **Keep the pipeline canvas and simplify its wording:** rejected because the main difficulty was the indirection of configuring joins and columns in separate nodes and panels.
- **A wizard without a board:** rejected by the product owner in favour of direct manipulation; the step-by-step editor still covers narrow screens.
- **Persist unlinked tables in the definition:** rejected because it would require a disconnected-relation concept that the safe SQL compiler and validation do not accept.
