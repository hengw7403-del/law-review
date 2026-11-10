"""Empty-container-database bootstrap; existing knowledge is never overwritten."""
import logging
from pathlib import Path
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.models import KnowledgeChunk
from app.services.knowledge import import_law,enable_source
from app.services.chunker import SourceInfo

logger=logging.getLogger(__name__)

def bootstrap_laws(engine,directory):
    imported=0
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            if connection.scalar(select(func.count()).select_from(KnowledgeChunk)):
                connection.rollback();return 0
            files=sorted(Path(directory).glob('*.txt'))
            if not files:logger.warning('法规自动导入：目录中没有txt文件：%s',directory)
            for path in files:
                try:
                    with connection.begin_nested():
                        with Session(bind=connection,join_transaction_mode='create_savepoint') as session:
                            result=import_law(session,path.read_text(encoding='utf-8'),path.name,SourceInfo(path.stem,path.stem,source_locator=path.resolve().as_uri()))
                            enable_source(session,path.stem)
                            imported+=result['imported']
                except Exception as exc:
                    logger.warning('法规自动导入失败：%s (%s)',path.name,type(exc).__name__)
            connection.commit()
        logger.warning('已自动导入 %s 条法规（%s 个来源文件）；已启用',imported,len(files))
    except Exception as exc:
        logger.warning('法规自动导入未完成，继续启动：%s',type(exc).__name__)
    return imported
