"""Container startup and health probes; no model calls in startup/health."""
import json
import os
from pathlib import Path
import sys
import threading
import time
from urllib.request import urlopen


def initialize():
    from app import db
    from app.services.auth import signing_key
    for key in ('CONTRACT_UPLOAD_DIR','JIEBA_CACHE_DIR'):
        Path(os.environ.get(key,'/data')).mkdir(parents=True,exist_ok=True)
    signing_key()  # Fail before serving if configured signing secret is invalid.
    db.init_db()
    db.check_database_ready()
    from app.services.bootstrap_laws import bootstrap_laws
    bootstrap_laws(db.engine,Path(__file__).resolve().parents[1]/'samples/laws')


def worker():
    from app.cli import main as cli_main
    path=Path(os.environ.get('WORKER_HEARTBEAT_FILE','/data/worker-heartbeat.json'))
    path.parent.mkdir(parents=True,exist_ok=True)
    stopped=threading.Event()
    def beat():
        while not stopped.is_set():
            temporary=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
            temporary.write_text(json.dumps({'pid':os.getpid(),'time':time.time()}),encoding='utf-8')
            temporary.replace(path)
            stopped.wait(5)
    heartbeat=threading.Thread(target=beat,daemon=True);heartbeat.start()
    try:
        sys.argv=['app.cli','run-worker']
        return cli_main()
    finally:
        stopped.set();heartbeat.join(timeout=6)
        path.unlink(missing_ok=True)


def main():
    action=sys.argv[1] if len(sys.argv)>1 else 'api'
    if action=='health-api':
        with urlopen('http://127.0.0.1:8000/health/ready',timeout=3) as response:
            return 0 if response.status==200 else 1
    if action=='health-worker':
        from app import db
        path=Path(os.environ.get('WORKER_HEARTBEAT_FILE','/data/worker-heartbeat.json'))
        value=json.loads(path.read_text(encoding='utf-8'))
        if not 0<=time.time()-value['time']<30:return 1
        with db.engine.connect() as connection:connection.exec_driver_sql('SELECT id FROM users LIMIT 1')
        return 0
    if action=='api':
        initialize()
        os.execvp('uvicorn',['uvicorn','app.main:app','--host','0.0.0.0','--port','8000'])
    if action=='worker':return worker()
    # Administrative commands use the same unprivileged image and data volume.
    from app.cli import main as cli_main
    sys.argv=['app.cli']+sys.argv[1:]
    return cli_main()


if __name__=='__main__':
    raise SystemExit(main())
