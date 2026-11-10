import tempfile,unittest
from pathlib import Path
from sqlalchemy import MetaData,CheckConstraint
from sqlalchemy.exc import IntegrityError
from app import db
from app.models import Base
from app.migrations import ticket_auto_review

class AutoMigrationTests(unittest.TestCase):
    def test_old_assigned_and_history_survive_repeated_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=db.create_db_engine('sqlite:///'+(Path(directory)/'test.db').as_posix())
            old=MetaData()
            for table in Base.metadata.sorted_tables:table.to_metadata(old)
            ticket=old.tables['tickets']
            for name in ['high_risk_count','needs_human_reason','confirmed_by','confirmed_at','confirmation_note','review_error']:
                column=ticket.c[name]
                for fk in list(ticket.foreign_key_constraints):
                    if column in list(fk.columns):ticket.constraints.remove(fk)
                ticket._columns.remove(column)
            for constraint in list(ticket.constraints):
                if isinstance(constraint,CheckConstraint) and ('status IN' in str(constraint.sqltext) or 'high_risk_count' in str(constraint.sqltext)):ticket.constraints.remove(constraint)
            ticket.append_constraint(CheckConstraint("status IN ('pending','assigned','reviewing','done','closed')"))
            old.create_all(engine)
            with engine.begin() as s:
                s.exec_driver_sql("INSERT INTO users(id,username,password_hash,role) VALUES ('u','u','x','reviewer')")
                s.exec_driver_sql("INSERT INTO contracts(id,series_id,contract_version,title,filename,stored_path,file_hash,party_stance,parse_status,owner_id) VALUES ('c','c',1,'c','c','c','x','party_b','parsed','u')")
                s.exec_driver_sql("INSERT INTO tickets(id,title,contract_id,requester_id,assignee_id,status,raw_request) VALUES ('t','title','c','u','u','assigned','keep')")
            ticket_auto_review(engine);ticket_auto_review(engine)
            with engine.connect() as s:
                self.assertEqual(s.exec_driver_sql("SELECT status,raw_request,high_risk_count FROM tickets").one(),('reviewing','keep',0))
                self.assertEqual(s.exec_driver_sql('PRAGMA foreign_key_check').all(),[])
            with self.assertRaises(IntegrityError),engine.begin() as s:s.exec_driver_sql("UPDATE tickets SET status='assigned'")
            engine.dispose()
