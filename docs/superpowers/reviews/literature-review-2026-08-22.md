# Literature Review & Research-Gap Analysis

**Project:** Agentic Autonomous Factory Recovery System (Middleware Integration for Event-Driven FJSP Optimization)
**Date:** 2026-08-22
**Scope:** Review of all five approved specs against 41 papers. Recency: **31/41 papers from 2024–2026 (76%)**; the remaining 10 older works are load-bearing foundations (benchmark definitions, canonical encodings, constrained-generation precedents) without which the project's methods would be ungrounded.

---

## 1. Paper Inventory

### 1.1 Older crucial foundations (7)

| # | Reference | Role for this project |
| --- | --- | --- |
| 1 | Brandimarte, P. (1993). *Routing and scheduling in a flexible job shop by tabu search.* Ann. Oper. Res. 41, 157–183 | Source of the MK01 benchmark; defines the FJSP structure our schema encodes |
| 2 | Venturelli, D., Marchand, D.J.J., Rojo, G. (2015). *Quantum annealing implementation of job-shop scheduling.* arXiv:1506.08479 | Canonical time-indexed QUBO encoding with one-hot, precedence, and no-overlap penalties — Phase 4 §5 follows it directly |
| 3 | Carugno, C., Ferrari Dacrema, M., Cremonesi, P. (2022). *Evaluating the job shop scheduling problem on a D-Wave quantum annealer.* Sci. Reports 12:6539 | Demonstrates horizon-selection trap; validates seeding T from OR-Tools — adopted as CP-SAT-seeded horizon (Phase 4 §5.1) |
| 4 | Kurowski, K. et al. (2023). *Application of QAOA to JSSP.* EJOR S0377221723002072 (+ Kurowski et al. 2020 ICCS hybrid QA heuristic) | QAOA makespan surrogate on toy instances at simulator scale — precedent for our micro-instance simulation studies |
| 5 | Preskill, J. (2018). NISQ framing, Quantum 2:79 | Justifies quantum-as-research-layer positioning (Phases 4–5) |
| 6 | Gao, L. et al. (2016). *An effective hybrid GA and TS for FJSP.* Int. J. Prod. Econ. 174, 93–110 | MK01 = 40 reference table (~20 metaheuristics converge) grounding Phase 2 Tier-1 assertion |
| 7 | Aggoune, R., Deleplanque, S. (2023). *Solving the JSP: QUBO model and quantum annealing.* hal-04037312 | Survey of practical-constraint QUBO formulations; confirms setups/workers are absent from mainstream QUBO-JSSP |

### 1.2 Quantum / QAOA / hybrid optimization (2024–2026, 11)

| # | Reference | Key finding used |
| --- | --- | --- |
| 8 | Efficient encoding for JSP. (2024). Quantum Sci. Technol. 10.1088/2058-9565/ad9cba | Time-indexed variable explosion quantified (N/log₂N factor); justifies ≤32-binary ceiling + head/tail pruning |
| 9 | Fu, K. et al. (2025). *Solving FJSP based on quantum computing.* Entropy 27(2):189 | Recent FJSP-specific quantum formulation landscape |
| 10 | Schworm, P. et al. (2026). *Evaluation of QA-based algorithms for FJSP.* Procedia CIRP | Threshold problem sizes where annealers suffice vs hybrids required — Phase 5 shadow-node measurement operationalizes exactly this question |
| 11 | Lopez-Ruiz, M.A. et al. (2025). *A non-variational quantum approach to the JSSP.* arXiv:2510.26859 (IonQ Forte) | JIT-JSSP tardiness objective; variable-freezing to build 24–36 qubit sub-instances — direct methodological precedent for our micro-slice extraction; classical exponential scaling evidence |
| 12 | Thermodynamic significance of QUBO encoding. (2026). arXiv:2601.04402 | `(p_sum, p_pair)` penalty families with sharp feasibility transitions — basis of Phase 4 calibration sweep + separation criterion |
| 13 | Hybrid quantum-classical scheduling, AIS room scheduling. (2025). arXiv:2509.04808 | Real-world scheduling evaluated on annealer as a study, not production authority |
| 14 | Hybrid classical–quantum optimization of routing. (2026). arXiv:2604.01250 | "Quantum value lies in difficult subproblems rather than end-to-end replacement"; overhead/noise warnings — foundation of the shadow-branch architecture |
| 15 | Multi-objective rescheduling of JSSP using QA. (2024). ScienceDirect S2213846324001287 | Quantum rescheduling under machine failures exists only as offline study — gap confirmed |
| 16 | A review on QAOA and its variants. (2023). CERN/Indico review | "QAOA advantage not yet realized due to NISQ noise" — honest-comparison mandate |
| 17 | Hierarchical QAOA. (2026). Phys. Rev. A l5w7-x27x | Current adaptive-QAOA state of practice beyond our scope, cited for completeness |

### 1.3 LLM agents for scheduling & planning (Phase 3, 11)

| # | Reference | Key finding used |
| --- | --- | --- |
| 18 | Wang, Z. et al. (2025). *MASC: LLM-based multi-agent scheduling chain for FJSP.* Adv. Eng. Inform. 67:103527 | Closest existing system: 4-agent chain handling malfunctions + urgent jobs; LLM ranks algorithms (84–90%); fine-tuned via QLoRA — contrast point for prompted+validated design |
| 19 | Chang, E.Y., Geng, L. (2025). *ALAS: stateful multi-LLM agent framework for disruption-aware planning.* arXiv:2505.12501 | Disruption recovery needs ACID-like guarantees; history-aware local compensation beats global replanning — validates frozen-op minimal-disruption design |
| 20 | SagaLLM. (2025). Context management, validation, transaction guarantees for multi-agent LLM planning | Compensatory rollback mechanisms + rigorous independent validation protocols — blueprint for gate + verifier split |
| 21 | DynaSchedBench. (2026). arXiv:2605.27566 | First calibrated dynamic-scheduling benchmark for LLM agents; observability paradox; confirms benchmark scarcity our corpus addresses |
| 22 | *A survey on LLMs for scheduling.* (2026). Adv. Eng. Inform. S1474034626006385 | Field-level map: modeling vs algorithm-optimization strands; our middleware sits across both |
| 23 | Huang, J. et al. (2025). *Leveraging LLMs for efficient scheduling in HRC flexible manufacturing.* npj Adv. Manuf. 2:47 | Fine-tuned local LLM generating heuristic dispatching rules; real-time disruption context |
| 24 | Retrieval-augmented LLM-driven multi-agent optimization. (2025/26). IEEE 11207301 | RAG-augmented agents bounded by traditional optimizers — same division of labor as ours |
| 25 | *Multi-agent LLMs as evolutionary optimizers for scheduling.* (2025). Comput. Ind. Eng. S0360835225003432 | LLMs as operators over schedules, not schedulers — supports "LLMs never calculate" boundary |
| 26 | Wang, L. et al. (2024). *A survey on LLM based autonomous agents.* Front. Comput. Sci. 18(6) | Unified agent framework; evaluation-strategy taxonomy informing fidelity metrics |
| 27 | Du, S. et al. (2026). *Survey on the optimization of LLM-based agents.* ACM CSUR 58(9), arXiv:2503.12434 | Parameter-free optimization (prompt engineering + validation) as legitimate agent engineering |
| 28 | Ye, H. et al. (2024). *ReEvo: LLMs as hyper-heuristics with reflective evolution.* NeurIPS | LLM-guided heuristic generation — adjacent strand we deliberately exclude |

### 1.4 DRL dynamic rescheduling — competing paradigm (2024–2026, 8)

| # | Reference | Key finding used |
| --- | --- | --- |
| 29 | Lv, L. et al. (2025). *Schedule repair for flexible job shops under machine breakdowns by DRL.* Comput. Ind. Eng. 207:111256 | GAT-based repair, +50% over AOR heuristic; no feasibility certificate |
| 30 | Type-aware MADRL for FJSP under breakdowns. (2025). Robot. Comput.-Integr. Manuf. S0736584524002102 | Multi-agent MDP real-time repair; credit assignment via difference rewards |
| 31 | Pang, Z. et al. (2026). *Multi-relational graph RL for dynamic FJSP under breakdowns.* Appl. Soft Comput. 190:114587 | Topology-adapting policies after failures |
| 32 | Wu, R. et al. (2025). *Dynamic scheduling under machine breakdown using improved double DQN.* Expert Syst. Appl. | Breakdown-focused DQN lineage |
| 33 | *A DRL method for dynamic FJSP.* (2026). Springer s12293-026-00508-3 | Stochastic insertions + breakdowns as MDP; makespan focus |
| 34 | *Policy-based RL with action masking under uncertainty.* (2026). arXiv:2601.09293 | Petri-net action guards — nearest analogue to hard constraints, but learned not certified |
| 35 | *A production scheduling framework for RL under real-world constraints.* (2025). arXiv:2506.13566 | Taxonomy of industrial adaptations: stochasticity, transport, multi-objective |
| 36 | Didden, J.B.H.C. et al. (2024). *Enhancing stability and robustness in online machine shop scheduling.* EJOR | MAS negotiation for downtime — pre-LLM agentic baseline |

### 1.5 AIoT / event-driven middleware (Phases 1, 5, 3)

| # | Reference | Key finding used |
| --- | --- | --- |
| 37a | Villar, E. et al. (2024). *Architectures for Industrial AIoT applications.* Sensors 24(15):4929 | Edge/fog/cloud reference architectures; MQTT pub-sub as IIoT backbone |
| 37b | Amiri, A., Zdun, U. (2024/25). *Deployment architectures of MQTT brokers in event-driven IIoT* | Empirical broker-deployment latency trade-offs — grounds our single-broker dev topology |
| 38 | Ahmed, B.S. et al. (2025). *Smart manufacturing: MLOps-enabled event-driven architecture in steel production.* J. Syst. Software 230:112542 | Production EDA + learning-agent control loop with digital-twin correlation — closest industrial middleware pattern |
| — | Cabane, H., Farias, K. (2024). *On the impact of event-driven architecture on performance.* Future Gen. Comput. Syst. 153:52–69 | EDA performance characteristics (cited via 37b) |

### 1.6 Constrained LLM generation (G1 precedent, 3)

| # | Reference | Key finding used |
| --- | --- | --- |
| 39 | Microsoft. (2023). *Guidance: A guidance language for controlling LLMs.* GitHub | Template-based constrained generation ensuring LLM outputs conform to schemas; precedent for bounded-catalog safety pattern |
| 40 | Willard, B.T., Louf, R. (2023). *Efficient guided generation for large language models.* arXiv:2307.09702 | Finite-state-machine token masking guaranteeing schema-valid outputs; theoretical foundation for the discriminated-union catalog |
| 41 | Beurer-Kellner, L. et al. (2023). *Prompting is programming: A query language for large language models.* PLDI | LMQL: declarative constraints over LLM output structure; validates that constrained-output patterns improve reliability in tool-use settings |

*(Supporting non-paper sources: OR-Tools official scheduling recipes and or-tools-discuss forum for AddCircuit setup patterns; IBM CP Optimizer transition-distance docs; SchedulingLab fjsp-instances repository for MK01 artifact verification; HiveMQ UNS/EDA industry references.)*

---

## 2. Existing Solutions Summary

1. **DRL repair policies** dominate dynamic-FJSP-rescheduling research (2024–2026): fast, adaptive, but black-box, uncertified, distribution-sensitive, and dependent on clean structured disruption inputs.
2. **LLM multi-agent chains** (MASC 2025) handle semantic understanding and algorithm selection but ship without formal verification layers or transactional state guarantees.
3. **Generic agentic-planning frameworks** (ALAS/SagaLLM 2025) provide transaction-style rollback and validation but operate on abstract planning domains, not DB-committed manufacturing schedules.
4. **Quantum JSSP studies** (2015–2026) are consistently isolated solver benchmarks on simulators or annealers — never embedded inside an event-driven recovery pipeline with end-to-end latency accounting.
5. **Industrial AIoT middleware** delivers MQTT/EDA plumbing with ML inference but contains no agentic recovery semantics.

---

## 3. Research Gaps This Project Solves

**G1 — Verified agentic recovery (Phases 2–3).** No published system combines an LLM semantic layer with a closed strategy catalog, deterministic applier, pre-commit invariant gates, post-commit verifier, and append-only rollback for FJSP recovery. MASC lacks formal verification; ALAS/SagaLLM provide the guarantees but not for committed manufacturing schedules. This project operationalizes SagaLLM-style transaction semantics on top of a certifying CP-SAT engine. The bounded-catalog pattern draws on the broader constrained-generation literature — Guidance (Microsoft, 2023), Outlines (Willard & Louf, 2023), and LMQL (Beurer-Kellner et al., 2023) demonstrate that restricting LLM output to validated schemas dramatically improves reliability in tool-use settings; this project applies the same principle to manufacturing recovery, where the "schema" is a pydantic discriminated union over four strategy types.

**G2 — Narrative-to-structured disruption ingestion with fidelity measurement (Phase 3).** Every DRL/MAS/quantum rescheduling work assumes disruptions arrive structured. The Translation Agent + seeded ground-truth corpus + exact-match/non-degradation benchmarks fill an ingestion gap the field does not address.

**G3 — Explainable rescheduling with audit trail (Phases 2–3).** DRL policies cannot explain moves; append-only schedule versioning plus diff-grounded rationale generation gives operator-visible, constraint-cited explanations — absent from all surveyed competitors.

**G4 — Operational measurement of the quantum-classical scheduling threshold (Phases 4–5).** Schworm et al. (2026) and arXiv 2604.01250 pose the hybrid-splitting question abstractly: at what problem scale does a quantum solver add value? This project answers it operationally by embedding a config-gated shadow node inside a live recovery graph that measures feasible-rate, true-objective gap, and stage-level latency against CP-SAT on an identical fixture — with the classical engine provably unaffected. The contribution is the measurement infrastructure and the empirical data point, not the claim that quantum scheduling is competitive at this scale.

**G5 — Reproducibility infrastructure (all phases).** DynaSchedBench (2026) confirms benchmark scarcity for dynamic scheduling agents. Deterministic scenario construction, seed-pinned solver/agent execution, fixture hashing, and byte-regenerable publication manifests exceed current community practice.

**G6 — Integrated rich-constraint canonical model (Phase 1).** Worker flexibility (Hutter/Nouri), sequence-dependent setups (GASS-derived), materials/BOM, and telemetry live in one instance-scoped relational schema — a composite no single benchmark provides, built reproducibly from public data plus labeled synthetic augmentation.

---

## 4. Spec-by-Spec Positioning

| Spec | Standing against literature |
| --- | --- |
| Phase 1 | MK01 parser expectations verified against independent artifact (SchedulingLab repo); MQTT/EDA design matches 2024–2025 IIoT empirical literature |
| Phase 2 | AddCircuit/explicit-interval setups match OR-Tools' own documented patterns; MK01=40 target grounded in ~20-algorithm convergence tables (Gao 2016) |
| Phase 3 | Architecture sits precisely at the intersection MASC (agents for FJSP) and SagaLLM (transactional validation) occupy separately; fidelity metrics align with DyneSchedBench-era evaluation practice |
| Phase 4 | Encoding/objective/calibration choices each traceable to ≥2 primary sources (Venturelli 15; QST 24; 2601.04402; Carugno 22; Lopez-Ruiz 25); scale honesty consistent with all five |
| Phase 5 | Shadow-integration + honest-latency comparison directly implements the hybrid-evaluation program called for by arXiv 2604.01250 and Schworm 2026 |

## 5. Honest Limitations

- Micro-instance quantum results will not demonstrate advantage; they characterize integration behavior at simulator scale — consistent with, not contrary to, the surveyed consensus.
- Single-broker, single-instance deployment limits external validity versus fleet-scale IIoT studies (Amiri & Zdun).
- Prompted general-purpose LLMs may trail fine-tuned specialists (MASC's QLoRA SchedAgent) on ranking quality; the trade is generality + zero training data for validated structure.
