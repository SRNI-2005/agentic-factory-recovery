from dataclasses import dataclass, field
from pathlib import Path

from coe.db.models.fjsp import Job, Machine, Operation, OperationMachineAlternative
from coe.db.session import session_scope
from coe.parsers.common import (
    SourceParseError,
    get_or_create_source_instance,
    sha256_file,
)


@dataclass
class Alt:
    machine_index: int
    processing_time: int


@dataclass
class ParsedOperation:
    index: int
    alternatives: list[Alt] = field(default_factory=list)


@dataclass
class ParsedJob:
    index: int
    operations: list[ParsedOperation] = field(default_factory=list)


@dataclass
class ParsedMkInstance:
    n_jobs: int
    n_machines: int
    jobs: list[ParsedJob]


def parse_mk01(raw: str) -> ParsedMkInstance:
    """Brandimarte grammar: '<n_jobs> <n_machines>' then per job '<num_ops>' then
    per operation '<num_alts> (<machine> <time>)*'. Machines are 0-indexed."""
    tokens: list[tuple[int, int]] = []  # (value, lineno)
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
    if n_jobs <= 0 or n_machines <= 0:
        raise SourceParseError("header must contain positive job and machine counts")

    jobs: list[ParsedJob] = []
    for j in range(n_jobs):
        job = ParsedJob(index=j)
        n_ops, _ = nxt(f"job {j + 1} operation count")
        for o in range(n_ops):
            op = ParsedOperation(index=o)
            k, _ = nxt(f"job {j + 1} operation {o + 1} alternative count")
            if k <= 0:
                raise SourceParseError(
                    f"job {j + 1} operation {o + 1}: needs at least one capable machine"
                )
            for _ in range(k):
                (m, ml), (t, tl) = (
                    nxt(f"job {j + 1} operation {o + 1} machine"),
                    nxt(f"job {j + 1} operation {o + 1} processing time"),
                )
                if not 0 <= m < n_machines:
                    raise SourceParseError(
                        f"line {ml}: machine {m} outside 0..{n_machines - 1}"
                    )
                if t <= 0:
                    raise SourceParseError(f"line {tl}: non-positive duration {t}")
                op.alternatives.append(Alt(machine_index=m, processing_time=t))
            job.operations.append(op)
        jobs.append(job)

    if pos != len(tokens):
        _, lineno = tokens[pos]
        raise SourceParseError(f"line {lineno}: trailing tokens after last operation")

    return ParsedMkInstance(n_jobs=n_jobs, n_machines=n_machines, jobs=jobs)


SOURCE_META = dict(
    source_name="brandimarte",
    source_url="https://github.com/SchedulingLab/fjsp-instances",
    source_version="brandimarte-mk",
    source_license="academic-benchmark",
)


def import_mk01(path: Path, instance_name: str = "mk01") -> int:
    """Atomic import into an isolated instance; returns the instance id."""
    parsed = parse_mk01(path.read_text())
    checksum = sha256_file(path)
    with session_scope() as session:
        inst, created = get_or_create_source_instance(
            session, name=instance_name, checksum=checksum, **SOURCE_META
        )
        if not created:
            return inst.id

        machines = {}
        for mi in range(parsed.n_machines):
            row = Machine(
                instance_id=inst.id, source_id=str(mi), name=f"M{mi}", status="ACTIVE"
            )
            session.add(row)
            machines[mi] = row

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
                    session.add(
                        OperationMachineAlternative(
                            instance_id=inst.id,
                            operation_id=orow.id,
                            machine_id=machines[alt.machine_index].id,
                            processing_time=alt.processing_time,
                        )
                    )
        session.flush()
        return inst.id
