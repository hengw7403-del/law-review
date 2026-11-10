"""Parse and persist an uploaded contract owned by the authenticated user."""
import hashlib
import json
import os
from pathlib import Path
import uuid
from datetime import datetime, timezone

from app import db
from app.models import Clause, Contract
from app.services.clause_splitter import split_document
from app.services.parse_quality import assess_quality
from app.services.parser import ParseError, display_filename, parse_document
from app.services.party_parser import party_detection


def create_contract(session_factory, user, filename, data, stance, title=None):
    text, mapping = parse_document(filename, data)
    document = split_document(text, mapping)
    ok, error, warnings = assess_quality(text, len(document["clauses"]))
    if not ok:
        raise ParseError(error, "合同正文未通过解析质量检查")
    cid = uuid.uuid4().hex
    directory = Path(os.environ.get("CONTRACT_UPLOAD_DIR", str(db.PROJECT_ROOT / "work/uploads"))).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stored = directory / (cid + Path(filename).suffix.lower())
    try:
        with stored.open("xb") as output:
            output.write(data)
        with session_factory.begin() as session:
            now = datetime.now(timezone.utc).isoformat()
            session.add(Contract(id=cid, series_id=cid, contract_version=1,
                                 title=title or display_filename(filename), filename=display_filename(filename),
                                 stored_path=str(stored), file_hash=hashlib.sha256(data).hexdigest(),
                                 party_stance=stance, raw_text=text, preamble=document["preamble"],
                                 parse_status="parsed", parse_warnings=json.dumps(warnings), owner_id=user.id,
                                 created_at=now, updated_at=now))
            session.flush()
            for clause in document["clauses"]:
                session.add(Clause(id=uuid.uuid4().hex, contract_id=cid, seq=clause["seq"],
                                   original_no=clause["original_no"], content=clause["content"],
                                   char_start=clause["char_start"], char_end=clause["char_end"],
                                   location_json=json.dumps({"label": clause["location"]})))
    except Exception:
        stored.unlink(missing_ok=True)
        raise
    return {"contract_id": cid, "owner_id": user.id, "clause_count": len(document["clauses"]), "warnings": warnings,
            **party_detection(text, user.org_name)}
