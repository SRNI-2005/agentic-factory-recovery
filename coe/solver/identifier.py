"""Payload identifier synthesis. Operations have no name column in Phase 1;
the payload contract synthesizes "{job.name}-O{sequence_number}"."""


def op_id(job_name: str, sequence_number: int) -> str:
    return f"{job_name}-O{sequence_number}"


def parse_op_id(raw: str) -> tuple[str, int]:
    job_name, sep, seq = raw.rpartition("-O")
    if not job_name or not sep or not seq.isdigit():
        raise ValueError(f"malformed operation id: {raw!r}")
    return job_name, int(seq)
