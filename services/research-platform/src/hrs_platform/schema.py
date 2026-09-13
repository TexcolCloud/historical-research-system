from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()
books = Table(
    "books",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("title", Text, nullable=False),
    Column("state", String(40), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
uploads = Table(
    "uploads",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("book_id", UUID(as_uuid=False), ForeignKey("books.id"), nullable=False),
    Column("filename", Text, nullable=False),
    Column("byte_length", BigInteger, nullable=False),
    Column("token_hash", String(64), nullable=False),
    Column("state", String(40), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
runs = Table(
    "runs",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("book_id", UUID(as_uuid=False), ForeignKey("books.id"), nullable=False),
    Column("upload_id", UUID(as_uuid=False), ForeignKey("uploads.id"), nullable=True, unique=True),
    Column("state", String(40), nullable=False),
    Column("stage", String(40), nullable=False),
    Column("revision", Integer, nullable=False, server_default="0"),
    Column("recovery_attempt", Integer, nullable=False, server_default="0"),
    Column("source", JSONB),
    Column("conversion", JSONB),
    Column("pending_count", Integer, nullable=False, server_default="0"),
    Column("kind", String(30), nullable=False, server_default="book"),
    Column("parent_run_id", UUID(as_uuid=False), ForeignKey("runs.id")),
    Column("result", JSONB),
    Column("error", JSONB),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
events = Table(
    "events",
    metadata,
    Column("sequence", BigInteger, primary_key=True, autoincrement=True),
    Column("book_id", UUID(as_uuid=False), ForeignKey("books.id"), nullable=False),
    Column("run_id", UUID(as_uuid=False)),
    Column("kind", String(80), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
outbox = Table(
    "outbox",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("dedup_key", Text, nullable=False, unique=True),
    Column("run_id", UUID(as_uuid=False), ForeignKey("runs.id"), nullable=False),
    Column("kind", String(40), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("delivered", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

review_issues = Table(
    "review_issues",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("run_id", UUID(as_uuid=False), ForeignKey("runs.id"), nullable=False),
    Column("scope_key", Text, nullable=False),
    Column("page", Integer, nullable=False),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("state", String(30), nullable=False, server_default="pending"),
    Column("content", JSONB, nullable=False),
    Column("text_sha256", String(64), nullable=False),
    Column("replacement", JSONB),
    Column("draft", JSONB),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
review_decisions = Table(
    "review_decisions",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("issue_id", UUID(as_uuid=False), ForeignKey("review_issues.id"), nullable=False),
    Column("request_sha256", String(64), nullable=False),
    Column("receipt", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

chapters = Table(
    "chapters",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("book_id", UUID(as_uuid=False), ForeignKey("books.id"), nullable=False),
    Column("run_id", UUID(as_uuid=False), ForeignKey("runs.id"), nullable=False),
    Column("position", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("kind", String(30), nullable=False),
    Column("content", JSONB, nullable=False),
    Column("pages", JSONB, nullable=False),
    Column("codepoints", Integer, nullable=False),
)
stage_outputs = Table(
    "stage_outputs",
    metadata,
    Column("run_id", UUID(as_uuid=False), ForeignKey("runs.id"), primary_key=True),
    Column("step", Text, primary_key=True),
    Column("input_sha256", String(64), nullable=False),
    Column("reference", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
execution_nodes = Table(
    "execution_nodes",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("run_id", UUID(as_uuid=False), ForeignKey("runs.id"), nullable=False),
    Column("parent_id", UUID(as_uuid=False)),
    Column("kind", String(30), nullable=False),
    Column("label", Text, nullable=False),
    Column("objective", Text, nullable=False),
    Column("state", String(30), nullable=False),
    Column("details", JSONB),
    Column("started_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
)

cards = Table(
    "cards",
    metadata,
    Column("id", UUID(as_uuid=False), primary_key=True),
    Column("book_id", UUID(as_uuid=False), ForeignKey("books.id"), nullable=False),
    Column("run_id", UUID(as_uuid=False), ForeignKey("runs.id"), nullable=False),
    Column("title", Text, nullable=False),
    Column("state", String(30), nullable=False),
    Column("content", JSONB, nullable=False),
    Column("checks", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

# Durable deletion receipt; no second scheduler or in-memory background jobs.
book_deletions = Table(
    "book_deletions",
    metadata,
    Column("book_id", UUID(as_uuid=False), ForeignKey("books.id"), primary_key=True),
    Column("attempt", Integer, nullable=False, server_default="0"),
    Column("state", String(30), nullable=False),
    Column("manifest", JSONB),
    Column("error", Text),
)
