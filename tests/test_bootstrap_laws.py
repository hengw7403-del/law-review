import tempfile,unittest
from pathlib import Path
from sqlalchemy import select,func
from app import db
from app.models import Base,KnowledgeChunk
from app.services.bootstrap_laws import bootstrap_laws
from sqlalchemy.orm import Session
from app.services.search import build_search_index

class BootstrapLawsTests(unittest.TestCase):
    def test_import_enabled_searchable_once_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            engine=db.create_db_engine('sqlite:///'+(Path(d)/'law.db').as_posix());Base.metadata.create_all(engine)
            count=bootstrap_laws(engine,Path(__file__).resolve().parents[1]/'samples/laws');self.assertGreater(count,0)
            with Session(engine) as s:
                ids=set(s.scalars(select(KnowledgeChunk.id)));self.assertEqual(s.scalar(select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.enabled==1,KnowledgeChunk.verified==1)),count)
                self.assertTrue(build_search_index(s).search('违约金过高怎么办',5))
                first=s.get(KnowledgeChunk,next(iter(ids)));first.enabled=0;s.commit()
            self.assertEqual(bootstrap_laws(engine,'not-a-directory'),0)
            with Session(engine) as s:self.assertEqual(set(s.scalars(select(KnowledgeChunk.id))),ids);self.assertEqual(s.get(KnowledgeChunk,next(iter(ids))).enabled,0)
            engine.dispose()
    def test_bad_file_warns_but_valid_file_still_imports(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'bad.txt').write_bytes(b'\xff');(folder/'good.txt').write_text('第一条 依法履行合同义务。',encoding='utf-8')
            engine=db.create_db_engine('sqlite:///'+(folder/'law.db').as_posix());Base.metadata.create_all(engine)
            with self.assertLogs('app.services.bootstrap_laws',level='WARNING'):self.assertEqual(bootstrap_laws(engine,folder),1)
            engine.dispose()
