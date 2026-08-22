from dataclasses import dataclass, field
from pathlib import Path

from coe.db.models.fjsp import (
    Job,
    Machine,
    Operation,
    OperationMachineAlternative,
)
from coe.db.models.workers import OperationMachineWorkerTime, Worker
from coe.db.session import session_scope
from coe.parsers.common import (
    SourceParseError,
    get_or_create_source_instance,
    sha256_file,
)


@dataclass
class NouriAlt:
    machine_index: int
    workers: list[tuple[int, int]] = field(default_factory=list)  # (worker_idx, time)


@dataclass
class NouriOperation:
    index: int
    alternatives: list[NouriAlt] = field(default_factory=list)


@dataclass
class NouriJob:
    index: int
    operations: list[NouriOperation] = field(default_factory=list)


@dataclass
class ParsedNouriInstance:
    n_jobs: int
    n_machines: int
    n_workers: int
    jobs: list[NouriJob]


def parse_nouri(raw: str) -> ParsedNouriInstance:
    tokens: list[tuple[int, int]] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        for piece in line.split():
            try:
                tokens.append((int(piece), lineno))
            except ValueError as exc:
                raise SourceParseError(
                    f"line {lineno}: non-integer token {piece!r}"
                ) from exc

    pos = 0

    def nxt(context: str) -> tuple[int, int]:
        nonlocal pos
        if pos >= len(tokens):
            raise SourceParseError(f"unexpected end of file while reading {context}")
        value, lineno = tokens[pos]
        pos += 1
        return value, lineno

    n_jobs, _ = nxt("header job count")
    n_machines, _ = nxt("header machine count")
    n_workers, _ = nxt("header worker count")
    dims = (n_jobs, n_machines, n_workers)
    if any(v <= 0 for v in dims):
        raise SourceParseError(f"header dimensions must be positive, got {dims}")

    jobs: list[NouriJob] = []
    for j in range(n_jobs):
        job = NouriJob(index=j)
        n_ops, _ = nxt(f"job {j + 1} operation count")
        for o in range(n_ops):
            op = NouriOperation(index=o)
            k, _ = nxt(f"job {j + 1} op {o + 1} machine count")
            if k <= 0:
                raise SourceParseError(
                    f"job {j + 1} op {o + 1}: needs at least one capable machine"
                )
            for _ in range(k):
                m, ml = nxt(f"job {j + 1} op {o + 1} machine number")
                w_count, _ = nxt(f"job {j + 1} op {o + 1} worker count")
                if w_count <= 0:
                    raise SourceParseError(
                        f"job {j + 1} op {o + 1}: machine {m} must list at least one worker"
                    )
                if not 0 <= m - 1 < n_machines:
                    raise SourceParseError(
                        f"line {ml}: machine {m} outside 1..{n_machines}"
                    )
                alt = NouriAlt(machine_index=m - 1)
                for _ in range(w_count):
                    w, wl = nxt(f"job {j + 1} op {o + 1} worker number")
                    t, tl = nxt(f"job {j + 1} op {o + 1} worker processing time")
                    if not 0 <= w - 1 < n_workers:
                        raise SourceParseError(
                            f"line {wl}: worker {w} outside 1..{n_workers}"
                        )
                    if t <= 0:
                        raise SourceParseError(f"line {tl}: non-positive duration {t}")
                    alt.workers.append((w - 1, t))
                op.alternatives.append(alt)
            job.operations.append(op)
        jobs.append(job)

    if pos != len(tokens):
        _, lineno = tokens[pos]
        raise SourceParseError(f"line {lineno}: trailing tokens after last operation")

    return ParsedNouriInstance(
        n_jobs=n_jobs, n_machines=n_machines, n_workers=n_workers, jobs=jobs
    )


def import_nouri(path: Path, instance_name: str | None = None) -> int:
    """Atomic import; literal worker/op rows stay inside their own instance."""
    raw = path.read_text()
    parsed = parse_nouri(raw)
    checksum = sha256_file(path)
    name = instance_name or f"nouri-{path.stem.lower()}"
    with session_scope() as session:
        inst, created = get_or_create_source_instance(
            session,
            name=name,
            source_name="hutter-nouri-fjssp-w",
            # No verified public URL: record it only once confirmed against the
            # dataset's own README; leaving it null is honest provenance.
            source_url=None,
            source_version="mo-fjspw",
            source_license="academic-benchmark",
            checksum=checksum,
        )
        if not created:
            return inst.id

        machines = {}
        for mi in range(parsed.n_machines):
            row = Machine(instance_id=inst.id, source_id=str(mi), name=f"M{mi}")
            session.add(row)
            machines[mi] = row

        workers = {}
        for wi in range(parsed.n_workers):
            row = Worker(instance_id=inst.id, source_id=str(wi), name=f"W{wi}")
            session.add(row)
            workers[wi] = row

        for job in parsed.jobs:
            jrow = Job(
                instance_id=inst.id, source_id=str(job.index), name=f"J{job.index + 1}"
            )
            session.add(jrow)
            session.flush()
            for op in job.operations:
                orow = Operation(
                    instance_id=inst.id,
                    job_id=jrow.id,
                    source_id=f"{job.index}:{op.index}",
                    sequence_number=op.index + 1,
                )
                session.add(orow)
                session.flush()
                for alt in op.alternatives:
                    # Derive the machine-level alternative as the min worker time so
                    # the source instance is self-consistent without worker context.
                    session.add(
                        OperationMachineAlternative(
                            instance_id=inst.id,
                            operation_id=orow.id,
                            machine_id=machines[alt.machine_index].id,
                            processing_time=min(t for _, t in alt.workers),
                        )
                    )
                    for wi, t in alt.workers:
                        session.add(
                            OperationMachineWorkerTime(
                                instance_id=inst.id,
                                operation_id=orow.id,
                                machine_id=machines[alt.machine_index].id,
                                worker_id=workers[wi].id,
                                processing_time=t,
                            )
                        )
        session.flush()
        return inst.id
