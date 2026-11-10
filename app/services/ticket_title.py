from pathlib import PurePosixPath

def contract_title(title, filename=''):
    value = (title or '').strip() or PurePosixPath((filename or '').replace('\\','/')).name
    if PurePosixPath(value).suffix.lower() in {'.txt', '.pdf', '.docx', '.md'}:
        value = PurePosixPath(value).stem
    return value or '未命名合同'


def ticket_title(contract):
    return contract_title(contract.title, contract.filename) + ' · 条款审查'
