"""Reparse stored text as a new immutable contract version, keeping history."""
import json
import uuid
from sqlalchemy import func, select, text
from app.models import Clause, Contract
from app.services.clause_splitter import split_document
from app.services.parse_quality import assess_quality
from app.services.review_tasks import ReviewError


def reparse_contract(session_factory, contract_id: str):
    with session_factory.begin() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        old = session.get(Contract, contract_id)
        if old is None:
            raise ReviewError("CONTRACT_NOT_FOUND", "合同不存在", 404)
        document = split_document(old.raw_text or "")
        ok, error, warnings = assess_quality(old.raw_text or "", len(document["clauses"]))
        if not ok:
            raise ReviewError(error, "存储正文未通过解析质量检查")
        version = (session.scalar(select(func.max(Contract.contract_version)).where(Contract.series_id == old.series_id)) or 0) + 1
        new_id = uuid.uuid4().hex
        new = Contract(id=new_id, series_id=old.series_id, previous_version_id=old.id, contract_version=version,
                       title=old.title, filename=old.filename, stored_path=old.stored_path, file_hash=old.file_hash,
                       party_stance=old.party_stance, amount_minor=old.amount_minor, currency=old.currency,
                       focus_points=old.focus_points, raw_text=old.raw_text, preamble=document["preamble"],
                       parse_status="parsed", parse_warnings=json.dumps(warnings), owner_id=old.owner_id,
                       reviewer_id=old.reviewer_id)
        session.add(new)
        session.flush()
        for clause in document["clauses"]:
            session.add(Clause(id=uuid.uuid4().hex, contract_id=new_id, seq=clause["seq"], original_no=clause["original_no"],
                               content=clause["content"], char_start=clause["char_start"], char_end=clause["char_end"],
                               location_json=json.dumps({"label": clause["location"]})))
        return {"contract_id": new_id, "previous_version_id": old.id, "contract_version": version,
                "clause_count": len(document["clauses"]), "preamble_length": len(document["preamble"])}
