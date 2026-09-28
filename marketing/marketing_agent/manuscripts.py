"""Extract text-only manuscripts without executing document content."""
from io import BytesIO
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TEXT_LENGTH = 100000
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


def validate_manuscript(text):
    from .presentation import parse_manuscript
    if not text.strip() or len(text) > MAX_TEXT_LENGTH or '\x00' in text:
        raise ValueError('请提供有正文的文稿，最多 10 万字')
    parse_manuscript(text)
    return text


def extract_manuscript(filename, content):
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError('文件不能为空，且不能超过 5 MB')
    suffix = Path(filename).suffix.lower()
    notice = ''
    if suffix in {'.txt', '.md'}:
        try:
            text = content.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                text = content.decode('gb18030')
            except UnicodeDecodeError:
                raise ValueError('文本编码无法识别，请另存为 UTF-8 后上传') from None
    elif suffix == '.docx':
        try:
            with ZipFile(BytesIO(content)) as archive:
                info = archive.getinfo('word/document.xml')
                if info.file_size > 10 * 1024 * 1024:
                    raise ValueError('Word 文档正文过大，请精简后上传')
                xml = archive.read(info)
            if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                raise ValueError('Word 文档包含不支持的 XML 定义')
            root = ET.fromstring(xml)
            body = root.find('w:body', NS)
            if body is None:
                raise ValueError('Word 文档没有正文')
            def paragraph(node):
                parts = []
                for el in node.iter():
                    if el.tag == '{'+NS['w']+'}t': parts.append(el.text or '')
                    elif el.tag == '{'+NS['w']+'}tab': parts.append(' ')
                    elif el.tag == '{'+NS['w']+'}br': parts.append('\n')
                return ''.join(parts).strip()
            chunks = []
            for node in body:
                if node.tag == '{'+NS['w']+'}p':
                    value = paragraph(node)
                    style = node.find('w:pPr/w:pStyle', NS)
                    style_id = style.get('{'+NS['w']+'}val', '') if style is not None else ''
                    heading = re.fullmatch(r'(?:Heading|标题)\s*([1-6])', style_id, re.I)
                    if heading: value = '#' * int(heading[1]) + ' ' + value
                    if value: chunks.append(value)
                elif node.tag == '{'+NS['w']+'}tbl':
                    rows = [[' / '.join(paragraph(p) for p in cell.findall('w:p', NS)).replace('|', '\\|').replace('\n', ' ')
                             for cell in row.findall('w:tc', NS)] for row in node.findall('w:tr', NS)]
                    rows = [row for row in rows if row]
                    if rows:
                        width = max(map(len, rows))
                        rows = [row + [''] * (width - len(row)) for row in rows]
                        table = ['| ' + ' | '.join(row) + ' |' for row in rows]
                        table.insert(1, '| ' + ' | '.join(['---'] * width) + ' |')
                        chunks.append('\n'.join(table))
            text = '\n\n'.join(chunks)
            notice = '已提取 Word 正文和表格，请核对预览；图片、页眉页脚、批注及复杂版式不导入。'
        except (BadZipFile, KeyError, ET.ParseError, RuntimeError) as exc:
            raise ValueError('无法读取 Word 文件，请上传有效的 .docx 文档') from exc
    else:
        raise ValueError('支持 .docx、.md 和 .txt 文件；旧版 .doc 请另存为 .docx')
    validate_manuscript(text)
    title = next((line.lstrip('#').strip() for line in text.splitlines() if line.strip()), Path(filename).stem)
    return {'text': text, 'title': title[:120], 'filename': Path(filename).name, 'notice': notice}
