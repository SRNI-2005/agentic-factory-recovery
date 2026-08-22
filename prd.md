# Project Requirements Document (PRD)

## Title: Agentic Autonomous Factory Recovery System

**Subtitle:** Middleware Integration for Event-Driven FJSP Optimization

---

### 1. Executive Summary & Objective

The objective of this project is to engineer a highly reliable, backend-heavy autonomous recovery system for the Flexible Job Shop Scheduling Problem (FJSP). Traditional manufacturing environments struggle with manual rescheduling during sudden disruptions. This system acts as intelligent middleware where an LLM-driven multi-agent architecture owns the semantic layer of recovery — translating messy disruption reports into structured records, proposing soft recovery strategies, and explaining committed schedules — while a dual-pipeline algorithmic backend (Classical CP-SAT and Quantum QAOA) remains the sole authority for mathematical computation, minimizing overall makespan and tardiness before the recovered schedule is committed back to the system.

### 2. Scope & Strict Exclusions

This project strictly focuses on system-engineering rigor, fault-tolerant state management, and deterministic orchestration.

* **In Scope:** Relational database state mapping, agent-to-agent (A2A) negotiation on soft recovery trade-offs, LLM output fidelity benchmarking, deterministic API payloads, mathematical constraint programming, post-hoc schedule explainability, and comparative latency benchmarking.
* **Out of Scope (Explicitly Excluded):** 3D digital twins, physical coordinate system configurations, spatial room differentiation, and edge physics simulations (e.g., machine temperature, thermodynamics). The walls of the factory and the physical limitations of the machines are represented entirely as mathematical boundary conditions within a relational database.

---

### 3. System Architecture & Technology Stack

The pipeline relies on a Python-native stack designed for high reliability and separation of concerns.

| Component | Technology | Purpose |
| --- | --- | --- |
| **Orchestration** | LangGraph | Manages the Agent-to-Agent (A2A) protocol, handles state rollbacks, and enforces deterministic routing. |
| **State & Telemetry** | TimescaleDB | Acts as the single source of truth for all schedule and inventory states. |
| **Event Ingestion** | MQTT (Mosquitto) | Captures asynchronous machine failure signals from the edge. |
| **Production Solver** | Google OR-Tools (CP-SAT) | Computes the primary classical optimization for the full-scale factory load. |
| **Research Solver** | Qiskit / Aer (QAOA) | Evaluates a QUBO-mapped micro-instance for the quantum feasibility study. |

---

### 4. Core Entities & Boundary Constraints

The system operates on four primary mathematical entities, stored relationally and queried by the orchestration layer.

* **Jobs (Customer Orders):**
* *Precedence:* Strict sequence enforcement (Operation $O_{i, j+1}$ cannot start before $O_{i, j}$ ends).
* *Deadlines:* Tardiness penalties applied if the final operation exceeds the target completion timestamp.


* **Machines (Compute/Execution Nodes):**
* *Heterogeneity:* Operations are strictly bound to a subset of physically capable machines ($\sum_{k \in M_{i,j}} X_{i,j,k} = 1$).
* *Asymmetric Processing:* Operation duration scales depending on the specific machine assigned.
* *Capacity:* Strict no-overlap constraints; a machine processes exactly one operation at a time.


* **Materials (Inventory):**
* *Gatekeeping:* Operations cannot be scheduled if required raw materials are unavailable.


* **Workers (Human Resources):**
* *Dependency:* Operations require specific human roles available at the assigned machine during the scheduled block.



---

### 5. Data Sourcing & Management

To maintain academic rigor and solvable constraint matrices, the system will utilize standard Operations Research benchmark datasets, augmented for real-world simulation.

* **Base Topology:** Brandimarte or Kacem FJSP benchmark text files (provides exact job sequences, machine capabilities, and processing times).
* **Synthetic Augmentation:**
* *Deadlines:* Calculated computationally using the Total Work Content (TWK) method.
* *Existing Load/Queue:* Generated dynamically by simulating the baseline schedule and injecting an MQTT failure at $T > 0$.
* *Inventory & Workers:* Generated as synthetic boolean tables to satisfy the agentic gatekeeping requirements.
* *LLM-Generated Scenarios:* Synthetic disruption narratives (messy failure reports, inventory shortages, worker absentee events) generated to stress-test the pipeline and to serve as the ground-truth corpus for the narrative-to-payload fidelity benchmark.



---

### 6. Execution Flow & Multi-Agent Orchestration

The LLMs exist exclusively in the semantic layer: they translate meaning, propose preferences, and explain outcomes — they never calculate the math. All mathematical work is performed solely by the deterministic solver pipeline. Every LLM-derived output passes a schema/constraint validator before it can influence system state.

1. **Baseline Operation:** The factory executes a 30-job, 8-machine schedule. TimescaleDB tracks the timeline.
2. **Disruption Trigger:** An edge node publishes an MQTT payload indicating a machine failure. If the report arrives as unstructured narrative, the *Translation Agent* converts it into a structured disruption record (AI Role 1).
3. **Database Ingestion:** The backend listener validates the record and updates the specific machine's status to `FAILED`.
4. **Agentic Investigation & Strategy Proposals (LangGraph):**
* *Machine Agent:* Confirms the failed machine and its lost capabilities, then pings the Manager.
* *Production Agent:* Queries TimescaleDB to identify all stranded or pending jobs mapped to the failed machine.
* *Inventory Agent:* Verifies material/worker availability for alternative routing.
* *Strategy Agent:* Proposes soft recovery trade-offs (e.g., route to a slower capable machine, reorder job priorities, accept tardiness on a low-priority job) expressed as preference-weighted candidates (AI Role 2).

5. **Payload Compilation:** The *Manager Agent* compiles the remaining active queue, deadlines, processing times, and the proposed strategy candidates into a strict JSON payload. Hard constraints are computed from the database alone; LLM proposals are carried only as soft preference signals.
6. **Mathematical Handoff (The Solvers):** The JSON passes to the OR-Tools backend, which computes the recovery schedule — optimizing over feasible candidate strategies where possible, otherwise returning the global optimum. The mathematics happens here, and only here.
7. **State Commit:** The optimized schedule is returned to the Manager Agent, which executes SQL `UPDATE` commands to rewrite the `Active_Schedule` table, protected by the automated rollback safety net.
8. **Explanation:** The committed schedule is passed to an LLM, which generates a human-readable rationale — which constraints bound the solution and why operations moved — for operator and audit visibility (AI Role 3).

---

### 7. Dual-Solver Implementation Strategy

To satisfy both production stability and research novelty, the mathematical delegation is strictly bifurcated:

#### A. The Production Pipeline (Classical)

* **Scale:** 30 Jobs / 8 Machines.
* **Engine:** Google OR-Tools (CP-SAT).
* **Role:** The actual fail-safe. Handles the heavy combinatorial load, processes interval variables, enforces the `AddNoOverlap` constraints, and successfully commits the recovery schedule to the database.

#### B. The Research Pipeline (Quantum Benchmark)

* **Scale:** 3 Jobs / 2 Machines (Strict Micro-Instance).
* **Engine:** Qiskit / Aer (QAOA).
* **Role:** The academic hook. The LangGraph workflow formats a localized QUBO polynomial to run on local statevector simulators.
* **Deliverable:** A comparative latency and feasibility analysis detailing the integration of quantum algorithms within an AIoT middleware stack.

---

### 8. Implementation Roadmap (14-Week Timeline)

* **Phase 1: Infrastructure & Data Ingestion (Weeks 1–2)**
  * Deploy TimescaleDB and Mosquitto MQTT broker.
  * Develop parsers for Brandimarte MK01, Hutter/Nouri, and GASS benchmark files.
  * Build the deterministic scenario builder pipeline (7 transformations) producing `factory_demo_01`.
  * Prove the MQTT ingestion path end-to-end.


* **Phase 2: Classical Optimization Engine (Weeks 3–6)**
  * Implement the payload builder with recovery logic, conflict clipping, and material gatekeeping.
  * Build the full CP-SAT constraint model including sequence-dependent setup times (AddCircuit with dummy nodes).
  * Validate against MK01 known optimal (makespan = 40).
  * Implement schedule versioning, rollback, and all individual constraint tests.


* **Phase 3: Agentic Middleware (Weeks 7–10)**
  * Build the LangGraph A2A workflow (Translation, Investigation, Strategy, Manager, Explanation nodes).
  * Develop the narrative-to-payload translation layer with schema validation and retry logic.
  * Implement the strategy catalog, applier, pre-commit gate, post-commit verifier, and automated rollback.
  * Wire the MQTT failure trigger to launch recovery runs automatically.
  * Build the fidelity benchmark corpus and runner.


* **Phase 4: Quantum Formulation (Weeks 11–12)**
  * Extract the deterministic 3-job/2-machine micro-instance.
  * Manually encode JSSP penalty terms into a QUBO with calibration sweep.
  * Execute QAOA via Qiskit/Aer with depth sweep and independent feasibility validation.


* **Phase 5: Integration & Benchmarking (Weeks 13–14)**
  * Wire the quantum shadow node into the recovery graph.
  * Instrument per-node latency across the full pipeline.
  * Finalize solver comparison, fidelity metrics, and the reproducible publication bundle.
