# ADR-0046: Persisted node-canvas dataset authoring

- Status: Accepted; node model superseded by [ADR-0050](0050-table-and-link-dataset-board.md)
- Date: 2026-10-18

## Context

The guided visual editor is efficient for a small join tree, but it does not communicate data flow spatially. Analysts also need an n8n-like desktop surface where tables, joins, projection, presentation, and output can be understood and rearranged as a graph. This editor must not introduce a second executable dataset format or make browser state authoritative. Existing visual definitions and external API clients must remain compatible.

Reliable node dragging, transformed coordinates, keyboard selection, pan/zoom, minimap navigation, and edge hit testing are specialized interaction behavior. Reimplementing them with ad-hoc DOM and SVG logic would create a large accessibility and browser-compatibility surface unrelated to DataPipette's analytical domain.

## Decision

- Canvas Builder is a third authoring experience beside Guided Visual and Advanced SQL. It edits the same schema-version-2 visual definition used by the guided editor.
- Relations, joins, fields, and display settings remain the only executable semantics. Canvas nodes and edges are derived from those structures; generated SQL never reads canvas metadata.
- Add optional `canvas` authoring metadata to schema-version-2 visual definitions. It contains at most 24 stable node positions plus one bounded viewport (`x`, `y`, `zoom`). Existing documents may omit it and receive a deterministic layout when Canvas Builder is first opened.
- Persist layout and viewport through the existing create/replace-draft JSON contract. No metadata migration is required because dataset definitions already use versioned JSONB.
- Derive stable node identifiers from relation and join UUIDs plus fixed projection, presentation, and output identifiers. Deleting a join removes its right-hand relation subtree and dependent fields so the accepted connected acyclic join-tree invariant is never bypassed.
- Support both explicit foreign-key suggestions and manual single-condition joins. The client offers only broad compatible PostgreSQL type families; authoritative catalog existence, authorization, structure, and planner validation remain server-side.
- Use the MIT-licensed `@xyflow/react` runtime for the desktop graph surface. Load it only when Canvas Builder is opened, keep its CSS in the async chunk, and enforce a dedicated 70 KiB gzip budget across the runtime and editor chunks.
- Canvas Builder is unavailable below the 1050 px desktop breakpoint. Guided Visual remains the complete narrow-screen editor and saved canvas metadata is left unchanged.
- Undo/redo is bounded in-memory editing history. Only the current definition is durable after the normal draft save.

## Consequences

- Guided and canvas editing can be mixed without conversion or loss of stable field/relation/join identities.
- External clients see a backward-compatible optional authoring object and may ignore it.
- Moving or zooming the canvas makes the draft dirty, so the existing explicit save and ETag conflict behavior protects concurrent edits.
- The React entry budget increases from 80 to 86 KiB gzip because React Flow exercises additional shared React DOM behavior; the graph runtime itself stays outside initial navigation and is measured separately.
- Manual type-family compatibility is guidance, not proof. Server validation remains required before preview or publication.
- Mobile and tablet users retain complete guided authoring but cannot manipulate a node graph.

## Alternatives considered

- **Store the graph only in browser storage:** rejected because layout would not follow the draft across sessions, browsers, or users with edit access.
- **Create a second canvas dataset kind:** rejected because two executable formats would drift and complicate validation, publication, and public API integrations.
- **Persist generated graph edges as executable semantics:** rejected because relations and joins already provide the reviewed safe contract.
- **Build a custom SVG graph engine:** rejected because accessible transforms, selection, minimap, and edge interaction are mature infrastructure rather than product differentiation.
- **Enable canvas editing on phones:** rejected because the settings panel and spatial graph would compromise both reading and touch manipulation; Guided Visual is the supported narrow-screen workflow.
