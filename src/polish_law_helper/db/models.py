import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from polish_law_helper.config import settings


class Base(DeclarativeBase):
    pass


class Act(Base):
    __tablename__ = "acts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    eli_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    eli_address: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    act_type: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(String(50))
    in_force: Mapped[str | None] = mapped_column(String(20))
    announcement_date: Mapped[date | None] = mapped_column(Date)
    entry_into_force: Mapped[date | None] = mapped_column(Date)
    publisher: Mapped[str] = mapped_column(String(10), default="DU")
    year: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int | None] = mapped_column(Integer)
    keywords: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    raw_html_hash: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chunks: Mapped[list["Chunk"]] = relationship(back_populates="act", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    act_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("acts.id", ondelete="CASCADE"), nullable=False
    )

    # Hierarchy
    part_num: Mapped[str | None] = mapped_column(String(20))
    part_title: Mapped[str | None] = mapped_column(Text)
    title_num: Mapped[str | None] = mapped_column(String(20))
    title_name: Mapped[str | None] = mapped_column(Text)
    section_num: Mapped[str | None] = mapped_column(String(20))
    section_title: Mapped[str | None] = mapped_column(Text)
    chapter_num: Mapped[str | None] = mapped_column(String(20))
    chapter_title: Mapped[str | None] = mapped_column(Text)

    article_num: Mapped[str] = mapped_column(String(20), nullable=False)
    paragraph_num: Mapped[str | None] = mapped_column(String(20))
    point_num: Mapped[str | None] = mapped_column(String(20))

    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    text_for_embedding: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dim))
    char_count: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    act: Mapped["Act"] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("idx_chunks_act_id", "act_id"),
        Index("idx_chunks_article_num", "article_num"),
        Index(
            "idx_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class ActReference(Base):
    __tablename__ = "act_references"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_act_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("acts.id", ondelete="CASCADE"), nullable=False
    )
    target_eli_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_act_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("acts.id", ondelete="SET NULL")
    )
    reference_type: Mapped[str] = mapped_column(String(100), nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date)


class LegislativeProcess(Base):
    __tablename__ = "legislative_processes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    term: Mapped[int] = mapped_column(Integer, nullable=False)
    process_number: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(100))
    document_date: Mapped[date | None] = mapped_column(Date)
    process_start: Mapped[date | None] = mapped_column(Date)
    closure_date: Mapped[date | None] = mapped_column(Date)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    urgency_status: Mapped[str | None] = mapped_column(String(50))
    related_act_eli: Mapped[str | None] = mapped_column(Text)
    change_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_json: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("term", "process_number", name="uq_process_term_number"),
    )


class Voting(Base):
    __tablename__ = "votings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    term: Mapped[int] = mapped_column(Integer, nullable=False)
    sitting: Mapped[int] = mapped_column(Integer, nullable=False)
    voting_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    result: Mapped[str | None] = mapped_column(String(20))
    yes_count: Mapped[int | None] = mapped_column(Integer)
    no_count: Mapped[int | None] = mapped_column(Integer)
    abstain_count: Mapped[int | None] = mapped_column(Integer)
    raw_json: Mapped[dict | None] = mapped_column(JSONB)
    process_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("legislative_processes.id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("term", "sitting", "voting_number", name="uq_voting"),
    )


class SejmPrint(Base):
    __tablename__ = "sejm_prints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    term: Mapped[int] = mapped_column(Integer, nullable=False)
    print_number: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    document_date: Mapped[date | None] = mapped_column(Date)
    process_number: Mapped[str | None] = mapped_column(String(20))
    attachment_url: Mapped[str | None] = mapped_column(Text)
    text_content: Mapped[str | None] = mapped_column(Text)
    text_hash: Mapped[str | None] = mapped_column(String(64))
    raw_json: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    chunks: Mapped[list["PrintChunk"]] = relationship(
        back_populates="sejm_print", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("term", "print_number", name="uq_sejm_print_term_number"),
    )


class PrintChunk(Base):
    __tablename__ = "print_chunks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    print_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sejm_prints.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    text_for_embedding: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(settings.embedding_dim))
    char_count: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )

    sejm_print: Mapped["SejmPrint"] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("idx_print_chunks_print_id", "print_id"),
        Index(
            "idx_print_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class SenatProcess(Base):
    __tablename__ = "senat_processes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    term: Mapped[int] = mapped_column(Integer, nullable=False)
    print_number: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    sejm_process_number: Mapped[str | None] = mapped_column(String(20))
    decision: Mapped[str | None] = mapped_column(String(100))
    decision_date: Mapped[date | None] = mapped_column(Date)
    raw_json: Mapped[dict | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("term", "print_number", name="uq_senat_process"),
    )


class IngestionLog(Base):
    __tablename__ = "ingestion_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    identifier: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
