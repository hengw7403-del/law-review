"""Extract text and source spans without writing files or database records."""

from io import BytesIO
from pathlib import PurePosixPath
from typing import List, Tuple, TypedDict
import zipfile

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from lxml.etree import XMLSyntaxError
import pdfplumber
from pdfplumber.utils.exceptions import PdfminerException
from pdfminer.psexceptions import PSException


MAX_FILE_SIZE = 20 * 1024 * 1024


class TextSpan(TypedDict):
    char_start: int
    char_end: int
    location: str


class ParseError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def display_filename(filename: str) -> str:
    return PurePosixPath(filename.replace("\\", "/")).name


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def paragraph_ranges(text: str) -> List[Tuple[int, int]]:
    """Return nonempty, blank-line-separated paragraph ranges, end exclusive."""
    ranges = []  # type: List[Tuple[int, int]]
    start = None
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.strip():
            if start is None:
                start = offset
        elif start is not None:
            ranges.append((start, offset))
            start = None
        offset += len(line)
    if start is not None:
        ranges.append((start, len(text)))
    return ranges


def parse_pdf(data: bytes) -> Tuple[str, List[TextSpan]]:
    parts = []  # type: List[str]
    page_map = []  # type: List[TextSpan]
    offset = 0
    with pdfplumber.open(BytesIO(data)) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            text = normalize_newlines(page.extract_text() or "")
            if parts:
                parts.append("\n\n")
                offset += 2
            parts.append(text)
            # A page can have several paragraphs, all pointing to the same page.
            for start, end in paragraph_ranges(text):
                page_map.append({
                    "char_start": offset + start,
                    "char_end": offset + end,
                    "location": "p{}".format(page_number),
                })
            offset += len(text)
    return "".join(parts), page_map


def parse_docx(data: bytes) -> Tuple[str, List[TextSpan]]:
    document = Document(BytesIO(data))
    parts = []  # type: List[str]
    page_map = []  # type: List[TextSpan]
    offset = 0
    for paragraph_number, paragraph in enumerate(document.paragraphs, 1):
        text = normalize_newlines(paragraph.text)
        if parts:
            parts.append("\n\n")
            offset += 2
        parts.append(text)
        if text.strip():
            page_map.append({
                "char_start": offset,
                "char_end": offset + len(text),
                "location": "para{}".format(paragraph_number),
            })
        offset += len(text)
    return "".join(parts), page_map


def parse_txt(data: bytes) -> Tuple[str, List[TextSpan]]:
    try:
        text = normalize_newlines(data.decode("utf-8-sig", errors="strict"))
    except UnicodeDecodeError as exc:
        raise ParseError("GARBLED", "TXT 文件不是有效的 UTF-8 编码，请转换编码后重试") from exc
    page_map = [
        {"char_start": start, "char_end": end, "location": "para{}".format(number)}
        for number, (start, end) in enumerate(paragraph_ranges(text), 1)
    ]
    return text, page_map


def parse_document(filename: str, data: bytes) -> Tuple[str, List[TextSpan]]:
    """Dispatch by extension and validate actual contents before extraction."""
    if len(data) > MAX_FILE_SIZE:
        raise ParseError("FILE_TOO_LARGE", "单文件不得超过 20 MB", 413)
    extension = PurePosixPath(display_filename(filename)).suffix.lower()
    if extension not in {".pdf", ".docx", ".txt"}:
        raise ParseError("UNSUPPORTED_FORMAT", "仅支持 PDF、DOCX 和 UTF-8 TXT 文件", 415)
    if extension == ".pdf" and not data.startswith(b"%PDF"):
        raise ParseError("UNSUPPORTED_FORMAT", "文件扩展名与 PDF 文件头不符", 415)
    if extension == ".docx":
        if not zipfile.is_zipfile(BytesIO(data)):
            raise ParseError("UNSUPPORTED_FORMAT", "DOCX 文件必须是有效的 ZIP/OOXML 文件", 415)
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                names = set(archive.namelist())
                if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                    raise ParseError("UNSUPPORTED_FORMAT", "ZIP 文件不包含有效的 DOCX 正文", 415)
                if any(info.flag_bits & 1 for info in archive.infolist()):
                    raise ParseError("UNSUPPORTED_FORMAT", "暂不支持加密的 DOCX 文件", 415)
                if sum(info.file_size for info in archive.infolist()) > 100 * 1024 * 1024:
                    raise ParseError("UNSUPPORTED_FORMAT", "DOCX 解压内容超限", 415)
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            raise ParseError("UNSUPPORTED_FORMAT", "DOCX 压缩结构损坏或无法读取", 415) from exc
    if extension == ".txt" and (
        data.startswith((b"MZ", b"%PDF", b"PK\x03\x04")) or b"\x00" in data
    ):
        raise ParseError("UNSUPPORTED_FORMAT", "文件内容不是受支持的纯文本", 415)

    try:
        if extension == ".pdf":
            return parse_pdf(data)
        if extension == ".docx":
            return parse_docx(data)
        return parse_txt(data)
    except ParseError:
        raise
    except (PSException, PdfminerException, PackageNotFoundError, XMLSyntaxError,
            zipfile.BadZipFile, EOFError, KeyError, ValueError, OSError) as exc:
        raise ParseError("UNSUPPORTED_FORMAT", "文件损坏、加密或无法按指定格式读取", 415) from exc
