from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from coe.db.base import Base


class Machine(Base):
    __tablename__ = "machines"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','FAILED','MAINTENANCE')",
            name="machine_status",
        ),
        UniqueConstraint("id", "instance_id", name="uq_machines_id_instance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")


class MachineCapability(Base):
    __tablename__ = "machine_capabilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    capability_code: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str | None] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(40))


class JobFamily(Base):
    __tablename__ = "job_families"
    __table_args__ = (UniqueConstraint("id", "instance_id", name="uq_job_families_id_instance"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','IN_PROGRESS','COMPLETED','BLOCKED')",
            name="job_status",
        ),
        UniqueConstraint("id", "instance_id", name="uq_jobs_id_instance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(120))
    job_family_id: Mapped[int | None]
    release_time: Mapped[int] = mapped_column(default=0)
    deadline: Mapped[int | None]
    priority: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")

    family: Mapped["JobFamily | None"] = relationship(
        foreign_keys="Job.job_family_id",
        primaryjoin="Job.job_family_id == JobFamily.id",
    )


class Operation(Base):
    __tablename__ = "operations"
    __table_args__ = (
        CheckConstraint("sequence_number >= 1", name="sequence_min"),
        CheckConstraint(
            "status IN ('PENDING','SCHEDULED','IN_PROGRESS','COMPLETED',"
            "'INTERRUPTED','BLOCKED')",
            name="operation_status",
        ),
        UniqueConstraint("job_id", "sequence_number", name="uq_operations_job_sequence"),
        UniqueConstraint("id", "instance_id", name="uq_operations_id_instance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    source_id: Mapped[str | None] = mapped_column(String(60))
    sequence_number: Mapped[int] = mapped_column(Integer)
    required_role_id: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(20), default="PENDING")


class OperationMachineAlternative(Base):
    __tablename__ = "operation_machine_alternatives"
    __table_args__ = (
        CheckConstraint("processing_time >= 0", name="processing_time_nonnegative"),
    )

    instance_id: Mapped[int] = mapped_column(
        ForeignKey("instances.id"), primary_key=True
    )
    operation_id: Mapped[int] = mapped_column(
        ForeignKey("operations.id"), primary_key=True
    )
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machines.id"), primary_key=True
    )
    processing_time: Mapped[int] = mapped_column(Integer)


class SetupTime(Base):
    __tablename__ = "setup_times"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), index=True)
    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"))
    from_family_id: Mapped[int | None] = mapped_column(ForeignKey("job_families.id"))
    to_family_id: Mapped[int | None] = mapped_column(ForeignKey("job_families.id"))
    setup_duration: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(40))
