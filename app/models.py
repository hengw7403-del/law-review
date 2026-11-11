"""Review skeleton and additive conversation/ticket models."""

from sqlalchemy import (
    CheckConstraint, Column, ForeignKey, Index, Integer, Text, DateTime,
    UniqueConstraint, text,
)

from app.db import Base


class User(Base):
    __tablename__ = 'users'

    id = Column(Text, primary_key=True, nullable=False)
    username = Column(Text, nullable=False)
    org_name = Column(Text, nullable=True)
    password_hash = Column(Text, nullable=False)
    role = Column(Text, nullable=False)
    active = Column(Integer, nullable=False, server_default=text('1'))
    auth_version = Column(Integer, nullable=False, server_default=text('1'))
    created_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))

    __table_args__ = (
        UniqueConstraint('username'),
        CheckConstraint("role IN ('admin','reviewer','requester')"),
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
    party_stance = Column(Text, nullable=True)
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
        CheckConstraint("check_status IN ('supported','insufficient','failed','no_issue_detected','not_applicable','merged')"),
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


class Conversation(Base):
    __tablename__ = 'conversations'
    id = Column(Text, primary_key=True, nullable=False)
    user_id = Column(Text, ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    status = Column(Text, nullable=False, server_default=text("'active'"))
    collected = Column(Text, nullable=False, server_default=text("'{}'"))
    created_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("status IN ('active','completed','abandoned')"),
        CheckConstraint("json_valid(collected) AND json_type(collected)='object'"),
        Index('uq_user_active_conversation', 'user_id', unique=True, sqlite_where=text("status='active'")),
    )


class ConversationMessage(Base):
    __tablename__ = 'conversation_messages'
    id = Column(Text, primary_key=True, nullable=False)
    conversation_id = Column(Text, ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False)
    role = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (CheckConstraint("role IN ('user','assistant')"), Index('idx_conversation_history','conversation_id','created_at'))


class AgentRun(Base):
    __tablename__ = 'agent_runs'
    id = Column(Text, primary_key=True, nullable=False)
    request = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    contracts_total = Column(Integer, nullable=True)
    contracts_processed = Column(Integer, nullable=False, server_default=text('0'))
    rounds_used = Column(Integer, nullable=False, server_default=text('0'))
    rounds_limit = Column(Integer, nullable=True)
    stopped_reason = Column(Text, nullable=True)
    planning_tokens = Column(Integer, nullable=True)
    review_tokens = Column(Integer, nullable=True)
    total_calls = Column(Integer, nullable=False, server_default=text('0'))
    error_message = Column(Text, nullable=True)
    created_by = Column(Text, ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    # Compact display/checkpoint metadata only, never contract/result bodies.
    context_json = Column(Text, nullable=False, server_default=text("'{}'"))
    __table_args__ = (
        CheckConstraint("status IN ('running','completed','failed','partial')"),
        CheckConstraint('contracts_processed >= 0 AND rounds_used >= 0 AND total_calls >= 0'),
        CheckConstraint('json_valid(context_json)'),
        Index('idx_agent_runs_created_at', created_at.desc()),
        Index('idx_agent_runs_created_by', 'created_by'),
    )


class AgentStep(Base):
    __tablename__ = 'agent_steps'
    id = Column(Text, primary_key=True, nullable=False)
    run_id = Column(Text, ForeignKey('agent_runs.id', ondelete='CASCADE'), nullable=False)
    round = Column(Integer, nullable=False)
    max_rounds = Column(Integer, nullable=True)
    decision = Column(Text, nullable=True)
    tool = Column(Text, nullable=True)
    args = Column(Text, nullable=False, server_default=text("'{}'"))
    result_ok = Column(Integer, nullable=True)
    result_summary = Column(Text, nullable=True)
    tokens = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    review_task_id = Column(Text, ForeignKey('review_tasks.id', ondelete='SET NULL'), nullable=True)
    __table_args__ = (
        UniqueConstraint('run_id','round',name='uq_agent_step_round'),
        CheckConstraint('round > 0'),
        CheckConstraint('result_ok IS NULL OR result_ok IN (0,1)'),
        CheckConstraint('length(result_summary) <= 200'),
        CheckConstraint("json_valid(args) AND json_type(args)='object'"),
        Index('idx_agent_steps_run_round','run_id','round'),
    )


class Ticket(Base):
    __tablename__ = 'tickets'
    id = Column(Text, primary_key=True, nullable=False)
    title = Column(Text, nullable=False)
    conversation_id = Column(Text, ForeignKey('conversations.id', ondelete='SET NULL'), nullable=True, unique=True)
    contract_id = Column(Text, ForeignKey('contracts.id', ondelete='RESTRICT'), nullable=False)
    requester_id = Column(Text, ForeignKey('users.id', ondelete='RESTRICT'), nullable=False)
    assignee_id = Column(Text, ForeignKey('users.id', ondelete='RESTRICT'), nullable=True)
    status = Column(Text, nullable=False, server_default=text("'pending'"))
    priority = Column(Text, nullable=False, server_default=text("'normal'"))
    focus_points = Column(Text, nullable=False, server_default=text("'[]'"))
    raw_request = Column(Text, nullable=False)
    review_task_id = Column(Text, ForeignKey('review_tasks.id', ondelete='RESTRICT'), nullable=True)
    high_risk_count = Column(Integer, nullable=False, server_default=text('0'))
    needs_human_reason = Column(Text, nullable=True)
    confirmed_by = Column(Text, ForeignKey('users.id', ondelete='RESTRICT'), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    confirmation_note = Column(Text, nullable=True)
    review_error = Column(Text, nullable=True)
    source_system = Column(Text, nullable=True)
    source_contract_id = Column(Text, nullable=True)
    source_content_hash = Column(Text, nullable=True)
    agent_run_id = Column(Text, nullable=True)
    created_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    updated_at = Column(Text, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    __table_args__ = (
        CheckConstraint("status IN ('pending','needs_stance','reviewing','needs_human','done','closed')"),
        CheckConstraint("priority IN ('low','normal','urgent')"),
        CheckConstraint("json_valid(focus_points) AND json_type(focus_points)='array'"),
        CheckConstraint('high_risk_count >= 0'),
        Index('idx_ticket_queue','status','created_at'),
    )
