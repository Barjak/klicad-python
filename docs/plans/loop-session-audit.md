# Loop-session audit — 2026-05-27

**Scope.** A ~9 h unattended Opus loop ran three tracks (multi-channel,
schematic ratsnest M1, `Circuit.to_netlist` + ratsnest M2) and ended
on a wedged IPC-modal-stall investigation.  This report assesses (1)
the architectural verdict on the modal-stall fix, (2) the merge-
readiness of what landed, (3) a concrete forward path, and (4)
honest critique of how the loop ran.

Evidence cites file:line under
`/home/jakob/projects/{KliCAD,klicad-python}`.

---

## 1. Architectural verdict — IPC modal-stall

### 1.1 What's actually wrong

The current architecture (committed master + the existing
`close_topmost_dialog` binding at
`common/api/bindings_gui.cpp:413`) reasons like this:

1. KINNG listener thread (`libs/kinng/src/kinng.cpp:130-172`) is
   a strict REQ/REP loop: `nng_recv` → invoke callback → wait on
   `m_replyReady` condvar → `nng_send` → loop.
2. The callback (`common/api/api_server.cpp:247-341`) wraps the
   request in a `wxCommandEvent` and `QueueEvent`s it onto the wx
   main loop (line 340).
3. `handleApiRequestString` (`api_server.cpp:351-429`) runs on the
   wx main thread, calls handlers, calls `m_server->Reply()` —
   which signals the condvar — and returns.

The deadlock when a modal opens *inside* a handler:

- Test calls `run_python("pm.load_project(...)")`.
- The handler runs on the wx main thread.  Project-open code
  encounters the missing PCM schema and pops a modal via
  `wxMessageBox` or a `wxDialog::ShowModal()`.
- `ShowModal()` enters a nested wx event loop on the **same**
  thread.  The handler has not yet returned, so
  `m_server->Reply()` has not been called.
- KINNG listener is still parked in
  `m_replyReady.wait()` at `kinng.cpp:158`.  It cannot `nng_recv`
  a new request until that wait unblocks.
- Therefore any "out-of-band dismiss-modal" request sent from
  Python sits in the OS socket buffer; the listener never sees
  it; `pynng.Req0.recv` times out.

The user's in-progress patch
(`common/api/api_server.cpp` working-tree diff, lines 235-332)
peeks at `client_name` *inside* `onApiRequest` and runs the dismiss
logic on the listener thread before queueing the wx event.  The
patch is **structurally unable to work** as long as the listener
thread cannot reach `nng_recv` while the prior reply is pending.
That is precisely the situation the modal creates.  Evidence:
`kinng.cpp:154-172` — `m_callback` runs to completion, then a
condvar wait, then `nng_send`, then back to recv.  The OOB peek
runs only when the recv has already happened, i.e. only on
requests **after** the modal is already closed.  The current OOB
attempt cannot fix the canonical stall case.  Revert.

### 1.2 Is a "second socket" sloppy?

It's not sloppy — split sockets per message class is a textbook
NNG pattern (NNG's docs explicitly contrast REQ/REP with PAIR/PAIR
or PUB/SUB for control planes, and recommend separate sockets for
separate state machines).  But **for a single-process embedded
API server you can do strictly better than a second socket using
NNG `nng_ctx`.**

Per `nng_rep(7)`:

> "A REP socket processes one request-reply exchange at a time
> ...  Concurrent request handling can be supported by opening
> multiple contexts via `nng_ctx_open()`; each context may have
> at most one outstanding request."

That is **exactly** the desired primitive.  Two contexts on the
same listener socket:

- **Context A — main**.  Receives normal requests, queues them
  onto wx, returns reply when the handler signals.
- **Context B — control**.  Receives control requests (e.g.
  `DismissModal`, `Ping`, `CancelPendingRequest`) and replies
  *synchronously inside the listener thread*, never touching the
  wx main loop.

Two contexts on one socket is the canonical answer.  No second
socket file, no extra socket-path config, same auth/token, and
the listener becomes able to recv-a-second-message while the
first reply is still pending.  This is what NNG ships `nng_ctx`
for.

Verdict: **`nng_ctx` is the cleanest fix.  Two-socket would be
acceptable but is unnecessary.**

### 1.3 Could we fix it inside wx instead?

A few alternatives were considered.  None is as clean.

**a) `wxTheApp->CallAfter` + always-on-thread API server.**  Move
the API server to a worker thread, marshal every C++ call onto wx
via `CallAfter`.  This does not help: `CallAfter` enqueues onto
the wx main loop, which is exactly the loop suspended by
`ShowModal()`.  Worse, `CallAfter` events *are* processed by the
nested modal loop (wx's nested loop dispatches all idle/queued
events), so the handler would run, but the modal blocks the
*handler's own* call into `pm.load_project` from returning.  The
modal-during-handler deadlock isn't a wx event-routing problem;
it's a recursive-handler problem.

**b) Custom `wxDialog` subclass that registers with the API
server on `ShowModal`.**  Every KliCAD dialog inherits from
`DIALOG_SHIM`.  We could add a hook so `DIALOG_SHIM::ShowModal`
registers `this` with the API server and unregisters on
`EndModal`.  The control path could then *find* the right dialog
to dismiss — but it doesn't solve the recv-blocked problem.  The
registration is useful *combined* with `nng_ctx` (the control
context can ask the registry "what's the topmost modal?" without
wandering `wxTopLevelWindows`), but it isn't the load-bearing
fix.  Worth doing as a small polish, **after** the `nng_ctx`
patch lands.

**c) `wxApp::Yield` in modal loops + suppress modal at the
source.**  Suppressing the modal at source (e.g. don't pop a
schema-not-found dialog at all when an API client is connected)
is brittle: KliCAD has dozens of modal sites, and every new one
becomes a stall surface.  `Yield` doesn't help because the recv
is on a different thread.

**d) Convert `KINNG_REQUEST_SERVER` to `nng_aio` + `nng_ctx`.**
This is option (1.2).  Yes.

### 1.4 The recommended fix (concrete)

1. **Refactor `KINNG_REQUEST_SERVER` to use `nng_ctx` and
   `nng_aio`.**  Open N contexts (N=2 suffices; N=4 leaves
   headroom).  Each context has its own `recv → callback →
   reply` mini state machine.  The callback signature stays
   `function<void(string*)>`; add a `ctx_id` so `Reply()` can
   route back.  Use `nng_aio` so the listener is non-blocking
   and a per-context callback fires when a request arrives.

2. **Introduce a `client_name`-based dispatch in `onApiRequest`**
   (already in the WIP patch — keep this part conceptually).
   If `client_name == "__klicad_control__"`, route to the
   *control* code path which runs **on the listener-thread side
   of the callback**, calls `wxTheApp->CallAfter([...]{ ... })`
   or `wxQueueEvent` to send the dismiss event into the modal's
   nested loop (which **will** process queued events — that's
   how textbox typing works in a modal), and replies
   immediately.  Do **not** wait for the modal to actually close
   before replying — the client's job is to know that "dismiss
   requested" ≠ "dismiss happened" and poll.

3. **Add `DIALOG_SHIM`-level modal registry** (small follow-up).
   On `ShowModal`, push `this` to a thread-safe stack; on
   `EndModal`, pop.  The control path consults this stack
   instead of walking `wxTopLevelWindows` (the wandering walk
   in `bindings_gui.cpp:417-434` is best-effort and racy on the
   listener thread).

4. **Keep `close_topmost_dialog`** as the *normal-path*
   binding (it still works when no other request is in flight,
   and is the right tool for tests that wrap operations in a
   dismiss-modals loop).  Add a sibling `dismiss_modal_control()`
   on the *control* context for the deadlock case.

5. **klipy/`klicad.py` retry logic**: replace the WIP fallback
   with a clean `KliCAD.control_dismiss_modal()` method that
   uses a separate `KiCadClient` instance tagged as the control
   client.  Drop the timeout-sniffing/`_dismiss_modal_on_timeout`
   path in `run_python` — fragile and surprises callers.

### 1.5 What to do with the working-tree patches

- **`common/api/api_server.cpp` OOB attempt** — Revert.  The
  control-plane idea is right; the `client_name`-peek logic at
  `:235-332` is reusable in the `nng_ctx` redesign, but as
  committed the patch is dead code (cannot fire when a modal is
  up — the only case it's meant for).

- **`klipy/klicad.py` `dismiss_modal_oob` + retry** — Revert.
  Same reason.  Replace with the `control_dismiss_modal()`
  method after the C++ fix lands.

- **`qa/tests/eeschema/test_ee_item.cpp` SCH_RATSNEST_ITEM_T
  case** — **Keep and commit.**  This is a genuine gap: the test
  switch needs the new enum value to silence a `BOOST_FAIL`.  No
  controversy.

- **klicad-python test rename (`kicad_native_*` →
  `klicad_native_*`)** — **Keep, but check coverage.**  `grep`
  shows two test files were missed by the sed pass:
  `tests/test_hierarchy.py` (18 hits) and `tests/test_diff_apply.py`
  (6 hits) both still use the old prefix.  Add them to the
  rename and commit as one patch.

---

## 2. Feature-work merge-readiness

### 2.1 Track A — multi-channel finish

Committed on `KliCAD loop/integration-7` (HEAD `2d02bea855`):

```
2d02bea855 + R5.4-binding (98dd6f175c)
98dd6f175c R5.4-binding: add_sheet accepts repeat_count + repeat_instances
5d79ec5a56 F1: PathHumanReadable slot suffix for synthetic clones
bbea8ea1b5 R3.3.2: route reverse push through resolveHierPinPushTarget
ff0e570266 R3.4: QA test for bus-pin bit fan-out on a repeat=4 child sheet
9275ff4cd6 R3.3.1: drop KIID prefilter in reverse-direction hier-port fan-out
3a5b26ec1b R3.3: bus-pin bit fan-out at hier-pin connection site
a065e7d835 R3.2: SCH_SHEET_PATH::GetSlotIndex
d12711f46e R3.1: connection_graph hier-pin push respects synthetic-clone slot
```

Committed on `klicad-python loop/integration-7` (HEAD `1e12582`):
R5.1, R5.2, R5.3, R5.4, R5.5 — all 5 layers of the Python DSL
plus C.1 / C.2 / C.4 (track C).

**Static QA**: 46/46 qa_eeschema, 135 pure-python passing.

**Live-test coverage**: **zero**.  No `repeat=4` schematic ever
emitted into a real KliCAD process; no netlist export verified
end-to-end; no PCB Multi-Channel detection (chunk A.12 / R7)
attempted.  The two `auditor live-test` chunks A.11 and A.12
were never executed — the loop dispatched them but they require
a running KliCAD with no modal stalls, which is the exact gap
that ended the session.

**Verdict: paradigmatic implementation is sound; merge to
mainline is premature.**  The R3 connection-graph code is the
sketchiest piece — `resolveHierPinPushTarget` is invoked from
two reverse-direction sites and a forward site, and the only
test is a single C++ unit
(`test_repeated_sheet_busfanout.cpp`, four cases, all pass).
Real circuits with N > 4, mixed bus widths, vector slices,
and nested repeats are unverified.  Before any merge: A.11
(netlist round-trip on a real `repeat=4` design) and A.12
(PCB Multi-Channel peer detection) must pass.

**Branch state**: keep `loop/integration-7` as feature branch.
Do not fast-forward to mainline until A.11 + A.12 are green.

### 2.2 Track B — schematic ratsnest M1

All 6 chunks committed on KliCAD `loop/integration-7`:

```
a58ab05139 M1.6: layer toggle for LAYER_SCH_RATSNEST in View menu
7ae81fd099 M1.5: paint SCH_RATSNEST_ITEM on the schematic GAL
fd0b2cdd7e M1.4: refresh hook
bb267f331a M1.3 build-glue: link eeschema_kiface_objects against delaunator
2a663a2c67 M1.3: SCH_RATSNEST_BUILDER MST adapter
6ce59c1df5 M1.2: SCH_RATSNEST_ITEM data class skeleton
a567efe824 M1.1: add LAYER_SCH_RATSNEST schematic layer
```

Builds clean, intra-C++ duplication (`disjoint_set` + Kruskal
lifted from `pcbnew/ratsnest/ratsnest_data.cpp`) is logged as a
follow-up refactor (move helpers to `libs/kimath/include/geometry/`).

**Live-test coverage**: zero.  Never visually verified.  The
auditor's M1.5 / M1.6 verdict was code-review only.

**Sketchy bits worth eyes-on review before merge**:

- M1.4 hook into `RecalculateConnections` at
  `eeschema/schematic.cpp:~1775` — does the rebuild run on every
  connectivity refresh (perf), or only on connection-graph
  invalidation?  Audit log doesn't say.
- M1.5 view-item-owner pattern lifted from
  `pcb_draw_panel_gal.cpp:485-486, 921-922` — pcbnew's pattern
  is correct but the schematic GAL panel has different
  ownership semantics.  The auditor described it as
  "line-for-line" — that's worth checking on actually-rendered
  output.
- M1.6 menu toggle (`View → Show Schematic Ratsnest`) — works
  off `eeschema_settings.show_sch_ratsnest`; condition lambda
  wired to `mgr->SetConditions`.  Audit accepted without
  visual confirmation that the menu *check state* mirrors the
  setting after toggle.

**Verdict: code-complete, pre-merge gate is one human-eyes
session driving KliCAD with the 8pin design open and
toggling the rats on/off.**  Pure code review by auditors
cannot catch wrong-layer / wrong-color / wrong-coordinate
bugs.

### 2.3 Track C — `Circuit.to_netlist` + ratsnest M2

Committed (klicad-python only):

```
1aa2227 C.4: thin Circuit.to_netlist() method wrapper
ee723ca C.2: verify thin netlist wrapper handles 3-part circuit
5683bee C.1: thin netlist wrapper via kicad-cli
```

C.5 / C.6 / C.7 (ratsnest M2 spec-driven) **not attempted**.
That work was blocked-on-B-complete in the chunks doc and got
budget-starved.

**Live-test coverage**: C.2 is "verify thin wrapper handles
3-part circuit" — pure-python `pytest.skip()` when `kicad-cli`
isn't on PATH.  The audit log lists it as `commit ee723ca` with
"substring asserts only; skips when KliCAD IPC unavailable".
**It almost certainly does not actually run end-to-end.**  C.3
("auditor live"  kicad-cli round-trip) was never executed.

**Verdict: C.1 / C.2 / C.4 are merge-ready** (thin wrappers,
no risk, no format logic, fall back to skip when CLI missing).
M2 (C.5-C.7) is a separate work-stream that depends on the
modal-stall fix landing first — without a stable live-test
harness, the spec-driven ratsnest binding can't be verified.

### 2.4 Summary table

| Track | Code state | Static QA | Live tests | Merge-ready? |
|---|---|---|---|---|
| A R3/R5 (multi-channel) | committed integration-7 | 46/46 + 135 pytest | none — A.11/A.12 deferred | **no** — gate on A.11, A.12 |
| B M1 (schematic ratsnest) | committed integration-7 | builds clean | none — never rendered | **no** — gate on one visual session |
| C C.1/C.2/C.4 | committed integration-7 | passes (or skips) | C.3 never ran | **yes** as thin wrappers; **no** as verified |
| C C.5-C.7 (M2) | not started | n/a | n/a | blocked on modal-stall fix |

---

## 3. Recommended path forward

### 3.1 First — fix the modal stall

Two-day arc, in this order:

**Day 1.**

1. Refactor `libs/kinng/src/kinng.cpp` to `nng_ctx` + `nng_aio`.
   Open 2 contexts.  Per-context `Reply()` via a `ctx_id` token
   threaded through the `function<void(string*, ctx_id)>`
   callback.  Existing single-recv semantics preserved for
   the main context.  ~150 LoC + tests.

2. In `common/api/api_server.cpp::onApiRequest`, dispatch on
   `client_name`:
   - default → existing wx-queue path (with `ctx_id` plumbed
     through `wxCommandEvent::SetClientData` so `Reply()` knows
     which context to reply on);
   - `"__klicad_control__"` → execute inline on the listener
     thread; `wxTheApp->CallAfter` for any wx side-effect;
     `m_server->Reply(ctx_id, ...)` synchronously.

3. Revert the WIP `dismiss_modal_oob` in `klipy/klicad.py`.
   Add `KliCAD.dismiss_modal_control()` that uses a separate
   `KiCadClient` with `client_name="__klicad_control__"`.

**Day 2.**

4. Land `DIALOG_SHIM` modal-registry (small).
5. Re-run the live-test suite: `tests/test_gui_smoke.py`
   should pass to first-real-modal, then `dismiss_modal_control()`
   should clear it, then the suite should continue.
6. Bring the test prefix rename (klicad-python tests, the two
   missed files) to clean.

### 3.2 Second — live-test the feature work

Order matters; each one validates a layer below it.

1. **Multi-channel netlist (A.11)**.  Build a `repeat=4` Circuit in
   `klicad-python`; call `to_schematic()`; call
   `kicad-cli sch export netlist`.  Assert 4 body-symbol copies
   per body part; assert per-bit bus binding.  This exercises
   R3 (connection graph), R5.4 (`add_sheet` repeat-binding), and
   C.1 (kicad-cli wrapper) end-to-end.
2. **Multi-channel PCB peer detection (A.12 / R7)**.  Use the
   driver-board 8pin archive, refactor to `repeat=8`, run the
   PCB Multi-Channel detection tool via existing `klicad_native_pcb`
   binding.  Assert 8 peer rule areas.
3. **Schematic ratsnest visual session**.  Open 8pin design,
   toggle View → Show Schematic Ratsnest, screenshot both
   states.  Confirm rats colored, MST-shaped, dashed.
4. **Track C M2 (C.5-C.7)**.  Only after modal-stall fix.

### 3.3 Branch hygiene

- Keep `loop/integration-7` on both repos as the feature
  trunk for the loop's output.  Do **not** merge to
  mainline.
- Per-track worktrees (`KliCAD-trackA`, `-trackB`,
  `klicad-python-trackA`, `-trackC`) can be removed once
  integration-7 is the single source of truth.
- After the modal-stall fix + the three live-test gates above
  pass, integration-7 graduates to a feature-PR-equivalent merge
  to `feature/always-on-api-server` (or whatever the local
  fork's mainline is).

---

## 4. Honest critique of the loop process

### 4.1 What the auditors missed

**A. The 200 skipped pytests.**  Every auditor entry in
`audit-log.jsonl` reports "tests_ok: true" or
"tests_passing: 46, tests_failing: 0" based on whatever
`pytest -q` returned.  Not one auditor checked the **skipped**
count or asked *why* a third of the suite was skipped.  Lines
26, 36, 41, 43 of the audit log all green-light without ever
running a live test.  This is the central process failure: the
audit checklist tested for **regressions** (did anything break?)
not for **claim-validity** (does the feature actually work in
practice?).

**B. "Optional" creep in live-test prompts.**  Chunks A.11,
A.12, B.5, B.6, C.3, C.6, C.7 are all marked "auditor runs.
Builder writes only" in `loop-chunks.md`.  But the auditor
prompt (referenced in the audit-log entries) treated those as
**optional** — auditors never executed them.  No state in
`track-a.json` or `track-b.json` calls out "A.11 / A.12 still
pending live-run".  The auditor verdict on loop_n=4 line 43
explicitly says "*Skipped live KliCAD R4 run: track-c not
integrated, R5.3 still in flight*" — that's a deferral that
never came due.

**C. Accepting "live" tests that quietly `pytest.skip()`.**
C.2's commit message says "skips when KliCAD IPC unavailable".
The auditor accepted that without questioning whether the IPC
*was* available during the audit run.  The 200 skipped tests
are the same pattern — every smoke test starts with a
`@pytest.fixture` that yields a `KliCAD()` only if it can
connect, and skips otherwise.  Skip ≠ pass.

**D. Drifting away from the binary.**  After loop_n=4 the
auditor stopped re-checking binaries on freshly-rebuilt
KliCAD.  The audit entries after `integration-6` (timestamp
1779919333) verify only diff review.  R3.3.2 fixed 4 failing
QA tests — but the auditor noting that verdict
(`tests_passing:46, tests_failing:0`) was on a single tree;
nobody re-ran the QA suite after F1 and R5.4-binding landed.

### 4.2 What the next session's loop should do differently

1. **Treat "skipped" as a first-class failure mode in the
   auditor's tests_ok check.**  `audit` entries must include
   `tests_skipped: N` and the auditor must justify any non-zero
   skip count (does the test require live KliCAD? was KliCAD
   running? did it connect?).

2. **Live-test gates are mandatory, not optional.**  Reword
   chunk prompts: a chunk does not enter "complete" until its
   live-test acceptance criterion has actually been executed
   against a live KliCAD by *someone* — auditor, conductor,
   or human.  Pure-python pass is necessary but not sufficient.

3. **Live-test infrastructure first.**  Before dispatching
   feature-track builders, dispatch a "loop-zero" agent that
   verifies the live-test path works end-to-end on the baseline
   (no modal stalls, IPC alive, smoke tests green).  Without
   that gate, all feature work is on speculative ground.

4. **Auditor should periodically re-run the full live-test
   suite, not just diff-audit.**  Add a `live_audit` track to
   `loop.json` separate from `audit_loop`, fired every N hours
   regardless of chunk-cycle activity.

5. **State files must surface unresolved live-test debt.**
   `track-a.json` lists `audit_followups_done` but not
   `live_tests_pending`.  Make that a first-class field;
   refuse to declare a track "complete" while it's non-empty.

6. **Sanity-check fork-rename hygiene before the loop starts.**
   The `kicad_native_*` → `klicad_native_*` rename took the
   session down at hour 8 — the C++ exports and Python imports
   had been mismatched the entire run.  A 1-minute `grep`
   would have caught it before chunk 1.  Add a "pre-flight
   sanity" agent that greps for known rename drift.

### 4.3 What the loop did right

For completeness: the loop's thin-layer enforcement worked.
C.2's 332-line hand-rolled sexpr parser was caught and reverted
within one cycle (audit-log line 21-22), and the recurrence-
prevention rule was added to `loop-chunks.md`.  The auditor
caught R3.3 / R3.3.1 / R3.3.2 in three successive cycles, each
time honing the same bug into a tighter fix.  That sub-system
(C++ paradigmatic verdicts on diffs) genuinely works.

The hole is on the live-binary verification end of the loop,
not the static-review end.  Future loops should rebalance
budget toward live-driving.

---

## 5. Uncertainties I cannot resolve from desk-audit

- Does the missing PCM schema modal fire **before** or **after**
  `SetReadyToReply()`?  `single_top.cpp:495` sets ready after
  `PreloadDesignBlockLibraries`, but I don't know if PCM init
  runs in that path or in a later `wxIdleEvent`.  If it fires
  *before*, `AS_NOT_READY` would surface as an `ApiError`, not
  a timeout.  If it fires *after*, the timeout-on-handler
  scenario above is exact.  The user reported a timeout, so
  empirically it's the latter, but the code path is worth
  walking once with eyes.

- Does the wx nested modal loop dispatch `wxQueueEvent`-posted
  events?  In principle yes (it has to, for text input).  But
  *if* `API_REQUEST_EVENT` is bound to a non-frame `wxEvtHandler`
  (the `KICAD_API_SERVER` itself, `api_server.cpp:164`), and
  *if* the nested loop's dispatch only goes to active-frame
  handlers, the event could sit until the modal closes.  Worth
  one experiment: launch KliCAD, open any modal via the menu,
  fire `run_python` and observe whether it completes or hangs.

- M1.5's "view-item-owner pattern lifted from pcbnew" — auditor
  said line-for-line, but the schematic GAL panel has different
  ownership semantics.  Could be fine; need eyes on rendered
  output.

- R3.3.2 fixed slot-K bus fan-out.  Are there *other* slot-K
  paths in `connection_graph.cpp` not covered by the four-case
  QA test?  Specifically: vector slice (`DATA[1..2]`), nested
  repeat (a `repeat=4` inside a `repeat=2`), and bus alias
  rename across slots.  None of these are exercised.

These are all things one good live-driving session will surface.
The next loop's first task is to set that session up.
