# Automated Schematic Layout — Research Survey

A literature and ecosystem survey aimed at designing a complete `to_schematic()`
auto-layout strategy for **klicad-python**.

> Scope: schematic-level layout (human-readable diagrams), not PCB layout
> (electrical performance). Most VLSI placement / routing literature targets PCB
> or IC physical design; we explicitly translate findings to the schematic
> domain wherever the constraint differs.

## Table of Contents

1. [Where klicad-python is today](#1-where-klicad-python-is-today)
2. [Schematic-readability metrics (what we are actually optimizing)](#2-schematic-readability-metrics-what-we-are-actually-optimizing)
3. [Placement algorithms](#3-placement-algorithms)
   1. [Force-directed (Fruchterman–Reingold, Kamada–Kawai, FADE, FM³)](#31-force-directed)
   2. [Layered / Sugiyama and KIELER-style port-constrained drawing](#32-layered--sugiyama-with-ports)
   3. [Min-cut partitioning placement (Breuer, Kernighan–Lin, Fiduccia–Mattheyses)](#33-min-cut-partitioning)
   4. [Simulated annealing (TimberWolf and descendants)](#34-simulated-annealing)
   5. [Analytical placement (ePlace, RePlAce, DREAMPlace)](#35-analytical-placement)
   6. [ILP / SAT-based exact methods](#36-ilp-sat-based)
   7. [Template / building-block (ALIGN, BAG, LAYGO2)](#37-templatebuilding-block)
   8. [ML / RL (DeepMind, GraphPlanner, SmartGD, Schemato)](#38-ml-and-rl-approaches)
4. [Partitioning, clustering and structural recognition](#4-partitioning-clustering-structural-recognition)
   1. [Modularity / Louvain / Leiden](#41-modularity-louvain-leiden)
   2. [hMETIS / PaToH / KaHyPar multilevel hypergraph cut](#42-hmetis-patoh-kahypar)
   3. [Spectral / Laplacian clustering](#43-spectral-laplacian-clustering)
   4. [Symmetry / pattern / building-block detection](#44-symmetry-and-pattern-detection)
   5. [Signal-flow, datapath/control extraction, bus identification](#45-signal-flow-datapathcontrol-extraction-bus-identification)
5. [Routing algorithms](#5-routing-algorithms)
   1. [Maze / Lee / Hadlock / A\*](#51-maze-lee-hadlock-astar)
   2. [Rectilinear Steiner trees and the Hanan grid](#52-rectilinear-steiner-trees)
   3. [Channel and switchbox routing](#53-channel-switchbox)
   4. [Rip-up and reroute, negotiated congestion](#54-ripup-reroute)
   5. [Orthogonal hyperedge routing for schematics](#55-orthogonal-hyperedge-routing)
6. [Higher-level structure: orthogonal drawing & topology-shape-metrics](#6-higher-level-structure-orthogonal-drawing--tsm)
7. [Incremental / constraint-driven / interactive layout](#7-incremental-and-interactive-layout)
8. [Analog vs digital vs mixed conventions](#8-analog-vs-digital-vs-mixed-conventions)
9. [Existing KiCad-ecosystem schematic layout tools](#9-existing-kicad-ecosystem-schematic-layout-tools)
10. [Multi-objective combination strategies](#10-multi-objective-combination-strategies)
11. [Gaps, honest uncertainty, and open problems](#11-gaps-and-open-problems)
12. [Recommendations for `Circuit.to_schematic()`](#12-recommendations-for-circuittoschematic)
13. [Key references](#13-key-references)

---

## 1. Where klicad-python is today

Re-reading the local code at
`/home/jakob/projects/klicad-python/klipy/circuit/`:

- `_partition.py` — Louvain community detection over the signal-net graph
  projection. Filters communities by `size >= MIN_BLOCK_SIZE` and
  `boundary_ratio < 0.30`; everything else falls into a "leftover" top block.
- `_layout.py` — exposes **two** placement engines:
  - `sugiyama_positions()`: BFS-from-V/I-sources layering + barycentric
    crossing minimization + uniform coord assignment.
  - `spring_positions()`: networkx `spring_layout` (Fruchterman–Reingold) on
    a weighted graph with intra-cluster bonus springs, inter-cluster phantom
    edges, shrink-clusters post-pass, and a centroid-only page-fit pass.
- `_route.py` — A* on a Hanan grid expanded by `CHANNELS_PER_PIN=3` lateral
  channels, hub-and-spoke Steiner construction, with per-net keepout and
  forbidden-point bookkeeping; falls back to coincident labels when A* fails.
- `_klicad_sch.py` — orchestrator (1300 lines) that calls these in sequence.

The architecture is already textbook: partition → place → route, with two
placers wired in. The shortcomings the user describes are consistent with what
the literature predicts about these specific algorithms:

- **Wide horizontal sprawl** — Sugiyama with one layer per BFS depth degenerates
  to a single row when the circuit is shallow, and the spring layout's Coulomb
  repulsion explodes the bounding box without a bounding constraint.
- **Label fallback frequency** — successive-path Steiner construction with a
  sparse Hanan grid (~3 channels/pin) blocks more often than a true global
  router would, especially around dense pin clusters.
- **No symmetry / signal-flow / cluster cohesion** — none of these are
  measured or optimized directly. Barycentric ordering reduces crossings as a
  side effect, but it's not aware of differential pairs, current mirrors,
  or input-on-the-left/output-on-the-right conventions.

Throughout this survey I flag which findings address each of these concerns.

---

## 2. Schematic-readability metrics (what we are actually optimizing)

Schematic readability is a **human-visual** problem, not an electrical one.
The graph-drawing literature (especially Purchase's psychometric studies and
Tamassia's *Handbook of Graph Drawing and Visualization*, 2014) converges on
a short list of *aesthetic criteria* that empirically correlate with
diagram-reading time and error rate:

| Metric | Definition | Notes for schematics |
|---|---|---|
| Edge crossings | Count of wire-wire crossings | Strongest single predictor of confusion in Purchase's studies |
| Bends per edge | Number of orthogonal direction changes | Each bend adds visual noise; >3 bends/wire is a "smell" |
| Total wirelength | Sum of segment lengths | Proxy for diagram area + visual complexity |
| Edge length uniformity | Variance of wire lengths | Long lone wires are hard to follow |
| Angular resolution | Minimum angle between edges meeting at a node | Almost always 90° in schematics by convention |
| Symmetry score | Match between geometric symmetries and structural symmetries (e.g. mirror pairs) | Critical for analog (diff pairs, current mirrors) |
| Cluster cohesion | Whether structurally-clustered nodes are also geometrically clustered | Same idea as PCB "modularity-based clustering" — see Hu, Markov & Kahng 2020 |
| Flow direction conformity | Fraction of edges going left→right (or top→bottom) | EDN "Make schematic symbols understandable"; classic EE convention |
| Node overlap | Hard constraint (=0) | Any overlap kills readability |
| Aspect ratio fit | Bounding box vs page | A4 landscape ~ 1.41:1; tall sprawls waste page |

Three pragmatic observations from the literature worth highlighting:

1. **Orthogonal crossings have a limited impact on readability** when crossings
   are explicitly drawn at 90° with no junction dot (Zink–Walter–Baumeister–Wolff,
   *Computational Geometry* 2022). So spending O(n²) effort to eliminate the
   last crossing is wasted; spending O(n) to clarify each crossing isn't.
2. **Mental-map preservation matters as much as the absolute metric**
   (Misue, Eades, Lai, Sugiyama, *JVLC* 1995). Two layouts that score equally
   on aesthetics can still differ enormously in user-experience if one of them
   reshuffles the whole sheet after a small DSL edit.
3. **Schematic conventions outrank graph-drawing metrics** when the two
   disagree. Power on top, ground on bottom, input on left, output on right
   are conventions every EE expects; a layout that minimizes crossings but
   violates them looks "wrong" even if it scores well.

The MLCAD 2022 "Building Block Classification" paper from NYCU/NCTU formalizes
this for analog: their metrics are *building-block compliance rate*, *wire
crossings*, *wire bends*, and they argue these together capture what an analog
designer means by "looks right".

---

## 3. Placement algorithms

### 3.1 Force-directed

**Family + key idea.** Treat the netlist as a physical system: net edges as
Hookean springs, all pairs of nodes as Coulomb-repulsive. Iterate to energy
minimum.

Canonical variants:

- **Eades (1984), the original spring embedder.**
- **Kamada–Kawai (1989)** — energy minimization with stress = squared
  difference between graph-distance and geometric-distance.
- **Fruchterman–Reingold (1991)** — what `networkx.spring_layout` implements;
  what klicad-python uses today.
- **FADE / FM³ (Hachul–Jünger, 2004; Walshaw, 2003)** — multilevel
  coarsening for large graphs; O(n log n) per iteration.

Cheong & Si's 2020 survey *Force-directed algorithms for schematic drawings
and placement* (Information Visualization, also arXiv:2204.01006) catalogs
~50 years of variants across aesthetic graph drawing, VLSI HLS scheduling,
information visualization, and biological networks. They categorize them
into accumulated-force, energy-minimization, and combinatorial-optimization
families, with hybrid multilevel and multidimensional-scaling variants.

**Cost.** Naive Fruchterman–Reingold is O(I·n²) for I iterations. With
Barnes-Hut octrees, O(I·n log n).

**Quality.** Excellent at *clustering* — densely-connected nodes pack
together. Bad at *direction* (the energy is rotation-invariant). Bad at
*overlap* unless you bolt on hard non-overlap constraints (Dwyer's
constrained graph layout work).

**Strengths.** Black-box: only requires an edge list. Naturally handles
weighted edges (klicad-python already uses this — `INTRA_CLUSTER_BONUS`).

**Weaknesses.** No port awareness, no port-side awareness, no signal flow.
Initial-condition sensitive. Coulomb repulsion blows up bounding box
without bounding constraints. The shrink-clusters / page-fit fixups in
`_layout.py` are exactly the workarounds the literature predicts.

**Difficulty.** Already done; tuning weights is 1–2 person-weeks of work
per significant improvement.

**Implementations.**
- `networkx.spring_layout` (Python, BSD, mature)
- `graphviz neato / sfdp` (C, EPL, mature; sfdp is multilevel)
- `igraph` Kamada–Kawai (Python/C, GPL, mature)
- `ogdf` (C++, GPL/proprietary; high-quality FR + multilevel FM³)

**References.**
- Eades 1984. *A heuristic for graph drawing.*
- Fruchterman & Reingold 1991. *Graph drawing by force-directed placement.* Softw. Pract. Exper.
- Kamada & Kawai 1989. *An algorithm for drawing general undirected graphs.* IPL.
- Hachul & Jünger 2004. *Drawing large graphs with a potential-field-based multilevel algorithm.* GD '04.
- Cheong & Si 2020. *Force-directed algorithms for schematic drawings and placement: A survey.* Inf. Vis.

**EDA-specific notes.** No native handling of pin orientations or
left-input/right-output. Power/ground excluded from the spring graph
(klicad-python already does this); otherwise they over-couple.


### 3.2 Layered / Sugiyama with ports

**Family + key idea.** Four passes: (1) cycle removal, (2) layer assignment,
(3) within-layer crossing minimization (barycentric / median / two-phase),
(4) horizontal coordinate assignment. Produces a directed-flow diagram.

The **port-constrained extension** is what makes this approach uniquely
appropriate for schematics. Spönemann et al. (KIELER project, U Kiel, 2009–
present) extended the Sugiyama framework to support:

- Fixed-side ports (north/south/east/west).
- Port groups (a set of ports that can shuffle within themselves but stay
  contiguous).
- Hyperedges (nets with multiple terminals — exactly our case).
- Compound graphs (hierarchical groups — also our case).

The full extended algorithm is **KLay Layered**, productised as the
**Eclipse Layout Kernel (ELK)** — the layout engine behind Eclipse modeling
tools, Sirius, the Ptolemy II actor-oriented programming environment, and a
number of hardware schematic tools (Yosys/Netlistsvg uses ELK's JavaScript
port via `elkjs`).

**Cost.** O((|V|+|E|) log |E|) with Eiglsperger–Siebenhaller's 2004 efficient
implementation. Most subproblems are NP-hard (cycle removal, crossing
minimization, layer assignment with min-bends are all NP-hard), but
practical heuristics are linear- or log-linear-time per pass.

**Quality.** Produces clean L-to-R or T-to-B flows with rectangular
clustering. Crossings are *much* fewer than a force-directed layout when
the graph has any layered structure (i.e. all signal-flow schematics).
KLay Layered with port constraints + hyperedges + compound graphs is, as
far as I can tell from the literature, **state of the art for non-ML
schematic-like drawing**.

**Strengths.** Direction-aware (left input, right output by construction).
Port-side aware (so symbols can have their conventional pin sides). Handles
multi-terminal nets natively as hyperedges. Compound-graph support means
hierarchical sub-sheets get free rectangular treatment. ELK is open-source
(EPL) and battle-tested on actor-oriented schematic models.

**Weaknesses.** Requires you to direct the graph (klicad-python's current
"BFS from V/I sources" trick is one way; not the best). No symmetry
awareness. Doesn't know that two op-amp halves should be mirrored.

**Difficulty.** Rewriting current `sugiyama_positions()` to honor port
constraints + use median instead of barycenter is **3–5 person-weeks**.
Switching to ELK via `elkjs` or a Python wrapper is similar but mostly
integration work. Implementing KLay Layered fully from scratch is
**>10 person-weeks**.

**Implementations.**
- **ELK / KLay Layered** — Java, EPL. Mature; widely used. JavaScript port
  via `elkjs` works in Node/browser. No first-class Python binding (yet);
  callable via subprocess + JSON.
- `dagre` — JavaScript port of Sugiyama with limited port support.
- `graphviz dot` — original Sugiyama-style; no real port-side constraints.
- `OGDF SugiyamaLayout` — C++.

**References.**
- Sugiyama, Tagawa & Toda 1981. *Methods for visual understanding of
  hierarchical system structures.* IEEE T.SMC.
- Eiglsperger, Siebenhaller & Kaufmann 2004. *An efficient implementation
  of Sugiyama's algorithm for layered graph drawing.* GD '04.
- Sander 1994. *A fast heuristic for hierarchical Manhattan layout.* GD '95.
- Spönemann, Fuhrmann, von Hanxleden & Mutzel 2009. *Port constraints in
  hierarchical layout of data flow diagrams.* GD '09.
- Schulze, Spönemann & von Hanxleden 2014. *Drawing layered graphs with
  port constraints.* JVLC.
- Zink, Walter, Baumeister & Wolff 2022. *Layered drawing of undirected
  graphs with generalized port constraints.* Comp. Geom.
- Domrös et al. 2023. *The Eclipse Layout Kernel.* arXiv:2311.00533.

**EDA-specific notes.** This is the closest match for schematic drawing of
anything in the literature. Port constraints map directly to KiCad symbol
pins; port groups map to symbol units (e.g. dual op-amp); hyperedges map
to nets directly; compound graphs map to hierarchical sheets.


### 3.3 Min-cut partitioning

**Family + key idea.** Recursively bisect the netlist with a min-cut
heuristic; assign each subnet to a sub-area; recurse until subareas hold
one cell. Breuer 1977 introduced this; Kernighan–Lin 1970 and Fiduccia–
Mattheyses 1982 provide the swap heuristics.

**Cost.** FM is O(p) per pass on a netlist with p pins; multilevel
variants like hMETIS run in seconds on netlists of 100k cells.

**Quality.** Good at minimizing total wirelength on grid-floorplan IC
layouts. Less obviously applicable to schematics (no fixed die area; no
discrete grid for cells).

**Strengths.** Provably good wirelength bounds; deterministic; fast.

**Weaknesses.** Doesn't optimize bends, crossings, or directional flow.
Optimal cut on a 2-pair graph is often a cut perpendicular to readability.

**Difficulty.** 2–4 person-weeks to implement FM cleanly. Or just use
hMETIS / KaHyPar via Python bindings.

**Implementations.**
- **hMETIS** (Karypis lab, U Minnesota, free for research, mature).
- **KaHyPar** (Heuer & Sanders, Karlsruhe, MIT-licensed, very active 2018–
  present, has Python bindings).
- **PaToH** (Çatalyürek, OSU, free-for-research).
- `mt-metis` for graph partitioning (not hyper).

**References.**
- Breuer 1977. *A class of min-cut placement algorithms.* J. Des. Autom.
  & Fault-Tolerant Comp.
- Kernighan & Lin 1970. *An efficient heuristic procedure for partitioning
  graphs.* BSTJ.
- Fiduccia & Mattheyses 1982. *A linear time heuristic for improving
  network partitions.* DAC.
- Karypis & Kumar 1999. *Multilevel hypergraph partitioning: applications
  in VLSI domain.* IEEE T.VLSI.
- Schlag et al. 2016. *KaHyPar*, JEA + later papers.

**EDA-specific notes.** Hypergraph partitioning is the *correct* model for
netlists; the binary-projection trick klicad-python's `_partition.py`
currently uses (per its own docstring) loses information about
multi-terminal nets. Upgrading to KaHyPar is straightforward.


### 3.4 Simulated annealing

**Family + key idea.** Random local moves accepted with probability
exp(−ΔE/T); T decreases geometrically. TimberWolf (Sechen & Sangiovanni-
Vincentelli 1985, refined through 2000s) was the dominant IC placer for
~15 years.

**Cost.** Empirically O(n^1.33) for IC placement, with strong constant
factors. Tens of minutes for 1k-cell layouts; faster on small designs.

**Quality.** Will find *any* metric's optimum given enough cycles — useful
for cost functions that include symmetry, alignment, flow direction, *and*
crossings simultaneously.

**Strengths.** Trivially extensible cost function (just add a term).
No requirement that the cost be differentiable. Naturally handles discrete
moves (swap, rotate, mirror).

**Weaknesses.** Slow. Bad starts can take forever to escape. Highly tunable
(cooling schedule, move set) — that's a feature when expert-tuned, a bug
when shipped to other users.

**Difficulty.** Plain SA is 1–2 person-weeks. A *good* schedule for the
specific metric is 3–6 person-weeks of empirical tuning.

**Implementations.**
- TimberWolf source is academic (Yale; only fragments online).
- `simanneal` Python library (MIT, mature).
- DEAP toolbox (Python, LGPL).

**References.**
- Sechen & Sangiovanni-Vincentelli 1985. *The TimberWolf placement and
  routing package.* IEEE JSSC.
- Kirkpatrick, Gelatt & Vecchi 1983. *Optimization by simulated annealing.*
  Science.
- Sait & Youssef 1995. *VLSI Physical Design Automation* (book).

**EDA-specific notes.** SA is the right hammer when no clean structural
hint dominates (e.g. an interpretive analog circuit with mixed conventions).
For klicad-python it's most useful as a *post-pass* refinement on top of
a Sugiyama or force-directed seed.


### 3.5 Analytical placement

**Family + key idea.** Cast placement as a smooth optimization: half-
perimeter wirelength (HPWL) approximated by log-sum-exp, density as a
Poisson electrostatic field (ePlace), or as a PyTorch loss (DREAMPlace).
Solve with conjugate gradient or Nesterov.

**Cost.** GPU-friendly. DREAMPlace places 10M-cell IC designs in minutes.

**Quality.** Best-in-class for IC global placement on wirelength. Doesn't
care about bends or crossings or direction.

**Strengths.** Scales massively. Differentiable, so ML loss terms can be
mixed in.

**Weaknesses.** Designed for *continuous coordinate* IC floorplans, not
discrete grid schematics. Density-based; overkill for ≲100 components.
Implementation is heavy (PyTorch, CUDA, custom ops).

**Difficulty.** Reusing DREAMPlace as a library: 2–3 person-weeks of
integration but you're left with a wirelength-only objective. Adding bend
or symmetry terms: research-grade work.

**Implementations.**
- **DREAMPlace** (UT Austin / Duke, BSD, very active).
- **OpenROAD RePlAce** (gpl/bsd, in OpenROAD).
- **ePlace** (academic; folded into DREAMPlace).
- **Xplace** (CUHK, MIT, GPU).

**References.**
- Lu et al. 2015. *ePlace.* TODAES.
- Cheng et al. 2018. *RePlAce.* TCAD.
- Lin et al. 2019. *DREAMPlace.* DAC.
- Gu et al. 2020. *DREAMPlace 3.0.* ICCAD.

**EDA-specific notes.** Almost certainly the wrong tool at schematic scale.
Listed for completeness and because the loss-function framework (HPWL +
electrostatic density) is sometimes proposed as a substrate to *bolt
schematic aesthetics onto*.


### 3.6 ILP / SAT-based

**Family + key idea.** Encode placement as a mixed-integer linear program
or pseudo-Boolean SAT instance with explicit constraints and a single
combined objective.

**Cost.** Exponential in worst case; solvable to optimality for ≲30
components.

**Quality.** Provably optimal under the encoded objective — the only
approach that gives guarantees.

**Strengths.** Lets you express weird constraints exactly: "Q2 sits
directly above Q1", "R5 left-mirrors R6", "bus is broadside vertical".

**Weaknesses.** Doesn't scale. Encodings are bug-prone.

**Difficulty.** 4–8 person-weeks for a clean ILP encoding of placement +
crossings.

**Implementations.**
- Gurobi / CPLEX (commercial).
- CP-SAT in Google OR-Tools (Apache 2.0, free, very capable).
- Z3 (MIT).

**References.**
- Cohen-Steiner et al. 2003 on optimal orthogonal drawing (ILP). 
- HAL-04379729 (2023). *Path Length-Driven Hypergraph Partitioning: An ILP.*
  Demonstrates ILP applied to small EDA problems.

**EDA-specific notes.** Best fit as a **sub-solver** inside an otherwise
heuristic pipeline — e.g. "optimally place this 6-MOSFET differential
pair" while the rest of the layout uses Sugiyama.


### 3.7 Template / building-block

**Family + key idea.** Recognize textbook sub-circuits (current mirror,
diff pair, cascode, opamp Miller compensation, LDO regulator) and apply
a pre-designed visual template. Compose templates hierarchically.

**Cost.** O(n · |patterns|) for subgraph matching with reasonable pattern
counts.

**Quality.** The *only* approach that gives layouts an analog engineer
recognizes immediately.

**Strengths.** Best-possible match to convention. Naturally hierarchical.
Extensible (new patterns = new templates).

**Weaknesses.** Templates are labor-intensive to author. Coverage is the
single biggest issue; circuits with no matching template fall through to
the generic layouter.

**Difficulty.** Pattern matching engine: 2–3 person-weeks. Each template:
1–3 person-days. A useful library of ~30 templates (RC filter, voltage
divider, diff pair, current mirror, common-source, common-emitter, …):
8–12 person-weeks total.

**Implementations.**
- **ALIGN** (UMN/Intel, BSD, "Analog Layout, Intelligently Generated from
  Netlists"). Translates SPICE netlists to physical layout for IC; uses
  hierarchical pattern detection + constrained placement. arXiv:2008.10682.
- **BAG2 / Berkeley Analog Generator 2** (UC Berkeley, BSD). Python-based,
  parameterized generators. Process-independent.
- **LAYGO2** (KAIST, MIT). Template-and-grid for advanced CMOS.
- **MAGICAL** (UT Austin, BSD). End-to-end analog layout from netlist;
  symmetry detection + template-based placement + routing.

ALIGN's approach is *exactly* what would apply to klicad-python: detect
hierarchies in the SPICE netlist automatically, generate parameterized
cells, then assemble with symmetry/proximity constraints.

**References.**
- Kunal et al. 2020. *ALIGN: A system for automating analog layout.*
  arXiv:2008.10682.
- Chang et al. 2018. *BAG2: A process-portable framework for generator-
  based AMS circuit design.* CICC.
- Han et al. 2023. *LAYGO2: A Custom Layout Generation Engine.* TCAD.

**EDA-specific notes.** This is the analog answer. For digital, the
analogous idea is "datapath bitslice" extraction (see § 4.5).


### 3.8 ML and RL approaches

The field exploded after Mirhoseini et al.'s *Nature* 2021 paper and is
worth a careful distinction.

**(a) Mirhoseini et al. 2021 — RL for IC macro placement.** Trained an
agent to place IC blocks via REINFORCE on a reward = -(wirelength +
congestion + density). Claimed to beat human placement. Subsequently the
subject of one of the field's noisier disputes:

- Cheng et al. 2023 (later in CACM 2024). *The False Dawn: Reëvaluating
  Google's Reinforcement Learning for Chip Macro Placement.*
  arXiv:2306.09633. Argues the original results don't reproduce.
- Goldie et al. 2024. *That Chip Has Sailed: A Critique of Unfounded
  Skepticism.* arXiv:2411.10053. Counter-rebuttal.
- IEEE Spectrum 2024: "Ending an Ugly Chapter in Chip Design".

Both sides agree the method works for some block sets and fails for
others. **At schematic scale (≲200 components) RL training is wildly
disproportionate.**

**(b) GraphPlanner (Liu et al., TODAES 2022).** Variational GCN for IC
floorplanning. Hypergraph coarsening + spectral clustering → embedding →
init for a downstream placer. Improves DREAMPlace runtime by 25%.
Schematic-scale: again disproportionate, but the *idea* of using a GNN
embedding to seed a force-directed solver is appealing.

**(c) Optimization of Analog Circuit Placement via GNN (Y.H.S. 2024,
ICCAI).** GraphSAGE and GAT applied to analog placement. Reduces overlap
and wirelength.

**(d) Symmetry detection via GNNs.** This is the most schematic-relevant
ML thread:

- Hong et al. 2023. *Universal Symmetry Constraint Extraction for AMS
  Circuits.* DATE.
- Liao et al. 2023. *Graph attention-based symmetry constraint
  extraction.* arXiv:2312.14405.
- Wu et al. 2023. *Automatic Layout Symmetry Extraction for Analog
  Constraint Learning.* DAC.

These detect *what should be symmetric* in a netlist — a piece of
information you can feed into a classical placer. Roughly: train a GNN
on labeled-symmetric analog circuits, predict per-edge / per-node
symmetry constraints, hand off to a constraint-aware placer like
ALIGN. **This is feasible at schematic scale today** — inference is
cheap; training data is the bottleneck.

**(e) MLCAD 2022 RL for schematic generation.** Liu, Chen, Liu (NYCU/
NCTU). *Automatic Analog Schematic Diagram Generation based on Building
Block Classification and Reinforcement Learning.* Uses a building-block
classifier (CNN/GNN over SPICE netlist) + RL agent to place blocks with
a reward = -(crossings + bends − compliance_with_template). Reports
better building-block compliance and fewer crossings than commercial
tools on their benchmarks. Closest existing work to klicad-python's
problem.

**(f) Schemato (Matsuo et al. 2024).** arXiv:2411.13899. Fine-tunes an
LLM on netlist→LTSpice-asc pairs. 76% compilation success;
1.8×–4.3× accuracy improvements over GPT-4o. Limitations: scales to
small circuits only; LLM-style hallucinations of pin names; quality
ceiling appears to be ~human-novice.

**(g) PCBSchemaGen / AnalogMaster / CircuitLM (all 2025).**
Multi-agent LLM frameworks producing netlist + schematic + (sometimes)
PCB. Quality reports are early.

**(h) SmartGD (Wang–Yen 2023).** GAN-based generic graph drawing
optimizing arbitrary aesthetic goals. Not schematic-specific but a
general substrate for "I have a custom metric I want optimized".

**Cost.** Training: hours to days on GPU. Inference: seconds.

**Quality.** Good when training data matches deployment data; brittle
otherwise. Mirhoseini-style RL produces non-human layouts that may or
may not be readable.

**Difficulty.** Training a useful GNN symmetry detector from scratch
including dataset: **6–12 person-weeks**. Using a pretrained Schemato/
PCBSchemaGen if open-sourced: **2–4 person-weeks**. Building a custom
RL pipeline: **research project, 6+ months**.

**EDA-specific notes.** For klicad-python the realistic ML angle is
**symmetry-constraint detection** feeding into a Sugiyama-with-ports
placer, **not** end-to-end learned placement.

---

## 4. Partitioning, clustering, structural recognition

### 4.1 Modularity / Louvain / Leiden

- **Louvain** (Blondel et al. 2008). Greedy modularity maximization.
  Near-linear time. klicad-python uses this.
- **Leiden** (Traag et al. 2019). Guarantees well-connected communities
  (Louvain can produce disconnected ones, even though klicad-python's
  small designs rarely trip this). arXiv:1810.08473.
- **Markov Clustering / MCL** (van Dongen 2000). Random-walk-based.

UCSD's Hu, Markov & Kahng 2020 (*"On the superiority of modularity-based
clustering for determining placement-relevant clusters"*, Integration)
demonstrates modularity clustering specifically outperforms cut-based
clustering for finding *placement-relevant* groups in netlists. Direct
endorsement of klicad-python's current approach.

Drop-in upgrade: switch `louvain_communities` to a Leiden implementation
(igraph has one) for guaranteed-connected blocks.

### 4.2 hMETIS / PaToH / KaHyPar

These are hypergraph cut-based partitioners. Better than graph-projection
Louvain when nets have many terminals (a 5-terminal bus is one hyperedge,
not C(5,2)=10 graph edges).

| Tool | Author | License | Maturity | Python binding |
|---|---|---|---|---|
| hMETIS | Karypis | Free for research | 1998, mature, no longer active | Limited |
| PaToH | Çatalyürek | Free for research | 1999, mature | None official |
| **KaHyPar** | Heuer & Sanders | MIT | 2016, actively developed | Yes (`kahypar` on PyPI) |
| Mt-KaHyPar | Heuer et al. | MIT | 2020, parallel | Yes |

**Recommendation if upgrading from Louvain:** KaHyPar.

### 4.3 Spectral / Laplacian clustering

Eigenvectors of the graph Laplacian (or hypergraph normalized Laplacian)
give a continuous embedding that k-means can cut. Foundational; scales
poorly past ~10k nodes; not relevant at schematic scale, but spectral
embedding is sometimes used as a *seed* for force-directed or analytical
placers (GraphPlanner does this).

### 4.4 Symmetry and pattern detection

Two literatures:

**(a) Pattern-matching, rule-based.** HiLSD (Bhattacharya, Jangkrajarng,
Shi 2005, *Hierarchical extraction and verification of symmetry
constraints*). Subgraph isomorphism against a library of analog motifs.
Practical, brittle to topology variants.

**(b) Learned.** GNN-based detectors (§ 3.8(d)). Generalize across
topology variants. Need training data.

For klicad-python the (a) approach is implementable today; (b) is a
project of its own.

### 4.5 Signal flow, datapath/control extraction, bus identification

- **Signal flow** = identifying input and output ports per
  block/sub-circuit and orienting the DAG. For VLSI, this is the
  "timing graph". For klicad-python, you can mark sources (V/I parts,
  hierarchical input pins) and sinks (output pins, terminations) and
  orient edges by BFS distance — what `_layout.py` already does.

- **Datapath bitslice extraction.** Cadence Innovus has an SDP feature;
  DataPathLAYOUT and Buddi 2018 (*Layout Synthesis for Datapath
  Designs*, PSU thesis) describe it. The idea: a 16-bit ALU contains 16
  geometrically-identical bitslices; recognize them and tile vertically.
  Applies (rarely) to schematics with parallel structures (DAC ladders,
  bit-parallel registers).

- **Bus identification.** Wires with similar names (D[0]..D[7]) collapsed
  to a broadside bus on the schematic. Hard to do generically without
  naming hints; trivial if the DSL marks them as buses up front.

---

## 5. Routing algorithms

### 5.1 Maze / Lee / Hadlock / A*

- **Lee 1961.** Wave propagation BFS on a grid. O(grid size); optimal
  paths but big memory.
- **Hadlock 1977.** "Detour-number" — a Manhattan-distance-aware variant
  that's roughly A* with a Manhattan heuristic.
- **Soukup 1978.** Line search — much faster than Lee's BFS, less optimal.
- **A\*** (Hart, Nilsson, Raphael 1968) with admissible Manhattan
  heuristic is the modern default; what klicad-python uses.

### 5.2 Rectilinear Steiner trees

Hanan 1966 proved the optimum rectilinear Steiner tree has all Steiner
points on a grid through the terminals (the *Hanan grid*) — exactly what
`_route.py` exploits. Construction:

- **Sequential / shortest-path Steiner tree** (heuristic, ≤2-approximation,
  what klicad-python uses).
- **Iterated 1-Steiner** (Kahng & Robins 1992): better quality.
- **FLUTE** (Chu & Wong 2008): fastest near-optimal RSMT in practice.
  Free C source; widely used in IC routing.
- **GeoSteiner** (Juhl et al. 2018): proven-optimal RSMT for ≲500 terminals.
  GPL.

### 5.3 Channel and switchbox routing

Pre-Hanan-era technique: rectangular "channels" between rows of cells,
routed track-by-track. Mostly historical now but conceptually relevant —
SKiDL's router uses channel-style switchbox cells between blocks
(Knoll, 2023 blog post). Not a great match for schematics where
pin positions are fixed by symbols.

### 5.4 Rip-up and reroute (RRR)

When a net fails to route, *rip up* the offending neighbor and try again
with rerouting cost incentives. Used in essentially every modern global
router (TritonRoute, FastRoute, NCTU-GR). Negotiated-congestion variants
(Pathfinder/CGR, McMurchie & Ebeling 1995) iteratively inflate cost of
congested resources until convergence.

klicad-python today **does not** rip up — when A* fails it falls back to
labels. Adding a 2-pass RRR would reduce label fallbacks significantly.

### 5.5 Orthogonal hyperedge routing for schematics

Sass et al. 2021, *Interactive, Orthogonal Hyperedge Routing in
Schematic Diagrams Assisted by Layout Automatisms* (Springer). Builds
trees that share segments where two terminals run parallel — exactly the
"shared bus" pattern. References KIELER and ELK.

The "junction-aware merging" issue in `_route.py` (the careful hub-not-
on-pin logic) is the schematic-specific version of this problem; the
general algorithm in this paper handles it more cleanly.

---

## 6. Higher-level structure: orthogonal drawing & TSM

The **topology-shape-metrics (TSM) framework** (Tamassia 1987) is the
graph-drawing community's answer to schematic-like layouts:

1. **Topology**: planarize the graph, choose a planar embedding.
2. **Shape**: compute orthogonal shape with minimum bends — Tamassia
   proved this is solvable via min-cost flow in O(n²) for a fixed
   embedding.
3. **Metrics**: assign coordinates to fix edge lengths.

For schematics this is *too constrained* (it minimizes bends rather
than honoring symbol pin orientations) — yWorks' yFiles orthogonal
layouts are based on TSM and are used for many schematic-like diagrams,
but they pay for that with weaker port handling.

A more pragmatic recent paper:

- Bekos et al. 2025. *A Walk on the Wild Side: a Shape-First Methodology
  for Orthogonal Drawings.* arXiv:2508.19416. Inverts the TSM pipeline;
  starts from a shape and computes topology to match. Better
  port-constraint conformance.

---

## 7. Incremental and interactive layout

A schematic from a DSL is regenerated every time the DSL source changes.
Without incremental layout, even a one-line edit reshuffles the entire
sheet → mental-map destruction → user frustration.

Key references:

- Misue, Eades, Lai & Sugiyama 1995. *Layout adjustment and the mental
  map.* JVLC. Defines the mental map and gives a method to preserve it.
- North 1996. *Incremental Layout in DynaDAG.* GD '96. Sugiyama-with-
  position-preservation.
- Dwyer et al. 2009. *Constrained graph layout.* Adds hard constraints
  (alignment, separation, non-overlap) to force-directed layout.
- Dwyer, Marriott, Wybrow 2008. *Topology-preserving constrained graph
  layout.*
- Brandes et al. 2007. *Bayesian paradigm for dynamic graph layout.*

Practical implementations:

- **yFiles / yEd "incremental layout"** — commercial, the gold standard.
- **cola.js** (Tim Dwyer, MIT) — constraint-based force-directed; supports
  hard positional constraints, alignment, non-overlap. Python port:
  `cola-py` (less active).
- **ELK incremental mode** — re-layout with previous positions as soft
  init.

For klicad-python, the right primitive is:

1. Stable refs (the DSL already gives us this).
2. Persist last-known coordinates per ref in the `.kicad_sch` (KiCad
   does this automatically — they're in the file).
3. On regeneration, use last-known coords as soft init.
4. Add anchor / region-lock annotations to the DSL: `@anchor("R5", x, y)`,
   `@region("opamp_block", parts=...)`.

This is closer to a CRDT/diff problem than a layout problem.

---

## 8. Analog vs digital vs mixed conventions

- **Analog**: symmetry-dominated. Differential pairs, current mirrors,
  cascodes. Power top, ground bottom, signal L→R. Visual = match-this-
  topology. Best fit: template-based (§ 3.7) + symmetry GNN (§ 3.8(d))
  + Sugiyama-with-ports (§ 3.2).
- **Digital**: flow-dominated, bus-heavy. Buses broadside, registers
  aligned, control above datapath. Best fit: Sugiyama-with-ports + bitslice
  detection + bus broadside.
- **Mixed-signal**: hardest. Analog subsections need symmetry; digital
  needs flow. Realistic answer: partition into analog/digital sub-sheets
  (§ 4) and apply the right algorithm to each.

Schemalyzer's 30-rule guide and EDN's "Make schematic symbols
understandable" together codify the human conventions:

- Input left, output right.
- Ground bottom, positive supplies top, negative supplies bottom.
- Bypass capacitors close to the power pin.
- Feedback paths drawn right-to-left.
- 90° crossings without junction dots.
- ≥2 grid units between pins/junctions.

These are mostly *post-placement* concerns: they constrain how a placer
*should* lay things out, not how it does.

---

## 9. Existing KiCad-ecosystem schematic layout tools

| Tool | Approach | Quality | License | Notes |
|---|---|---|---|---|
| **KiCad native** (9.0 / 9.1 / nightly) | None at schematic level; PCB has spread-by-net | n/a | GPL | KiCad 9 added sheet-pin sync, no schematic auto-placement. PCB has "spread" and "place by hierarchy" but those are PCB-only. |
| **SKiDL** (Devbisme) | Force-directed FR + node grouping + switchbox routing | "Better than nothing" per author; ≲50 components | MIT | Most advanced KiCad-targeted automatic schematic generation. KiCad V5 schematic format only (no V6/7/8). 2 person-years of work; author called quality the hard problem. |
| **atopile** | Task-by-task PCB only; no schematic auto-layout | n/a | MIT | Explicitly chose to skip schematic auto-layout; treat .ato as the readable representation. |
| **circuit-synth** | KiCad project generation w/ Claude Code | Anecdotal | (check) | Bidirectional Python⇄KiCad. Uses kicad-sch-api for byte-exact output. |
| **hdl21 / VLSIR** | Hardware DSL targeting SPICE + various; no schematic layout | n/a | MIT | Same philosophy as atopile — the DSL is the document. |
| **kicad-kbplacer** | Keyboard-specific PCB placement | Good in scope | MIT | Limited domain. |
| **Yosys + netlistsvg** | digital netlist → SVG via ELK | Good for small digital | ISC | Uses `elkjs`. Real-world example of ELK on schematics. |
| **gschem / lepton-eda** | None | n/a | GPL | gEDA family; no automatic layout. |
| **qucs / qucsator** | None | n/a | GPL | Manual placement only. |
| **dia / drawio** | Generic diagram orthogonal routing | Decent | GPL/MIT | Not netlist-aware. |
| **Logisim Evolution / Digital** | None for placement; manual | n/a | GPL | Educational, manual layout. |

Commercial (closed-source) for completeness:

- **Cadence Virtuoso Schematic Editor**: pattern recognition + analog
  templates; closest commercial match to what klicad-python wants.
- **Mentor / Siemens Xpedition**: manual + heuristic snap.
- **Altium Designer**: manual + "auto-junction" only.
- **OrCAD Capture**: manual.
- **Synopsys Custom Compiler**: like Virtuoso; constraint-driven.

**Key takeaway: no open-source tool currently solves the schematic
auto-layout problem well.** SKiDL is the closest attempt and its author
explicitly calls the quality "the hard problem". This is genuinely
research-grade territory — but klicad-python is *already* further along
than SKiDL on partitioning and Sugiyama integration.

---

## 10. Multi-objective combination strategies

Three options for combining the metrics in § 2 into a single placement
cost function:

1. **Weighted sum.** Cost = α₁·crossings + α₂·bends + α₃·wirelength +
   α₄·symmetry_violations + ... Widely used; easy to tune; standard
   recommendation in *Engineering Design Optimization* (Martins &
   Ning 2022) is *against* weighted sums in general because the
   Pareto-frontier coverage depends discontinuously on weights — but
   for schematic layout the Pareto frontier is narrow enough that this
   isn't usually painful.

2. **Lexicographic.** Optimize crossings first, then break ties by bends,
   then by wirelength. KIELER does this internally (crossing min before
   bend min before coord assign).

3. **ε-constraint.** Optimize one objective subject to bounds on others.
   Hardest to implement; gives Pareto-front coverage. Probably overkill.

4. **Pareto evolutionary.** NSGA-II / SPEA2. Useful if you want to
   present multiple candidate layouts to the user. Overkill for the
   common case.

**Recommendation:** weighted sum with empirically-tuned weights, plus a
lexicographic tie-break inside each Sugiyama phase. Matches both the
classical EDA placement literature (TimberWolf-style) and the
graph-drawing community (KIELER).

---

## 11. Gaps and open problems

Honest uncertainty across the survey:

- **No open-source tool combines symmetry detection + port-aware
  Sugiyama + Steiner routing**. Each piece exists separately; gluing
  them is novel work.
- **Schematic-readability metrics are not as well-validated as the
  graph-drawing community pretends.** Purchase 1997 studied small
  graphs; nobody has done a psychometric study on real-world circuit
  schematics that I can find.
- **Training data for symmetry-detection GNNs is closed.** The MAGICAL
  and ALIGN teams use private corpora; the published datasets are tiny.
- **ML chip placement remains controversial.** Mirhoseini/Cheng dispute
  notwithstanding, the schematic-scale ML literature (Schemato,
  Building-Block RL) is too thin to recommend in production today.
- **ELK is the obvious right tool but is Java.** Calling it from Python
  is a Real Engineering Project — not impossible, but a project.
- **Recent (≥2024) LLM-based approaches (Schemato, PCBSchemaGen,
  AnalogMaster, CircuitLM) are too new to evaluate.** All claim wins;
  none have independent reproduction yet.
- **Hierarchical sheet decomposition driven by Louvain is folk
  knowledge.** klicad-python's current heuristic
  (`MAX_BOUNDARY_RATIO=0.3`) is plausible but I cannot find a study
  that validates that specific threshold.

---

## 12. Recommendations for `Circuit.to_schematic()`

Four candidate strategies, in increasing order of investment.

### Strategy A — Low-effort: tighten and instrument what exists

Estimated effort: **1–3 person-weeks.**

1. Replace Louvain with **Leiden** (igraph) for guaranteed-connected
   blocks — drop-in swap.
2. Replace the barycenter heuristic in `_layout.py` with the **median
   heuristic** (Eades & Wormald 1994); ~10% fewer crossings on small
   graphs.
3. Add **2-pass rip-up and reroute** in `_route.py`: when A* fails for
   a net, temporarily relax forbidden_points for the most-blocking
   prior net, re-route both. Should cut the label-fallback rate
   significantly.
4. Add a **post-placement local annealing** pass: simulated annealing
   over swaps within layers and rotations of symbols, with cost =
   weighted_sum(crossings, bends, wirelength, flow_conformity). Use
   `simanneal`. ~100 iterations is enough for ≲50 components.
5. Introduce per-circuit **readability metrics in the SVG output** —
   logged stats so you can see whether changes improve or regress
   quality. Without this you're flying blind on every tuning attempt.

**Pros.** Almost no architectural risk; incremental wins. Each piece
helps independently. Improves observability.

**Cons.** Still doesn't solve sprawl. Still doesn't honor pin orientations.
No symmetry. No mental-map preservation across edits.


### Strategy B — Medium-effort: adopt ELK / KIELER via subprocess

Estimated effort: **6–10 person-weeks.**

1. Bundle **elkjs** (the JavaScript port of ELK) or vendor the Java JAR.
   Call from Python with JSON in / JSON out — same pattern klicad-python
   already uses for KliCAD C++ bindings.
2. Translate `Circuit` → ELK JSON: parts → nodes, pins → ports with
   north/south/east/west side hints from the symbol library, nets →
   hyperedges, sub-sheets → compound graphs. Apply ELK Layered with
   `nodePlacement.strategy=NETWORK_SIMPLEX`,
   `crossingMinimization.strategy=LAYER_SWEEP`,
   `edgeRouting=ORTHOGONAL`.
3. Translate ELK output → KiCad coords.
4. Use **KaHyPar** for the hierarchical-sheet decomposition step,
   replacing Louvain's binary projection with true hypergraph cut.
5. Keep `_route.py` as a fall-back for cases ELK's edge routing
   produces awkward results (or for sheets where you don't want
   ELK's full flow). Most cases will not need it.

**Pros.** Adopts the most mature open-source port-aware schematic
layouter. Solves port orientation, signal flow, and bus broadside in
one move. Compound graph support gives hierarchical sheets a clean
home.

**Cons.** Java dependency. ELK's defaults are tuned for software /
data-flow diagrams; tuning for schematic specifics (power on top,
mirror conventions) requires care. ELK doesn't know symmetry.


### Strategy C — Ambitious: ELK + symmetry detection + template library

Estimated effort: **3–6 months.**

All of B, plus:

1. **Rule-based symmetry detector**: scan netlist for differential-pair
   topologies (matched pair of NMOS or PMOS with shared source/gate
   geometry), current mirrors (diode-connected reference + mirrors),
   matched-resistor pairs (R = R, both ends shared). Emit ELK
   `symmetry_partner` annotations.
2. **Template library** for the dozen most-common analog motifs
   (RC filter, voltage divider, common-emitter, common-source,
   common-drain, common-collector, diff pair, current mirror,
   cascode, Miller comp, LDO, charge pump). Each template is a
   parameterized sub-layout that replaces ELK's generic placement
   inside the matched region.
3. **Convention-driven post-pass**: power rails to top of sheet, ground
   to bottom, sources on left, sinks (output labels, terminations) on
   right. Implementable as ELK constraints + post-translation.
4. **Mental-map preservation**: persist part coords in a sidecar
   `.layout.toml` next to the DSL source. On regenerate, use previous
   positions as ELK soft init. Add `@anchor` / `@region_lock` DSL
   annotations.

**Pros.** Best-in-class output for the schematic forms klicad-python's
users actually draw. Differentiates klicad-python from every other
KiCad-targeted DSL. Mental-map preservation removes the
"edit-DSL-and-everything-moves" pain.

**Cons.** Each template is engineering work. Symmetry detector
coverage will lag user expectations. Significant test burden.


### Strategy D — Research: GNN-augmented hybrid pipeline

Estimated effort: **6+ months, with no guarantee.**

All of C, plus:

1. Train a small GNN (GraphSAGE or GAT, 2–4 layers, ≲100k params) on
   labelled symmetric pairs from open analog corpora (CIRCUIT248 and
   the ALIGN benchmarks). Run inference at `to_schematic()` time to
   supplement the rule-based detector with topology-variant matches.
2. Optionally use SmartGD-style GAN refinement as a final aesthetic
   polish on top of the ELK output.
3. Optionally explore Schemato / PCBSchemaGen for the
   netlist-as-text input path if klicad-python ever wants to consume
   raw SPICE.

**Pros.** Pushes the field forward. Open contributions land in a
relatively empty space.

**Cons.** Dataset acquisition is the hard part. ML maintenance burden
is real. Risk of "demoware" — works on training corpus, fails on
novel circuits.

---

### Suggested path

Given that klicad-python is a single-maintainer project and the user's
stated goal is *complete picture*, I would strongly recommend **starting
with Strategy A** (1–3 weeks; immediate wins) and **planning toward
Strategy B** (ELK adoption; transformative for output quality). Strategy
C and D are appropriate medium- and long-term roadmap items.

The single highest-leverage change in Strategy A is the
**post-placement simulated annealing pass with a weighted-sum aesthetic
cost function** — because it allows iterating on the *cost function
weights* without rewriting any placer, and the empirical literature on
TimberWolf shows that for ≲50 components SA-on-a-decent-seed gets
within a few percent of optimal on typical cost functions.

---

## 13. Key references

### Foundational
- Eades 1984. *A heuristic for graph drawing.* Cong. Numer.
- Sugiyama, Tagawa & Toda 1981. *Methods for visual understanding of
  hierarchical system structures.* IEEE T.SMC.
- Tamassia 1987. *On embedding a graph in the grid with the minimum
  number of bends.* SIAM J. Comp.
- Fruchterman & Reingold 1991. *Graph drawing by force-directed
  placement.* Softw. Pract. Exper.
- Kamada & Kawai 1989. *An algorithm for drawing general undirected
  graphs.* IPL.
- Breuer 1977. *A class of min-cut placement algorithms.* J. Des.
  Autom. & Fault-Tolerant Comp.
- Fiduccia & Mattheyses 1982. *A linear-time heuristic for improving
  network partitions.* DAC.
- Lee 1961. *An algorithm for path connections.* IRE Trans. Elec. Comp.
- Hart, Nilsson, Raphael 1968. *A formal basis for the heuristic
  determination of minimum cost paths.* IEEE T.SSC.
- Hanan 1966. *On Steiner's problem with rectilinear distance.* SIAM J.
- Kirkpatrick, Gelatt, Vecchi 1983. *Optimization by simulated
  annealing.* Science.
- Sechen & Sangiovanni-Vincentelli 1985. *The TimberWolf placement and
  routing package.* IEEE JSSC.
- Misue, Eades, Lai, Sugiyama 1995. *Layout adjustment and the mental
  map.* JVLC.

### Surveys and handbooks
- Tamassia (ed.) 2014. *Handbook of Graph Drawing and Visualization.*
  Chapman & Hall/CRC.
- Cheong & Si 2020. *Force-directed algorithms for schematic drawings
  and placement: A survey.* Inf. Vis. arXiv:2204.01006.
- Yang, Qiao, Chen et al. 2025. *A review of automatic schematic
  generation techniques and their application to printed circuit
  boards.* Frontiers of Information Technology & Electronic
  Engineering 26(9):1534–1550.
- Sait & Youssef 1995. *VLSI Physical Design Automation.* IEEE Press.

### Modern (port-aware schematic-relevant)
- Spönemann et al. 2009. *Port constraints in hierarchical layout of
  data flow diagrams.* GD '09.
- Schulze, Spönemann & von Hanxleden 2014. *Drawing layered graphs with
  port constraints.* J. Vis. Lang. Comp.
- Zink, Walter, Baumeister, Wolff 2022. *Layered drawing of undirected
  graphs with generalized port constraints.* Comp. Geom.
  arXiv:2008.10583.
- Domrös et al. 2023. *The Eclipse Layout Kernel.* arXiv:2311.00533.
- Sass et al. 2021. *Interactive, orthogonal hyperedge routing in
  schematic diagrams.* SDL Forum.
- Bekos et al. 2025. *A walk on the wild side: a shape-first
  methodology for orthogonal drawings.* arXiv:2508.19416.

### Partitioning
- Blondel et al. 2008. *Fast unfolding of communities in large
  networks.* J. Stat. Mech. (Louvain)
- Traag, Waltman, van Eck 2019. *From Louvain to Leiden.* Scientific
  Reports. arXiv:1810.08473.
- Karypis & Kumar 1999. *Multilevel hypergraph partitioning:
  applications in VLSI domain.* IEEE T.VLSI. (hMETIS)
- Schlag et al. 2016 +. *KaHyPar* series.
- Hu, Markov, Kahng 2020. *On the superiority of modularity-based
  clustering for determining placement-relevant clusters.* Integration.

### Analog / template / ML
- Kunal et al. 2020. *ALIGN: A system for automating analog layout.*
  arXiv:2008.10682.
- Chang et al. 2018. *BAG2.* CICC.
- Han et al. 2023. *LAYGO2.* TCAD.
- Bhattacharya, Jangkrajarng, Shi 2005. *Hierarchical extraction and
  verification of symmetry constraints.* (HiLSD)
- Hong et al. 2023. *Universal symmetry constraint extraction for AMS
  circuits with GNNs.* DATE.
- Liao et al. 2023. *Graph attention-based symmetry constraint
  extraction for analog circuits.* arXiv:2312.14405.
- Mirhoseini et al. 2021. *A graph placement methodology for fast chip
  design.* Nature 594:207–212.
- Cheng et al. 2024. *Reevaluating Google's reinforcement learning for
  IC macro placement.* CACM. arXiv:2306.09633.
- Goldie et al. 2024. *That chip has sailed: a critique of unfounded
  skepticism.* arXiv:2411.10053.
- Liu, Lin et al. 2022. *GraphPlanner.* TODAES.
- Liu, Chen, Liu (NYCU/NCTU) 2022. *Automatic analog schematic diagram
  generation based on building block classification and reinforcement
  learning.* MLCAD '22.
- Matsuo et al. 2024. *Schemato: an LLM for netlist-to-schematic
  conversion.* arXiv:2411.13899.
- Wang & Yen 2023. *SmartGD: a GAN-based graph drawing framework for
  diverse aesthetic goals.* IEEE TVCG.
- PCBSchemaGen, AnalogMaster, CircuitLM — arXiv 2025, multi-agent LLM
  schematic generation frameworks.

### Placement infrastructure
- Lu et al. 2015. *ePlace.* TODAES.
- Cheng et al. 2018. *RePlAce.* TCAD.
- Lin et al. 2019. *DREAMPlace.* DAC.
- Kahng & Wang et al. 2020. *TritonRoute.*

### Routing
- Soukup 1978. *Fast maze router.* DAC.
- Hadlock 1977. *A shortest path algorithm for grid graphs.* Networks.
- Chu & Wong 2008. *FLUTE: fast lookup table based rectilinear Steiner
  minimal tree algorithm for VLSI design.* TCAD.
- McMurchie & Ebeling 1995. *PathFinder: a negotiation-based
  performance-driven router for FPGAs.* (Pathfinder/CGR)
- Pan et al. 2012. *FastRoute.* VLSI Design.

### Open-source tools and ecosystem (KiCad-adjacent)
- KiCad 9 release notes (kicad.org/blog/2025/02/Version-9.0.0-Released/).
- SKiDL (github.com/devbisme/skidl).
- atopile (github.com/atopile/atopile) — Discussion #881 on auto-layout.
- circuit-synth (github.com/circuit-synth/circuit-synth).
- kicad-sch-api (pypi.org/project/kicad-sch-api/).
- Eclipse Layout Kernel (eclipse.dev/elk).
- ALIGN (github.com/ALIGN-analoglayout/ALIGN-public).
- KaHyPar (github.com/kahypar/kahypar).
- DREAMPlace (github.com/limbo018/DREAMPlace).
- OpenROAD (github.com/The-OpenROAD-Project/OpenROAD).
- Yosys + netlistsvg (github.com/nturley/netlistsvg).
