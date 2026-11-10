import unittest,tempfile
from pathlib import Path
from sqlalchemy import MetaData
from sqlalchemy.exc import IntegrityError
from app import db
from app.migrations import add_ticket_tables
from app.models import Base

class TicketSchemaTests(unittest.TestCase):
    def test_additive_idempotent_constraints_and_delete_policies(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=db.create_db_engine('sqlite:///'+(Path(directory)/'law.db').as_posix())
            old=MetaData()
            for table in Base.metadata.sorted_tables:
                if table.name not in {'conversations','conversation_messages','tickets'}:table.to_metadata(old)
            old.create_all(engine);add_ticket_tables(engine);add_ticket_tables(engine)
            with engine.begin() as c:
                c.exec_driver_sql("INSERT INTO users(id,username,password_hash,role) VALUES ('u','u','x','reviewer')")
                c.exec_driver_sql("INSERT INTO contracts(id,series_id,contract_version,title,filename,stored_path,file_hash,party_stance,parse_status,owner_id) VALUES ('c','c',1,'c','c','c','x','party_b','parsed','u')")
                c.exec_driver_sql("INSERT INTO conversations(id,user_id) VALUES ('v','u')")
                c.exec_driver_sql("INSERT INTO conversation_messages(id,conversation_id,role,content) VALUES ('m','v','user','hello')")
                c.exec_driver_sql("INSERT INTO tickets(id,title,conversation_id,contract_id,requester_id,raw_request) VALUES ('t','t','v','c','u','hello')")
            for sql in ["INSERT INTO conversations(id,user_id) VALUES ('v2','u')", "UPDATE conversations SET status='bad'", "UPDATE conversation_messages SET role='system'", "UPDATE tickets SET status='bad'", "UPDATE tickets SET priority='bad'", "UPDATE tickets SET contract_id='missing'", "UPDATE tickets SET focus_points='{}'", "UPDATE tickets SET status='assigned'", "DELETE FROM contracts WHERE id='c'", "DELETE FROM users WHERE id='u'", "INSERT INTO tickets(id,title,conversation_id,contract_id,requester_id,raw_request) VALUES ('t2','t','v','c','u','hello')"]:
                with self.subTest(sql=sql), self.assertRaises(IntegrityError),engine.begin() as c:c.exec_driver_sql(sql)
            with engine.begin() as c:
                c.exec_driver_sql("DELETE FROM conversations WHERE id='v'")
                self.assertIsNone(c.exec_driver_sql("SELECT conversation_id FROM tickets WHERE id='t'").scalar())
                self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM conversation_messages').scalar(),0)
                self.assertEqual(c.exec_driver_sql('PRAGMA foreign_key_check').all(),[])
            engine.dispose()
