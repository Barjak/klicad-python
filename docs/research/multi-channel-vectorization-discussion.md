# Multi-channel vectorization — verbatim discussion

The five conversational exchanges that surfaced the
intent-vs-implementation drift on the multi-channel ("vectorization")
feature.  Preserved verbatim so future auditors don't have to
re-derive the analysis.

The implementation work all landed on `loop/integration-7` in both
`~/projects/KliCAD` and `~/projects/klicad-python`.  Key commits the
discussion references: `bf422c17bc` (R3.3 per-slot bit-member binding),
`f5e10b6` (R5.6+R5.7 bus-shaped sheet pin + bare bit-member body
labels), `4bc7548`/`961a619` (A.11 live test).

---

## Exchange 1 — User asks for a demo

> User: **Okay now show me an example of the vectorization feature.**

> Assistant (abridged — the relevant text-only conclusion after the
> R-only demo emitted cleanly):
>
> Here's the demo with the correct readout:
>
> **Python (5 lines)**:
> ```python
> ch = Circuit("ch", ports=["IN[0..3]", "OUT[0..3]"])
> for k in range(4):
>     ch.add(R(f"R{k+1}", f"IN[{k}]", f"OUT[{k}]", value="10k"))
>
> top = Circuit("top")
> top.add(ch.instance("U_CH", repeat=4, IN="INBUS[0..3]", OUT="OUTBUS[0..3]"))
> ```
>
> **Emitted**: 1 top sheet (1.7KB) + 1 body sheet (11KB) on disk.  Body
> has 4 SCH_SYMBOLs.  Top sheet has ONE sub-sheet block with
> `(repeat_count 4)` + 3 stable `repeat_instances` UUIDs.
>
> **Hierarchy after `BuildSheetList`**: 4 paths (1 on-canvas template
> + 3 synthetic clones sharing the template's screen).
>
> **Netlist**: 16 annotated component instances (R1..R16).  Per-slot
> bit binding:
>
> | Parent bus bit | Annotated ref |
> |---|---|
> | `/INBUS0` ↔ `/OUTBUS0` | R1 (slot 0's bit-0 part) |
> | `/INBUS1` ↔ `/OUTBUS1` | R6 (slot 1's bit-1 part) |
> | `/INBUS2` ↔ `/OUTBUS2` | R11 (slot 2's bit-2 part) |
> | `/INBUS3` ↔ `/OUTBUS3` | R16 (slot 3's bit-3 part) |
>
> The R3.3 fan-out picks each slot's bit-K body part for the bit-K bus
> binding; the other 3 body parts in each slot remain on slot-local
> nets (R2,R3,R4 in slot 0; R7,R8,R9 in slot 1; etc.).

---

## Exchange 2 — User reads the artifact and notices it's wrong

> User: **So, in our sidebar it says "U_CH (page )". And on page one it
> shows U_CH's name as "U_CH". I think it might be helpful to make the
> sidebar display "U_CH[0..3]". I think the "x4" should use a larger
> font. And maybe the schematic label should be rendered as "U_CH[0..3]".
> Also, it's not clear how to set number of subdrawing copies. Is it a
> property?**
>
> **Also, this doesn't satisfy the cornerstone requirement of the
> feature: vectorization. The subdrawing has 4 inputs, 4 outputs, and 4
> resistors when it ought to have one of each. The main drawing looks
> close, but on closer inspection it's just a well-decorated
> non-vectorized workflow.**
>
> **We have decoupled the schematic from driving the netlist, so
> there's no reason we need everything instantiated in the drawing 1:1
> anymore.**

> Assistant:
>
> You've put your finger on the cornerstone — and yes, the demo I just
> showed is a hand-unrolled body that happens to be drawn once, not real
> vectorization.  Let me lay out what's actually wrong and what it takes
> to fix.
>
> **The half-measure I shipped**
>
> The C++ `repeatBusPinBitName` returns the bit-K member name (`IN0`,
> `IN1`, ...) for slot K, and the name-matching in
> `propagateToNeighbors` requires body hier-labels to *literally be
> named* `IN0`/`IN1`/.../`IN<N-1>`.  So R5.7 emits N body labels and
> R5.6 makes the user write N body parts.  The QA test
> (`RepeatedSheetBusFanout`) asserts exactly that shape.  The spec in
> `multi-channel.md:108` actually called for a scalar body port named
> `IN`, but the C++ went the other way and the QA followed the code
> instead of the spec — and I followed both.
>
> **True vectorization is a small C++ change**
>
> In `propagateToNeighbors`, in addition to comparing body labels
> against the bit-K member name, also compare against the bus *base*
> name (`IN`).  If the body's hier-port is `IN` (scalar) and the
> parent's pin is `IN[0..3]`, bind via the slot index — the
> `m_repeat_bus_bit_index` machinery we already have on
> `CONNECTION_SUBGRAPH` does the bit-K clone correctly.  Both shapes
> (vectorized scalar port OR per-bit labels) can coexist; the QA test
> stays green, and the recommended emit becomes the vectorized one.
>
> **Then the klicad-python emit collapses**
>
> Body emits 1 part (one R wired to scalar `IN`/`OUT` nets) and 2
> scalar hier-labels (`IN`, `OUT`).  Parent already emits the
> bus-shaped sheet pins correctly.  Annotation still produces 16
> component instances in the netlist via the slot expansion; the
> schematic-side .kicad_sch shrinks from ~11KB to ~2KB.
>
> **UX gaps you saw**
>
> - "U_CH (page )" in the hierarchy navigator — the page number is
>   empty for synthetic clones.  Either fix per-slot numbering
>   (`U_CH:1`, `U_CH:2`) or render the repeat in the navigator
>   (`U_CH[0..3]`).
> - "×4" decoration in `sch_painter.cpp` should bump font size and
>   probably move.
> - On-canvas sheet name → "U_CH[0..3]" is a `SCH_FIELD` formatter tweak.
> - `repeat_count` should appear in the sheet-properties dialog
>   (`dialog_sheet_properties.cpp`), not just be set programmatically —
>   currently the only way to set it IS via the API.
>
> **Proposed sequence** (parallelizable):
>
> 1. C++ — scalar body port matching (the cornerstone).
> 2. klicad-python — vectorized emit.
> 3. UX — sheet-properties dialog.
> 4. UX — canvas / navigator polish.
>
> #1 and #3 are independent C++ work in different files.  #2 depends on
> #1 (it would emit a shape the C++ doesn't yet accept).  #4 is
> independent visual polish.
>
> Want me to dispatch #1 and #3 in parallel, then #2 once #1 lands,
> then #4?

---

## Exchange 3 — User asks for causal analysis at multiple levels

> User: **Do a causal analysis at various levels of analysis.  What is
> the most helpful layer?  Where does the intent live?**

> Assistant:
>
> Doing this properly — going to walk it bottom-up, then converge on
> where the intent is forced to live vs. where it should live.
>
> ---
>
> **L0 — The user's design intent**
>
> > "I have a per-channel function.  Run it N times.  Wire each instance
> > to a different bit of these buses."
>
> That's it.  The function (body) and the cardinality (N) are
> *independent*.  Cardinality is a property of the *instantiation
> site*, not of the function.
>
> ---
>
> **L1 — The DSL (`klicad-python`)**
>
> The Python encodes this correctly today:
>
> ```python
> ch = Circuit("ch", ports=["IN[0..3]", "OUT[0..3]"])  # the function
> top.add(ch.instance(repeat=4, IN="INBUS[0..3]", ...))  # the loop
> ```
>
> `repeat=4` is one scalar at the call site.  Already vectorized.
> Intent lives here cleanly.
>
> ---
>
> **L2 — The .kicad_sch file format**
>
> ```
> (sheet
>   (property "Sheetfile" "ch.kicad_sch")
>   (repeat_count 4)
>   (repeat_instances (uuid ...) (uuid ...) (uuid ...)))
> ```
>
> Also vectorized.  One SCH_SHEET + a scalar count + N-1 stable KIIDs
> for round-tripping.  The N-1 KIIDs aren't expansion of the body —
> they're persistent identities for the *instantiation* sites so
> per-instance annotation survives a save/load.
>
> Intent is preserved.  The file format already does the right thing.
>
> ---
>
> **L3 — `BuildSheetList` (synthetic clones)**
>
> Walks the on-canvas SCH_SHEET, mints N-1 synthetic clones, returns N
> hierarchy paths sharing one screen.  This is *expansion*, but only of
> the *path namespace* — the body is still one SCH_SCREEN.  The "loop"
> gets unrolled into addressable paths for annotation/netlist purposes.
> Body untouched.
>
> Still vectorized.  Intent honored.
>
> ---
>
> **L4 — `CONNECTION_GRAPH::propagateToNeighbors` (the bend)**
>
> Here's where we forced unrolling.  The bit-K binding is implemented
> by matching the body's hier-label *name* against
> `repeatBusPinBitName(path, pin)` which returns `IN0`, `IN1`, ...  The
> slot identity is in the *path*, but the matcher demands it also
> appear in the *label name*.  So the body has to carry N hier-labels
> and N body parts — one per slot — and only the K-th one is "active"
> per slot K.  The other N-1 stay slot-local.
>
> This is the root-cause inversion.  **Slot identity is path-level, but
> the matcher reaches into label-name space.**  That contamination
> propagates downward — R5.7 then has to emit N labels; the user then
> has to write N body parts to wire to them.  The DSL stays vectorized
> in form but lowers into a hand-unrolled shape.
>
> ---
>
> **L5 — `NETLIST_EXPORTER_XML`**
>
> Walks `GetNetMap()`.  Doesn't know about multi-channel.  Sees
> whatever the connection graph produced.  Faithful projection of L4's
> choices.
>
> ---
>
> **L6 — Ratsnest spec / `set_spec` (C.5)**
>
> Renders the *expanded* netlist on the schematic.  Compensates
> visually for what L4 fails to express structurally.  It's a polish
> layer over the unrolled shape.
>
> ---
>
> **Where the intent actually lives, by layer:**
>
> | Layer | Encodes "N" as | Encodes "per-channel function" as |
> |---|---|---|
> | L0 user | a number | a body |
> | L1 DSL | `repeat=4` | `ch = Circuit(...)` |
> | L2 file format | `(repeat_count 4)` | sub-sheet file |
> | L3 BuildSheetList | N paths | one screen |
> | **L4 conn-graph** | **N body labels (name)** | **N body parts** |
> | L5 netlist | implicit (16 records) | — |
> | L6 ratsnest | spec edges | — |
>
> **L4 is the only layer that inflates N.**  Everything above is
> parametric; everything below is downstream of L4's lowering.  L4 is
> the bottleneck and the bug.
>
> ---
>
> **Where the intent *should* live (the answer to your question):**
>
> In the *path*.  `SCH_SHEET_PATH::GetSlotIndex()` already returns the
> slot K.  That's where slot identity belongs structurally — it's the
> contextual parameter that the body function is being evaluated under.
> The body shouldn't have to know its own slot index by encoding it in
> label names.
>
> A correct L4 reads: "for each slot path K, body subgraph named `IN`
> (the bus's base name) binds to parent's `IN[0..3]` bit K — use the
> path's slot index to pick the member."  That's exactly the machinery
> in `m_repeat_bus_bit_index` we already added; we just need to relax
> the *name match* to accept the bus base, not just the bit-K member.
>
> That makes:
> - L1 stays parametric (already correct)
> - L2 stays parametric (already correct)
> - L3 stays parametric (already correct)
> - L4 *finally* accepts a parametric body and uses the path's slot
>   identity for bit selection
> - L5/L6 fall out — they project whatever L4 produces
>
> ---
>
> **Most-helpful layer to work at:** L4 (the C++ connection graph
> extension), because every layer above it is already correct and every
> layer below it is downstream of L4's choices.  One file's change
> (`connection_graph.cpp`, ~30 lines) unblocks the entire vectorized
> shape — the Python lowering collapses, the schematic shrinks 6×, the
> user-facing mental model matches the disk format, and the UX
> cosmetics (sheet name, ×N decoration, properties dialog) become
> straightforward because the canvas only has to display *one* body.
>
> The Python emit (R5.6/R5.7 in `_klicad_sch.py`) becomes a cleanup
> *after* L4 lands — they're symptoms, not causes.  Fix L4 first; L2
> collapses on its own.
>
> Want me to dispatch the L4 fix?  It's a small, surgical change with
> the QA test as a regression gate.

---

## Exchange 4 — User pushes for upstream causes

> User: **These all feel like proximal causes.**

> Assistant:
>
> You're right.  Let me actually do the work.
>
> The proximate stuff — L4 matches by name, QA asserted the shape, spec
> was ambiguous — those are all consistent with each other.  That's
> what tipped me off earlier and what I missed.  If five layers are all
> consistent with each other but inconsistent with the user, the cause
> isn't in any of them; it's in the process that produced them.
>
> Going upstream:
>
> **P1.  Each layer was verified against the layer beneath it, not
> against L0.**  R5.7 was checked against the C++ shape.  C++ shape was
> checked against the QA test.  QA test was checked against C++
> behavior.  Each link was internally consistent.  The chain was never
> verified against user intent.
>
> **P2.  The QA test was written after the implementation.**  That's a
> structural inversion.  Tests-after-code ratify whatever shape the
> code already has.  The implementer's mental model becomes the
> assertion library's mental model.  Future readers — auditors,
> downstream chunk-authors, me — read the test and conclude "this is
> the shape."  The test ossified a wrong choice.
>
> **P3.  The chunks insulated decisions from each other.**  R3.3 in
> week one decided "match labels by bit-K name."  R5.7 in week three
> said "OK, then we must emit N labels."  Neither author was the same
> person; neither audit checked the other's assumption.  Chunk
> boundaries gave each decision a small, internally coherent scope.
> The cross-chunk question — *is this lowering chain the right shape
> end-to-end?* — had no owner.
>
> **P4.  We landed netlist-decoupling (C.5/C.6) after the schematic
> emit was frozen.**  Until C.5/C.6 went in, the schematic *had* to be
> 1:1 with the netlist — every wire you wanted in the netlist needed a
> label in the .kicad_sch.  R5.6/R5.7 were written under that
> constraint.  C.5/C.6 lifted the constraint days later.  Nothing in
> the workflow revisited R5.6/R5.7.  We added the new capability and
> left the old emit shape paying for a tax it no longer owed.
>
> **P5.  The auditor verified shape conformance, not intent
> conformance.**  The auditor's job description (in
> `shiny-sleeping-karp.md`) is "diff review + thin-layer check +
> paradigmatic drift check."  All three of those are *intra-codebase* —
> they compare commits to existing patterns.  None of them compares the
> emitted artifact to user expectation.  The auditor couldn't catch
> this kind of drift because the auditor doesn't run the demo.
>
> **P6 (deeper).  We had no eyeball in the verification loop.**  Every
> test we ran — pytest, the 22 C++ QA cases, A.11's xfail-to-strict
> transition — was automated assertion.  Automated assertions verify
> against the author's model.  *The author's model was wrong.*  That's
> the only failure mode automated testing cannot catch: when the
> assertion writer and the implementer share the same misconception.
> The output passes every test and looks wrong to the first human who
> opens the file.
>
> You provided the eyeball.  The demo I ran was the first time anyone
> in this entire R0-through-C.7 arc *looked at the schematic* and asked
> "is this what we meant?"  Not "does the netlist match" — does the
> *thing on the screen* match what someone authoring `repeat=4` thinks
> they're building.  Nobody asked that.
>
> **P7 (root).  The workflow has no checkpoint for "is the user's
> mental model still encoded?"**  Chunk-by-chunk decomposition is great
> for delivering features without scope creep, but it deletes the
> global view that catches a slow drift in lowering semantics.  The
> drift in this case is small *per chunk* — R3.3 chose name-matching,
> R5.7 emitted to match, R3.3.1 patched a clone case, R5.4 added a
> kwarg — each is a defensible local move.  It's the sum that diverges.
> There was no scheduled "stop, demo, look, ask the user if the shape
> is right" gate.
>
> That's the actual cause.  L4 is the proximal lever; everything above
> it expressed the user's intent correctly until it hit L4's name-match.
> But the reason we landed an L4 that bends the intent is process —
> chunk-locality + tests-after + auditors that compare commits to
> commits + a netlist-decoupling realization that arrived after the
> emit was committed + zero scheduled eyeball-on-output.  Patching L4
> fixes the symptom.  The process gap is what produced it and will
> produce the next one.
>
> The intent lives at L1.  The intent is *honored* down through L3.
> It gets *projected* out of shape at L4 because the matcher is
> name-keyed instead of path-keyed.  But the *reason* it gets projected
> wrong is that no step in our process was watching the projection.
>
> Two things follow:
>
> 1. The L4 fix is correct and small and we should still do it.
> 2. We're going to ship something else wrong-shaped on the next
>    feature unless we add a step.  The cheapest version: before
>    declaring a feature complete, hand-author a minimal demo, open the
>    artifact, look at it, ask "is this what someone writing the DSL
>    expects to see?"  That's the step you just performed.  It should
>    be a deliberate gate, not an after-the-fact catch.
>
> Want me to add that gate to the chunk-loop plan + auditor prompts as
> the first move, and then dispatch the L4 fix?

---

## Exchange 5 — User confirms direction, requests auditor + spec update

> User: **Bingo.  Let's dispatch an auditor to look at all this and
> attempt to find 3 "correct" fixes.  Then we'll weigh our options.  I
> think you should write our last 5 conversational exchanges verbatim
> to a file and provide that as reference to our auditor also.**
>
> **Also, let's update our spec on what an auditor ought to be doing.**

(This document is the verbatim file the user requested.  The next
moves: update the auditor spec, then dispatch the auditor with this
file + the spec update + an explicit ask for three candidate "correct"
fixes that preserve intent through the lowering chain.)
