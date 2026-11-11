import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import yaml
from sqlalchemy.orm import sessionmaker
from app import db
from app.container_entrypoint import initialize,main
from app.services.auth import signing_key
from tests.parse_helpers import ROOT


class ContainerSetupTests(unittest.TestCase):
    def test_compose_topology_and_context_isolation(self):
        config=yaml.safe_load((ROOT/'docker-compose.yml').read_text(encoding='utf-8'))
        services=config['services'];self.assertEqual(set(services),{'api','worker','nginx'})
        self.assertEqual(services['nginx']['ports'],['80:80'])
        for name in ['api','worker']:
            self.assertNotIn('ports',services[name]);self.assertEqual(services[name]['volumes'],['./data:/data'])
            self.assertEqual(services[name]['environment']['DATABASE_URL'],'sqlite:////data/law.db')
            self.assertIn('healthcheck',services[name])
        self.assertEqual(services['worker']['restart'],'unless-stopped')
        self.assertEqual(services['worker']['depends_on']['api']['condition'],'service_healthy')
        dockerfile=(ROOT/'Dockerfile').read_text(encoding='utf-8')
        self.assertIn('FROM python:3.9-slim',dockerfile);self.assertIn('USER 10001:10001',dockerfile)
        self.assertNotIn('COPY . ',dockerfile)
        ignored=(ROOT/'.dockerignore').read_text(encoding='utf-8')
        for item in ['.venv/','work/','law.db','data/','.env','samples/*.tmp']:self.assertIn(item,ignored)

    def test_startup_initializes_ten_tables_idempotently_and_worker_probe(self):
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d);engine=db.create_db_engine('sqlite:///'+(directory/'law.db').as_posix())
            env={'JWT_SECRET':'x'*64,'CONTRACT_UPLOAD_DIR':str(directory/'uploads'),'JIEBA_CACHE_DIR':str(directory/'index'),
                 'WORKER_HEARTBEAT_FILE':str(directory/'heartbeat.json')}
            try:
                with patch.dict(os.environ,env),patch.object(db,'engine',engine),patch.object(db,'SessionLocal',sessionmaker(bind=engine)):
                    initialize();initialize()
                    self.assertEqual(signing_key(),'x'*64)
                    with engine.connect() as c:
                        self.assertEqual(c.exec_driver_sql("SELECT count(*) FROM sqlite_master WHERE type='table'").scalar_one(),12)
                        self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM llm_logs').scalar_one(),0)
                    (directory/'heartbeat.json').write_text(json.dumps({'time':time.time(),'pid':123}),encoding='utf-8')
                    with patch('sys.argv',['entrypoint','health-worker']):self.assertEqual(main(),0)
                    (directory/'heartbeat.json').write_text(json.dumps({'time':time.time()-90,'pid':123}),encoding='utf-8')
                    with patch('sys.argv',['entrypoint','health-worker']):self.assertEqual(main(),1)
            finally:engine.dispose()
