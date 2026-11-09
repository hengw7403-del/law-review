"""Seven declarative models matching v6 section 9; no business logic."""

from sqlalchemy import (
    CheckConstraint, Column, ForeignKey, Index, Integer, Text,
    UniqueConstraint, text,
)

from app.db import Base


class User(Base):
    __tablename__ = 'users'

    id = Column(Text, primary_key=True, nullable=False)
    username = Column(Text, nullable=False)
    password_hash = Column(Text, nullable=False)
    role = Column(Text, nullable=False)
    active = Column(Integer, nullable=False, server_default=text('1'))
    auth_version = Column(Integer, nullable=False, server_default=text('1'))
    created_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    __table_args__ = (
        UniqueConstraint('username'),
        CheckConstraint("role IN ('admin','reviewer')"),
        CheckConstraint('active IN (0,1)'),
    )

class Contract(Base):
    __tablename__ = 'contracts'

    id = Column(Text, primary_key=True, nullable=False)
    series_id = Column(Text, nullable=False)
    contract_version = Column(Integer, nullable=False)
    previous_version_id = Column(Text, ForeignKey('contracts.id'), nullable=True)
    title = Column(Text, nullable=False)
    filename = Column(Text, nullable=False)
    stored_path = Column(Text, nullable=False)
    file_hash = Column(Text, nullable=False)
    party_stance = Column(Text, nullable=False)
    amount_minor = Column(Integer, nullable=True)
    currency = Column(Text, nullable=False, server_default=text("'CNY'"))
    focus_points = Column(Text, nullable=True)
    raw_text = Column(Text, nullable=True)
    preamble = Column(Text, nullable=True)
    parse_status = Column(Text, nullable=False)
    parse_error = Column(Text, nullable=True)
    parse_warnings = Column(Text, nullable=False, server_default=text("'[]'"))
    parse_confirmed_by = Column(Text, ForeignKey('users.id'), nullable=True)
    parse_confirmed_at = Column(Text, nullable=True)
    owner_id = Column(Text, ForeignKey('users.id'), nullable=False)
    reviewer_id = Column(Text, ForeignKey('users.id'), nullable=True)
    locked_at = Column(Text, nullable=True)
    revision = Column(Integer, nullable=False, server_default=text('1'))
    created_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    __table_args__ = (
        Index('idx_contracts_reviewer', 'reviewer_id'),
        Index('idx_contracts_owner', 'owner_id'),
        UniqueConstraint('series_id', 'contract_version'),
        CheckConstraint('contract_version > 0'),
        CheckConstraint("party_stance IN ('party_a','party_b')"),
        CheckConstraint('amount_minor IS NULL OR amount_minor >= 0'),
        CheckConstraint("parse_status IN ('uploaded','parsing','parsed','parse_failed')"),
    )

class Clause(Base):
    __tablename__ = 'clauses'

    id = Column(Text, primary_key=True, nullable=False)
    contract_id = Column(Text, ForeignKey('contracts.id'), nullable=False)
    parent_clause_id = Column(Text, ForeignKey('clauses.id'), nullable=True)
    seq = Column(Integer, nullable=False)
    original_no = Column(Text, nullable=True)
    content = Column(Text, nullable=False)
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)
    location_json = Column(Text, nullable=False, server_default=text("'{}'"))
    created_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    __table_args__ = (
        Index('idx_clauses_contract', 'contract_id'),
        UniqueConstraint('contract_id', 'seq'),
    )

class KnowledgeChunk(Base):
    __tablename__ = 'knowledge_chunks'

    id = Column(Text, primary_key=True, nullable=False)
    source_id = Column(Text, nullable=False)
    source_name = Column(Text, nullable=False)
    source_kind = Column(Text, nullable=False)
    source_file = Column(Text, nullable=True)
    source_locator = Column(Text, nullable=False)
    publisher = Column(Text, nullable=True)
    source_version = Column(Integer, nullable=False)
    article_no = Column(Text, nullable=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(Text, nullable=False)
    effective_date = Column(Text, nullable=True)
    expiry_date = Column(Text, nullable=True)
    applicability = Column(Text, nullable=True)
    verified = Column(Integer, nullable=False, server_default=text('0'))
    verified_by = Column(Text, ForeignKey('users.id'), nullable=True)
    verified_at = Column(Text, nullable=True)
    enabled = Column(Integer, nullable=False, server_default=text('0'))
    superseded = Column(Integer, nullable=False, server_default=text('0'))
    created_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    __table_args__ = (
        Index('idx_knowledge_active', 'enabled', 'verified', 'superseded'),
        UniqueConstraint('source_id', 'source_version', 'chunk_index'),
        CheckConstraint("source_kind IN ('law','internal_rule')"),
        CheckConstraint('source_version > 0'),
        CheckConstraint('verified IN (0,1)'),
        CheckConstraint('enabled IN (0,1)'),
        CheckConstraint('superseded IN (0,1)'),
    )

class ReviewTask(Base):
    __tablename__ = 'review_tasks'

    id = Column(Text, primary_key=True, nullable=False)
    contract_id = Column(Text, ForeignKey('contracts.id'), nullable=False)
    review_version = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)
    review_status = Column(Text, nullable=False, server_default=text("'pending'"))
    stage = Column(Text, nullable=False, server_default=text("'queued'"))
    input_snapshot = Column(Text, nullable=False)
    total_items = Column(Integer, nullable=False, server_default=text('0'))
    done_items = Column(Integer, nullable=False, server_default=text('0'))
    failed_items = Column(Integer, nullable=False, server_default=text('0'))
    error_code = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    summary_history = Column(Text, nullable=False, server_default=text("'[]'"))
    frozen_report = Column(Text, nullable=True)
    report_hash = Column(Text, nullable=True)
    confirmed_by = Column(Text, ForeignKey('users.id'), nullable=True)
    confirmed_at = Column(Text, nullable=True)
    revision = Column(Integer, nullable=False, server_default=text('1'))
    created_by = Column(Text, ForeignKey('users.id'), nullable=False)
    started_at = Column(Text, nullable=True)
    finished_at = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    __table_args__ = (
        Index('uq_active_review', 'contract_id', unique=True, sqlite_where=text("status IN ('queued','running')")),
        Index('idx_tasks_queue', 'status', 'created_at'),
        UniqueConstraint('contract_id', 'review_version'),
        CheckConstraint('review_version > 0'),
        CheckConstraint("status IN ('queued','running','completed','partial_failed','failed')"),
        CheckConstraint("review_status IN ('pending','in_review','confirmed')"),
        CheckConstraint("review_status <> 'confirmed' OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL AND frozen_report IS NOT NULL AND report_hash IS NOT NULL)"),
    )

class ReviewItem(Base):
    __tablename__ = 'review_items'

    id = Column(Text, primary_key=True, nullable=False)
    review_id = Column(Text, ForeignKey('review_tasks.id'), nullable=False)
    item_key = Column(Text, nullable=False)
    clause_id = Column(Text, ForeignKey('clauses.id'), nullable=True)
    rule_id = Column(Text, nullable=False)
    execution_status = Column(Text, nullable=False, server_default=text("'pending'"))
    check_status = Column(Text, nullable=True)
    risk_level = Column(Text, nullable=True)
    machine_result = Column(Text, nullable=True)
    human_result = Column(Text, nullable=True)
    decision = Column(Text, nullable=False, server_default=text("'pending'"))
    decision_note = Column(Text, nullable=True)
    decision_history = Column(Text, nullable=False, server_default=text("'[]'"))
    updated_by = Column(Text, ForeignKey('users.id'), nullable=True)
    revision = Column(Integer, nullable=False, server_default=text('1'))
    created_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    __table_args__ = (
        Index('idx_items_review', 'review_id'),
        UniqueConstraint('review_id', 'item_key'),
        CheckConstraint("execution_status IN ('pending','done')"),
        CheckConstraint("check_status IN ('supported','insufficient','failed','no_issue_detected','not_applicable')"),
        CheckConstraint("risk_level IN ('high','medium','low','unknown')"),
        CheckConstraint("decision IN ('pending','investigating','accepted','modified','excluded','unresolved')"),
        CheckConstraint("execution_status <> 'done' OR (check_status IS NOT NULL AND machine_result IS NOT NULL)"),
    )

class LLMLog(Base):
    __tablename__ = 'llm_logs'

    id = Column(Text, primary_key=True, nullable=False)
    request_id = Column(Text, nullable=False)
    # Single-clause previews have a request_id but no fabricated review task.
    review_id = Column(Text, ForeignKey('review_tasks.id'), nullable=True)
    item_id = Column(Text, ForeignKey('review_items.id'), nullable=True)
    purpose = Column(Text, nullable=False)
    provider = Column(Text, nullable=False)
    model = Column(Text, nullable=False)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    attempt_no = Column(Integer, nullable=False)
    error_code = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    __table_args__ = (
        Index('idx_logs_review', 'review_id', 'created_at'),
    )
