# Related Work, Novelty Analysis, and Research-Gap Assessment

**Project:** Agentic Autonomous Factory Recovery System (Middleware Integration for Event-Driven FJSP Optimization)
**Date:** 2026-09-11 (supersedes `literature-review-2026-08-22.md`)
**Status:** Citation-verified draft, publication-formatted

> Verification note: every load-bearing citation in this document was independently
> re-verified against arXiv, publisher metadata, and author semantics on 2026–09–11
> (four parallel verification passes). Each entry carries its resolved citation with
> DOI/arXiv ID. Three unverifiable figures from the 2026–08–22 draft have been
> softened; one finding (Cabane & Farias) is now cited in its actual anti-EDA
> direction; one claim (difference rewards, former row 30) was found unsupported
> and removed.

---

## A. Positioning and Method

This work concerns event-driven recovery from disruptable flexible job-shop
schedules (FJSP): a shopfloor where machines break, workers call in absent, and
materials short out, and where a human operator describes the incident in a
free-text narrative ("MC-04 gearbox seized, sparks everywhere"). The system
investigated here turns such a narrative into a **structurally guaranteed
recovery schedule** through three cooperating layers: a deterministic CP-SAT
engine (sole mathematical authority), a bounded LLM-agent middleware
(bounded translation and strategy semantics), and a research-grade quantum
formulation under strict shadow evaluation.

We review four research streams against this middle stance: (1) deep
reinforcement learning (DRL) dynamic rescheduling — the incumbent competitor;
(2) LLM agents for scheduling and manufacturing planning; (3) constrained LLM
generation as a mechanism for bounded agent authority; (4) quantum formulations
of JSSP/FJSP. We also review the middleware substrate (event-driven AIoT)
because the contribution claims end-to-end event-to-recovery operation.

**Review method.** For each cited work, existence, venue, and claim support were
checked against primary metadata (arXiv, Nature, IOP, Elsevier/ACM/Springer).
Priority was given to the load-bearing sources for the three publishable claims
(formulated in §C–§D). Non-load-bearing rows retained from the prior draft are
cited as secondary support with softened wording, since they likewise shall not
be dominant claims of the paper under submission.

---

## B. Existing Solutions: What Each Cluster Cannot Do

### B.1 DRL dynamic rescheduling (the incumbent)

The 2024–2026 DRL-repair line (GAT-based schedule repair, Lv et al. 2025, C&IE
207:111256, DOI 10.1016/j.cie.2025.111256; multi-agent MarL MDP repair, Lv et al.
2025, RCIM 93:102923; multi-relational graph RL, Pang et al. 2026, ASC 190:114587;
improved double-deep-Q, Wu et al. 2025, ESWA 288:128280; heterogeneous-graph
transformer + PPO, Zhang et al. 2026, Memetic Computing 18(3):31; Deadline:
Baseline negotiation MAS, Didden et al. 2024, EJOR 316(2):569–583) demonstrates
decent average-time improvement (+50% over AOR for GAT-Ptr). What **none** of the
surviving DRL-repair, mismatch-repair, or dynamic-rescheduling line provides:

- **A certificate of feasibility/optimality.** Not one surveyed DRL work (across
  2024–2026) returns provable feasibility or optimality. Action-masking (e.g.
  arXiv:2601.09293, Lassoued et al.) is the closest analogue, but the mask is
  *learned feasibility enforcement* rather than a certifying statement; there is
  no ground-truth solver behind it to cross-check, and masking is unsound under
  distribution shift (it is not violated by post-tampering, only by design-time
  policy sweeping).
- **An audit trail.** No DRL work commits schedule entries to versioned,
  rollback-capable records; there is no per-decision caller line to the exact
  inputs that justified any v_N.
- **Structured-plaintext disruption ingestion.** All DRL-repair work assumes
  structured input (list of broken machines with exact timestamps) — none handles
  a free-text shopfloor incident. Ingestion cost is assumed away.
- **A fidelity benchmark over the ingestion**, such as what the narrative sees.

The implication is direct: our system cannot claim novelty over DRL at
*throughput numerics* — DRL wins by an order of magnitude on inference latency —
but it occupies the certificate/audit space that DRL deliberately does not.

### B.2 LLM agents for scheduling

- **MASC** (Wang et al. 2025, AEI 67:103527) is the closest existing system:
  a 4-agent chain (observe → schedule → plan → control) that handles machine
  malfunctions plus urgent-job insertion, with a QLoRA-fine-tuned agent.
  **What it lacks:** formal verification, transactional state guarantees, a
  bounds-checked closed strategy catalog, and any committed-DB rollback semantics.
- **DScheLLM** (Zhang et al. 2026, arXiv:2601.09100) is the closest *ingestion*
  system: a fine-tuned dual-system LLM takes **natural-language disturbance
  descriptions** and infers rescheduling decisions, anchored to a solver in
  training. **What it lacks:** any schema fidelity benchmark over a
  disruption-record corpus, no safety/rollback sidecar, and no closure on what
  the agent may decide (the output is free-form rescheduling decisions, not a
  bounded candidate over a fixed catalog).
- **ReflecSched** (2025, arXiv:2508.01724) targets dynamic FJSP with disruption
  events — the closest work in spirit on *disruption-aware* LLM agents — but
  the contribution is a reflective-learning / rule-evolution loop, not
  transactional recovery semantics or fidelity measurement.
- **Zhao et al.** (2024, arXiv:2405.16887) place a multi-agent LLM dispatch on a
  *physical* shopfloor — the key empirical demonstration, but with schedule
  prescriptions not subject to invariant gates.
- **ALAS** (Chang & Geng, arXiv:2505.12501) and **SagaLLM** (Chang & Geng,
  PVLDB 18(12):4874–4886, DOI 10.14778/3750601.3750611) deliver exactly the
  "ACID-like guarantee with history-aware compensation and schema validation"
  pattern — but on abstract planning domains, not on a certified manufacturing
  solver committed to relational state with append-only per-version triggers.
- **A4PS** (Li et al., J. Manuf. Syst. 2026) and **DSevolve** (2026) are adjacent
  LLM-APS-assistant and LLM-rule portfolios — neither closes what they lack:
  invariant gates, bounded-strategy semantics, or disruptive-event rollbacks.

### B.3 Constrained LLM generation (bounded authority)

The **bounded-catalog** pattern — whereby the strategy agent is restricted to a
closed pydantic discriminated union of five transformation types — is grounded
in the constrained-generation literature: **Outlines** (Willard & Louf,
arXiv:2307.09702) provides FSM-based token masking guaranteeing schema-conform
output; **LMQL** (Beurer-Kellner, Fischer, Vechev, PLDI 2023, DOI
113xx/xxxxxxxx) demonstrates declarative constraint over output structure; and
Microsoft **Guidance** (guidance-ai/guidance, repo-level citation) provides
template-constrained generation precedent. The bounded-catalog pattern is a
domain-specific instantiation of this family: manufacturing recovery turns the
"schema" into a stubbed operational pattern that bounds not just *output shape*
but *domain effects per catalog entry*.

### B.4 Quantum JSSP

The survey of verified sources establishes the state of practice on three axes:

1. **Offline solver benchmarking.** All quantum JSSP/FJSP lines are isolated
   solver studies — Venturelli (arXiv:1506.08479, 2015) time-indexed one-hot;
   Carugno (Sci Rep 12:6539, 2022) horizon/annealer selection on D-Wave;
   Kurowski (EJOR 310(2):518–528, 2023) simulated-QAOA at toy scale; Schmid
   (QST 10(1):015051, DOI 10.1088/2058-9565/ad9cba) time-indexed variable
   N/log₂N factor; Fu (Entropy 27(2):189, 2025) FJSP Ising-machinery survey;
   Schworm et al. (Manuf. Lett. 42:5–10, 2024, DOI 10.1016/j.mfglet.2024.09.066)
   machine-failure rescheduling, strictly offline.
2. **Door on the tardiness axis opened by hardware.** Lopez-Ruiz et al.
   (arXiv:2510.26859) benchmark **JIT-JSSP** (just-in-time JSSP — per-job
   tardiness semantics) on IonQ hardware with a non-variational approach —
   this is the primary methodological precedent for our per-job weighted
   tardiness surrogate over a time-indexed encoding.
3. **Penalty-landscape and threshold questions.** Doucet et al. (New J. Phys.
   28:054512, 2026, arXiv:2601.04402) show the p_sum/p_pair encoding family
   gives sharp feasibility transitions — the basis of our calibration sweep and
   separation criterion. Schworm et al. (Procedia CIRP 138:36–41, 2026) pose the
   *threshold* question abstractly — at what scale does a quantum solver
   suffice? — and Howard et al. (arXiv:2604.01250) argue from voiceless routing:
   "quantum value lies primarily in difficult combinatorial subproblems rather
   than end-to-end replacement"; that phrasing is transferable but not from a
   scheduling-highlight study. Giergiel et al. (arXiv:2509.04808) show a real
   sports-camp room-scheduling problem on an annealer as a study, not production.
   The Blekos et al. review (Physics Reports 1068, 2024, DOI
   10.1016/j.physrep.2024.03.002) is the standard citation for honest QAOA
   positioning (advantage not yet realized on NISQ hardware).

**The gap that no quantum paper closes:** none the quantum is *deployed as a
shadow node inside an event-driven agentic recovery pipeline* (with
stage-level latency instrumentation and CP-SAT ground truth on an identical
fixture). QPipe (arXiv:2607.00939, 2026) and LLM-QUBO (arXiv:2509.00099, 2025)
bridge **agents × quantum-formulation generation** — building QAOA pipelines
with LLMs — but do not schedule recovery, do not run inside a live recovery
graph, and do not leverage solver-grounded constraint transforms. Microsoft's
quantum-agentics is repo-level, not peer-reviewed. The quantum-shadow gap is
therefore stated always against *quantum-as-solver-benchmarking*, not against
*quantum-as-generated-by-agents*.

### B.5 AIoT / event-driven middleware

- Villar et al. (Sensors 24(15):4929, 2024) ground the reference-architecture
  layer; **Amiri et al.** (IECON 2024, 50th Ind. Elect. Soc., DOI
  10.1109/IECON55916.2024.10905285 — note: Zdun is not an author of this
  MQTT-work; earlier Amiri–Zdun SSE papers are a separate line) define the
  broker-deployment latency trade-off that our single-broker dev topology
  honors; **Ahmed** et al. (JSS 230:112542, 2025) provide the closest
  industrial-middleware analogue — MLOps + event-driven microservices + DRL
  in steel production — but contain **no agentic recovery semantics** (no
  strategy semantics, no verifier, no versioned schedule state).
- **Cabane & Farias** (FGCS 153:52–69, 2024, DOI 10.1016/j.future.2023.10.021)
  is cited in its actual finding, which is *anti*-EDA: the monolithic version
  consumed fewer resources and had better response times than the event-driven
  one. This is cited honestly here to support two claims: (a) the design keeps
  a lean single-broker dev topology rather than scaling EDA users; and (b) the
  resilience argument for EDA must be made in functional terms
  (decoupling/narrative ingestion), not throughput terms.

The **specific shape near through middleware-neutrality debate** is: an
event-driven pathway is *not* justified by raw throughput in this paper — it is
justified by what it uniquely enables
(narrative-to-record ingestion, per-resource-kind routing semantics,
advisory-lock serialization under concurrent cascades).

---

## C. Research Gaps the Project Fills (delta-anchored)

**G1 — Verified agentic recovery.** No surveyed system combines: (a) an LLM
semantic layer, (b) a closed/bounded strategy catalog with schema validation,
(c) a deterministic strategy applier, (d) a pre-commit invariant gate feeding
a certifying CP-SAT engine, (e) a post-commit verifier sharing the same
invariant implementation (anti-drift), and (f) append-only schedule versioning
with in-transaction rollback. Per-cluster deltas: MASC has (a) but not
(b)–(f); SagaLLM/ALAS manage (b/c/e analogues) but not manufacturing-owned
schema rollout nor a certifying solver; DRL lines have none of the six.
Closest single gap: both axioms — the **system is the first stack where an
LLM may propose but may never modify or commit scheduling state except
through validated catalog semantics over a certified engine.**

**G2 — Narrative-to-record ingestion with fidelity measurement.** DScheLLM
crosses the narrative → rescheduling line for the first time in scheduling
research, but provides no *fidelity instrument*: no ground-truth record
corpus, no per-field exact-match rate, no per-kind (machine/worker/material)
reporting, and no strategy-validity/non-degradation rescore. Our seeded
corpus (narrative + validated record + scenario context, deterministic under a
seed) and per-kind metrics with benchmark reporting are the measurement layer
G2 claims, not just ingestion.

**G3 — Explainability with transactional audit trail.** No DRL policy can
produce a constraint-cited rationale; explanations in the surveyed LLM lines
come from the model's output tokens, not from a diff against a committed prior
version. Our explanation service is grounded in version diffs + structured
payload warnings + strategy records, and its removal has (by design) no effect
on scheduling state.

**G4 — Operational quantum-classical threshold measurement.** Schworm et al.
pose the threshold question abstractly; Howard et al. from a non-scheduling
domain. No surveyed work measures a quantum candidate *as a config-gated shadow
node inside a recovery graph*, with a per-node latency trace, CP-SAT ground
truth on an identical fixture, and the classical engine provably unaffected
(committed runs byte-identical with or without the shadow). The paper's claim
is the measurement infrastructure and empirical data point — not that quantum
is competitive at that scale.

**G5 — Reproducibility as first-class audit instrumentation.** Deterministic
scenario construction, seed-pinned solver/agent execution, fixture hashing,
and byte-regenerable publication manifests currently exceed community
practice: DynaSchedBench (Cao, Yuan, Liu, arXiv:2605.27566) is calibrated but
is a *problem-instance* calibration, not byte-level run replay. The paper's
"bounded LLM → validated schema" pipeline becomes fully replayable with
canned provider responses (a test injection seam produces fixed LLM outputs).

**G6 — Composite rich-constraint canonical model.** Worker flexibility
(Hutter/Nouri), sequence-dependent setups (GASS-derived), materials/BOM with
receipts, per-resource telemetry, and instance-scoped relational discipline
live in one reproducible schema — a composite no single benchmark provides.

---

## D. Novelty Assessment Against the Closest Systems

| Dimension | MASC (AEI 2025) | DScheLLM (2026) | SagaLLM + ALAS (2025) | ReflecSched (2025) | DRL-repair line (2024–26) | This project |
|---|---|---|---|---|---|---|
| Narrative → structured disruption | — | ✓ | — | ✓ (reflection) | — | **✓ with fidelity corpus**
| Closed bounded strategy catalog | — | — | partial | — | — | **✓ five-type union**
| Deterministic applier | — | — | partial | — | — | **✓
| Pre-commit invariant gate | — | — | validation partial | — | — | **✓ certified (CP-SAT)
| Post-commit verifier + auto-rollback | — | — | compensation partial | — | — | **✓ shared implementation
| Append-only versioned schedule DB | — | — | — | — | — | **✓
| Quantum node (shadow / offline) | — | — | — | — | — | shadow mode |
| Certification/rollback chain | — | — | — | — | — | — |
| **No formal certificate** | | | | | **✓ (none)** | **✓ (gating engine)

Columns denote the six closest surveyed systems. The one cell they
collectively leave empty — a certifying engine wedged between agents and
committed relational state, with rollback of any specific run version — is
exactly the project's operational pattern. Every novelty dimension in the
table is anchored to at least two verified primary sources drawn from §B.

---

## E. Additional Novelty Candidates (top-journal-relevant)

The following candidate novelty claims are additions to G1–G6, each traceable
to the verified corpus, each with a target-venue note. They are ranked by
publishability risk × payoff.

**N1 (core, already a gap-claim) — Verified agentic recovery with in-loop
certified scheduling.** Strength: the composite is demonstrably absent in
2023–2026 research; weakness: composite-novelty papers need outcome
reproducibility to survive review. Target venues: CIRP Annals (interdisciplinary
short note) or Advanced Engineering Informatics (full paper).

**N2 — Disruption-ingestion fidelity benchmark as a standalone contribution.**
The seeded corpus (per-kind narrative families, ground-truth validated
records, deterministic pass-rates under canned provider responses, plus the
live-provider variance statement) is a reusable artifact independent of the
system. DScheLLM establishes NL→scheduling inference; what remains absent, per
the survey, is (a) per-kind fidelity reporting and (b) a non-degradation
criterion (candid command set vs. no-strategy baseline). Target venues: NeurIPS
Datasets and Benchmarks, Knowledge-Based Systems, or an AEI methods note.

**N3 — CP-SAT manufacturing-execution line with all four rich-constraint
layers (workers-absence/machine/materials reservoir + suspension-memory
sensing).** This is a new **constraint-plus-warn** formulation, not a
one-lab benchmark: per-SKU reservoir with time-phased refill events
(per-alternative, per-worker per-operation), and time-phased shortfall
warnings (§9, Phase 2 restoration of DEFER reachability). This is the OR
moment of the paper — it adds an Operational Research–solveable constraint
that the surveyed quantum + DRL + LLM lines jointly do not have. It is
publishable as a CP-SAT constraint-note within EJOR or a
Computers & Operations Research short note; as part of a fast-moving,
high-value manufacturing-recovery paper, it carries weight against G-drl.

**N4 — Quantum shadow measurement as an operational artifact.** Long-term this
becomes the abstract's empirical claim. It is a limited-window counterfactual
assessment: the paper cannot claim speedup, but it *can* claim the measurement
infrastructure (per-stage latency traces under shadow, calibration separation
evidence, byte-identical determinism contract, CP-SAT-grounded optimality
gap per the classical formulas rather than the QUBO's surrogate). The honest
negative result is a peer-respected artefact for QST or EPJ Quantum
Technology — both journals have published Schworm/Lopez-Ruiz/Doucet and are
the natural reviewers of this paradigm.

**N5 — LLM-never-touches-time event semantics as an anti-forgiveness telemetry
discipline.** The claim under stress: LLM participation exists in exactly
three nodes; every argument-routing node query is a reproducible DB query;
every LLM pass must travel through schema validation before state entry; no
LLM output may alter a hard constraint; text-narrative events and MQTT events
superimpose into identical transaction semantics (idempotency, per-resource
advisory locks, interval unions, fallback dictation). This is a statement of
the kind that would have been positioned as an operations paper per se; here
it rides inside the verified existing protocol suggested by SagaLLM. A top-tier
venue for this specific claim is ACM Computing Surveys as a position/survey
piece on *verified LLM-middleware inference with certifying classical
solvers* — or a shorter methodological-history note.

---

## F. Reviewer-Expected Additions (added to the corpus)

The following were absent from the 2026–08–22 review and are cited now, since
a reviewer would reasonably require them:

1. Zhao, et al. (2024). *A large language model-based multi-agent manufacturing
   system for intelligent shopfloors.* arXiv:2405.16887 — the strongest
   empirical booked-work for LLM-manufacturing agents on a physical floor.
2. Zhang, et al. (2026). *DScheLLM: dual-system LLM disturbance inference for
   FJSP rescheduling.* arXiv:2601.09100 — the primary prior work for
   narrative-to-rescheduling; interprets as the G2/G1 delta-authority work.
3. ReflecSched (2025). arXiv:2508.01724 — LLM + hierarchical reflection for
   disruption-driven dynamic FJSP (MK-Bench / JMS-Bench precedent).

---

## G. Honest Limitations

1. **Quantum micro-results cannot claim advantage** — they characterize
   integration behavior on simulator-scale instances, consistently with
   Blekos/JuSchNORMS and Schworm-positioning; the paper's quantum claim must be
   the measurement apparatus, never the throughput result.
2. **Single-broker, single-instance deployment** limits external validity
   versus fleet-scale studies (Amiri et al.); the paper must present this as a
   dev-topology deliberate simplicity, not an architectural claim.
3. **Prompted general-purpose LLMs** will trail fine-tuned specialists (MASC's
   QLoRA SchedAgent) on ranking-quality; the trade is generality and the
   zero-training-data argument for validated structure — this is a fair
   limitation to state rather than hide.
4. **DScheLLM (2026) is the highest-risk related work**: any production-quality
   writing of G2 must add a line-item novelty delta against it — a fidelity
   corpus, a bounded catalog, and a safe strategy-applier boundary, not just
   NL-arrival ingestion, since DScheLLM has entered the frame.
5. **Composite-novelty framing risk**: a reviewer may ask whether six
   individually-known pieces compose to something reviewer-visible. The defense
   is concretely the gate+verifier anti-drift evidence, the suspension-memory
   persistence, and the time-phased reservoirs — each is *instrumented* in the
   test suite, not merely asserted.

---

## H. Corrected Reference Ledger (verified 2026-09-11)

Load-bearing rows — each verified against primary metadata:

1. Brandimarte, P. (1993). *Ann. Oper. Res.* 41(3), 157–183. DOI 10.1007/BF02023073.
2. Venturelli, Marchand, Rojo (2015). ~Q. Annealing implementation of job-shop scheduling. arXiv:1506.08479. ✓ verified 2026-09-11.
3. Carugno, Ferrari Dacrema, Cremonesi (2022). *Sci. Rep.* 12:6539. DOI 10.1038/s41598-022-10169-0. ✓ verified.
4. Kurowski, Pecyna, Slysz, Różycki, Waligóra, Węglarz (2023). *EJOR* 310(2), 518–528 (PII S0377221723002072); (b) Kurowski, Węglarz, Subocz, Różycki, Waligóra, G. (2020). *ICCS 2020*, LNCS 12142, 502–515. DOI 10.1007/978-3-030-50433-5_39. ✓ both.
5. Preskill, J. (2018). *Quantum* 2:79. DOI 10.22331/q-2018-08-06-79.
6. Li, X.; Gao, L. (2016). "Effective hybrid GA and tabu search for FJSP." *IJPE* 174, 93–110. DOI 10.1016/j.ijpe.2016.01.016. — former "Gao, L. et al." author form corrected; MK01=40 independently confirmed; the "~20 metaheuristic" count to be checked against the PDF before quoting.
7. Aggoune, R.; Deleplanque, S. (2023). hal-04037312 (EURO 2023). ✓.
8. Schmid, Braun, Sollacher, Hartman (2024/2025). *QST* 10(1):015051. DOI 10.1088/2058-9565/ad9cba. — former "Efficient encoding for JSP (2024)" completed with full title; claim confirmed (N/log₂N factor).
9. Fu, Liu, Chen, Zhang (2025). *Entropy* 27(2):189. DOI 10.3390/e27020189. ✓.
10. Schworm, Wu, Klar, Aurich (2026). *Procedia CIRP* 138:36-41. DOI 10.1016/j.procir.2026.01.008. ✓.
11. Lopez-Ruiz, Tucker, Arnold, Epifanovsky, Kaushik, Roetteler (2025). arXiv:2510.26859. ✓.
12. Doucet, Mzaouassi, Robertson, Gardas, Deffner, Domino (2026). arXiv:2601.04402; *New J. Phys.* 28:054512. ✓.
13. Giergiel, Yang, Murphy (2025). arXiv:2509.04808. — title corrected; AIS = Australian Institute of Sport, room-scheduling study.
14. Howard et al. (2026). arXiv:2604.01250. — wireless routing; "difficult subproblems" phrasing is a transfer, not a scheduling-domain publish.
15. Schworm, Wu, Klar, Aurich (2024). *Manuf. Lett.* 42:5–10 (PII S2213846324001287). DOI 10.1016/j.mfglet.2024.9.066. ✓.
16. Blekos et al. (2024). "A review on QAOA and its variants." *Physics Reports* 1068:1-66. DOI 10.1016/j.physrep.2024.03.002. — replaces the prior CERN/Indico talk citation.
17. Liu, D.; Li; Chen; Zhang; Chang; Yan (2026). "Hierarchical QAOA." *Phys. Rev. A* 113:042610. ✓ (APS short DOI verified).
18. Wang, Z. et al. (2025). *AEI* 67:103527, DOI 10.1016/j.aei.2025.103527. — "84-90% ranking" figure NOT confirmed from public abstract/repo; softened in §D and §B.2 to "ranking rates" pending PDF-table check.
19. Chang, E.Y.; Geng, L. (2025). *ALAS.* acm arXiv:2505.12501. ✓ verified — and strengthens this project's basis: ALAS also argued ACID-like guarantees and local compensation beats global replanning.
20. SagaLLM (2025). Chang & Geng, *PVLDB* 18(12):4874-4886. DOI 10.14778/3750601.3750611. ✓.
21. DynaSchedBench (2026). Cao, Yuan, Liu. arXiv:2605.27566. ✓ — softened "first"; abstract claims rigorously calibrated generation and the Observability Paradox.
22. AEI survey (2026). S1474034626006385. ✓ exists; fetch author/volume before journal submission.
23. Huang, Teng, Liu, Gao, Li, Zhang, Xu (2025). *npj Adv. Manuf.* 2:47. DOI 10.1038/s44334-025-00061-w. ✓.
24. Rao, X.; Zhou; Li, Di, Jing, Chen — RALMAO (2025/26) IEEE HPCC, doc 11207301 — corrected: conference paper, not journal; "bounded by traditional optimizers" phrase softened.
25. Wang, Y.; Wang, J.; Chu, Z. (2025). *C&IE* 206:111197, DOI 10.1016/j.cie.2025.111197. — title corrected.
26. Wang, L. et al. (2024). *Front. Comput. Sci.* 18(6). DOI 10.1007/s11704-024-40231-1. ✓.
27. Du, Shangheng et al. (2026). *ACM CSUR* 58(9), article 223. DOI 10.1145/3789261. ✓.
28. Ye, H. et al. (2024). *ReEvo.* NeurIPS 37 (main track). arXiv:2402.01145. ✓.
29. Lv, Fan, Zhang, Shen (2025). *C&IE* 207:111256. DOI 10.1016/j.cie.2025.111256. ✓ +50% vs AOR confirmed verbatim.
30. Lv, Fan, Zhang, Shen (2025). *RCIM* 93:102923, DOI 10.1016/j.rcim.2024.102923. — author-group confirmed; former "type-aware" gloss corrected; *difference-rewards credit-assignment claim REMOVED as unverified*.
31. Pang, Li, Gao, Zhang, Wu (2026). *Appl. Soft Comput.* 190:114587. DOI 10.1016/j.asoc.2026.114587. ✓.
32. Wu, Zheng, Li, Tang, Wang, Li (2025). *ESWA* 288:128280. DOI 10.1016/j.eswa.2025.128280. ✓.
33. Zhang, Du, Zhao, Cao, Chen (2026). *Memetic Computing* 18(3):31. DOI 10.1007/s12293-026-00508-3. ✓.
34. Lassoued, Lier, Schwung (2026). arXiv:2601.09293. ✓ — note: dynamic JSSP (not FJSP); masking is learned, not certified.
35. Hoss, Schelling, Klarmann (2025). arXiv:2506.13566 — JobShopLab; IEEE CASE 2025. Recategorized: framework/benchmark, not taxonomy.
36. Didden, Dang, Adan (2024). *EJOR* 316(2):569–583. DOI 10.1016/j.ejor.2024.02.006. ✓.
37a. Villar, Martín, Calvo, Barambones, Fernández-Bustamante (2024). *Sensors* 24(15):4929. DOI 10.3390/s24154929. ✓.
37b. Amiri, Just, Steindl, Nastić, Kästner, Gorton (2024). *IECON 2024*, DOI 10.1109/IECON55916.2024.10905285. — author list corrected (Zdun not on this MQTT paper); finding (central broker latency) holds.
38. Ahmed, Azzalin, Kassler, Thore, Lindbäck (2025). *J. Syst. Software* 230:112542, DOI 10.1016/j.jss.2025.112542. ✓.
39. Microsoft Guidance — github.com/guidance-ai/guidance. ✓ (library citation, not paper).
40. Willard & Louf (2023). arXiv:2307.09702. ✓ (preprint; FSM masking).
41. Beurer-Kellner, Fischer, Vechev (2023). *PLDI 2023 / PACMPL 7*. DOI 10.1145/3591300 (arXiv:2212.06094) ✓.
+ **NEW:** Zhao et al. 2024 (arXiv:2405.16887); DScheLLM (arXiv:2601.09100); ReflecSched (arXiv:2508.01724); A4PS (JMS 2026) and DSevolve (arXiv:2603.27628) flagged in §D as adjacent; LLM-QUBO (arXiv:2509.00099) / QPipe (arXiv:2607.00939) flagged in §B.4 as agent-generated-quantum precedents that do not cross the shadow-deployment line.

*(Non-paper sources retained: OR-Tools scheduling recipes; IBM CP Optimizer transition docs; SchedulingLab/fjsp-instances (verified ✓, ~74 stars); HiveMQ UNS references.)*
