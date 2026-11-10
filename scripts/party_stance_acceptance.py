"""Paid real Docker ticket acceptance. Prepare only with the worker stopped.

Overall budget: 100 attempts including conversation; audit counts in law.db.
An existing completed result is never overwritten or rerun.
"""
import json
from pathlib import Path
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'work/party-acceptance.json'


def main():
    action, side = sys.argv[1:3]
    data = json.loads(STATE.read_text(encoding='utf-8'))
    record = data['contracts'][side]
    with httpx.Client(base_url=data['base'], trust_env=False, timeout=90,
                      headers={'Authorization':'Bearer ' + (ROOT / 'work/party-auth-token').read_text().strip()}) as c:
        if action == 'prepare':
            assert not record.get('task_id'), 'Task already exists; do not duplicate paid runs'
            response = c.post('/conversations', json={})
            response.raise_for_status()
            conversation = response.json()['conversation_id']
            record['conversation_id'] = conversation
            STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            for index in range(1 if side == 'party_a' else 2):
                content = ('确认创建工单，合同ID ' + record['contract_id'] +
                           '。合同已明确选定，请审查全部条款，关注违约金、付款条件与单方解除；优先级普通，确认建单。')
                response = c.post('/conversations/' + conversation + '/messages', json={'content':content})
                response.raise_for_status()
                reply = response.json()
                record.setdefault('dialogue', []).append(reply)
                STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                if reply.get('ticket'):
                    break
            assert reply.get('ticket'), reply
            record['ticket_id'] = reply['ticket']['id']
            record['task_id'] = reply['ticket']['review_task_id']
            assert record['task_id'], reply
            response = c.get('/tasks/' + record['task_id'])
            response.raise_for_status()
            task = response.json()
            record['queued_task'] = task
            STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            assert (task['total_items'], task['clause_items'], task['contract_items']) == (125,120,5), task
            print(json.dumps({'side':side, 'task_id':record['task_id'], 'ticket_id':record['ticket_id'], 'planned_model_items':task['planned_model_items'], 'total_items':task['total_items']},ensure_ascii=False))
        elif action == 'wait':
            previous = None
            for _ in range(600):
                response = c.get('/tasks/' + record['task_id'])
                response.raise_for_status()
                task = response.json()
                if task['processed_items'] != previous:
                    previous = task['processed_items']
                    print(json.dumps({'side':side,'status':task['status'],'done':previous,'total':task['total_items'],'calls':task['metrics']['model_calls']}),flush=True)
                if task['status'] in {'completed','partial_failed','failed'}:
                    break
                time.sleep(1)
            else:
                raise TimeoutError('Task did not finish')
            results = c.get('/reviews/' + record['task_id'] + '/items')
            results.raise_for_status()
            ticket = c.get('/tickets/' + record['ticket_id'])
            ticket.raise_for_status()
            record.update(task=task, results=results.json(), ticket=ticket.json())
            expected = 'needs_human' if side == 'party_b' else 'done'
            record['expected_ticket_status'] = expected
            record['expectation_met'] = ticket.json()['status'] == expected
            STATE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'side':side,'task_id':record['task_id'],'status':task['status'],'done':task['done_items'],'ticket_status':ticket.json()['status'],'high':ticket.json()['high_risk_count'],'stats':results.json()['stats'],'model_calls':task['metrics']['model_calls'],'expectation_met':record['expectation_met']},ensure_ascii=False),flush=True)
            assert task['status']=='completed' and task['done_items']==125, task
        else:
            raise ValueError('Unknown action')


if __name__ == '__main__':
    main()
