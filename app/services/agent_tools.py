"""Local capabilities; remote OA tools never own our review/search services."""
import hashlib
import json
from sqlalchemy import select
from app.models import Ticket, ReviewTask
from app.services.mcp_client import MCPError
from app.services.review_rules import CLAUSE_RULES, MISSING_RULES, RULES_VERSION
from app.prompts.risk_judge import PROMPT_VERSION
from app.prompts.missing_judge import PROMPT_VERSION as MISSING_PROMPT_VERSION
from app.services.search import build_search_index, eligibility_conditions, TOKENIZER_VERSION
from app.models import KnowledgeChunk
from app.services.llm import MODEL


def content_hash(source):
    return hashlib.sha256(source['content'].encode('utf-8')).hexdigest()


def search_law(sf,args):
    query=args.get('query');top_k=args.get('top_k',5)
    if set(args)-{'query','top_k'} or not isinstance(query,str) or not 1<=len(query.strip())<=1000:
        raise MCPError('INVALID_TOOL_ARGUMENTS','search_law需要1至1000字的query，不接受其他参数')
    if isinstance(top_k,bool) or not isinstance(top_k,int) or not 1<=top_k<=50:
        raise MCPError('INVALID_TOOL_ARGUMENTS','top_k必须是1至50的整数')
    with sf() as s:
        index=build_search_index(s)
    return index.search(query.strip(),top_k)


def reusable_handoff(sf,endpoint,cid,source,stance,user):
    """Only completed legal handoffs, identical source/stance/rules/KB.

    This is explicit reuse of a real task, never a generated or mock result.
    An unrelated old OA review cannot qualify for this narrow path.
    """
    with sf() as s:
        rows=s.scalars(select(Ticket).where(Ticket.source_system==endpoint,
            Ticket.source_contract_id==cid,Ticket.source_content_hash==content_hash(source),
            Ticket.status.in_(['done','needs_human']),Ticket.review_task_id.isnot(None),
            Ticket.agent_run_id.isnot(None)).order_by(Ticket.created_at.desc())).all()
        kb={r.id:r.content_hash for r in s.scalars(select(KnowledgeChunk).where(*eligibility_conditions()))}
        for row in rows:
            # Legal/admin visibility is still enforced by Agent entry-point RBAC.
            task=s.get(ReviewTask,row.review_task_id)
            if not task or task.status!='completed':continue
            snap=json.loads(task.input_snapshot)
            if (snap['contract']['stance']!=stance or set(snap['rules'])!=set(CLAUSE_RULES)
                or set(snap.get('missing_rules',[]))!=set(MISSING_RULES)
                or snap.get('rules_version')!=RULES_VERSION or snap.get('prompt_version')!=PROMPT_VERSION
                or snap.get('missing_prompt_version')!=MISSING_PROMPT_VERSION
                or snap.get('tokenizer_version')!=TOKENIZER_VERSION or snap.get('model')!=MODEL
                or {k['id']:k['content_hash'] for k in snap['knowledge']}!=kb):continue
            return {'task_id':task.id,'contract_id':row.contract_id,'party_stance':stance,
                    'reused':True,'ticket_id':row.id,'source_agent_run_id':row.agent_run_id}
    return None
