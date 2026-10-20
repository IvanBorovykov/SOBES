"""Explicit user-selected document text for voice questions. No directory crawling."""

import json
import threading
import zipfile
from pathlib import Path
from xml.etree import ElementTree

TEXT_TYPES = {'.txt', '.md', '.csv', '.json', '.yaml', '.yml', '.py', '.js', '.ts', '.tsx', '.jsx',
              '.html', '.css', '.sql', '.log', '.rst'}
SUPPORTED_TYPES = TEXT_TYPES | {'.pdf', '.docx'}
MAX_FILES = 20
MAX_CHARS = 300_000


def validate_paths(paths):
    if len(paths) > MAX_FILES:
        raise ValueError(f'Можно выбрать не больше {MAX_FILES} файлов.')
    result = []
    for value in paths:
        path = Path(value).expanduser().resolve(strict=True)
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_TYPES:
            raise ValueError(f'Неподдерживаемый файл: {path.name}. Используйте текст, PDF или DOCX.')
        limit = 20_000_000 if path.suffix.lower() in {'.pdf', '.docx'} else 2_000_000
        if path.stat().st_size > limit:
            raise ValueError(f'Слишком большой файл: {path.name}. Максимум {limit // 1_000_000} МБ.')
        if str(path) not in result:
            result.append(str(path))
    return tuple(result)


class FileContext:
    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()

    def _read(self, path):
        stat = path.stat()
        stamp = (str(path), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        with self._lock:
            cached = self._cache.get(stamp)
        if cached is not None:
            return cached
        if path.suffix.lower() == '.pdf':
            from Quartz import PDFDocument
            from Foundation import NSURL
            document = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(str(path)))
            if document is None or document.isLocked():
                raise ValueError(f'Не удалось прочитать PDF: {path.name}. Проверьте пароль.')
            if document.pageCount() > 1000:
                raise ValueError(f"Слишком много страниц в {path.name}. Максимум 1000.")
            pages = []
            length = 0
            missing = []
            for index in range(document.pageCount()):
                text = str(document.pageAtIndex_(index).string() or '')
                length += len(text)
                if length > MAX_CHARS:
                    raise ValueError(f'Слишком много текста в {path.name}. Выберите нужные страницы отдельно.')
                if text.strip():
                    pages.append({'page': index + 1, 'text': text})
                else:
                    missing.append(index + 1)
            if missing:
                pages_text = ', '.join(map(str, missing[:20]))
                raise ValueError(f'PDF {path.name}: страницы без извлекаемого текста ({pages_text}). '
                                 'Возможно, это сканы или пустые страницы. Для сканов нужен OCR; '
                                 'уберите пустые страницы, чтобы использовать весь документ без пропусков.')
            content = pages
        elif path.suffix.lower() == '.docx':
            with zipfile.ZipFile(path) as archive:
                info = archive.getinfo('word/document.xml')
                if info.file_size > 4_000_000:
                    raise ValueError(f'Слишком много текста в {path.name}.')
                root = ElementTree.fromstring(archive.read(info))
            namespace = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            lines = []
            for paragraph in root.findall('.//w:p', namespace):
                pieces = []
                for element in paragraph.iter():
                    if element.tag == '{' + namespace['w'] + '}t':
                        pieces.append(element.text or '')
                    elif element.tag == '{' + namespace['w'] + '}tab':
                        pieces.append('\t')
                    elif element.tag in {'{' + namespace['w'] + '}br', '{' + namespace['w'] + '}cr'}:
                        pieces.append('\n')
                lines.append(''.join(pieces))
            content = self._lines('\n'.join(lines))
        else:
            data = path.read_bytes()
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = data.decode('utf-16') if data.startswith((b'\xff\xfe', b'\xfe\xff')) else data.decode('cp1251')
            if '\x00' in text:
                raise ValueError(f'Это не текстовый файл: {path.name}.')
            content = self._lines(text)
        if not content:
            raise ValueError(f'Нет извлекаемого текста: {path.name}. Для скана PDF сначала нужен OCR.')
        after = path.stat()
        if (after.st_mtime_ns, after.st_ctime_ns, after.st_size) != stamp[1:]:
            raise ValueError(f'Файл {path.name} изменился во время чтения. Повторите запрос.')
        with self._lock:
            self._cache = {key: value for key, value in self._cache.items() if key[0] != str(path)}
            self._cache[stamp] = content
            while len(self._cache) > MAX_FILES:
                self._cache.pop(next(iter(self._cache)))
        return content

    @staticmethod
    def _lines(text):
        if len(text) > MAX_CHARS:
            raise ValueError('Слишком много текста в одном файле. Выберите меньший фрагмент.')
        return [{'line': index, 'text': line} for index, line in enumerate(text.splitlines(), 1) if line.strip()]

    def load(self, paths):
        if not paths:
            raise ValueError('Добавьте документы кнопкой «Файлы…», затем задайте вопрос вслух.')
        paths = validate_paths(paths)
        documents = [{'source': f'F{index}', 'name': Path(value).name, 'content': self._read(Path(value))}
                     for index, value in enumerate(paths, 1)]
        payload = json.dumps(documents, ensure_ascii=False)
        if len(payload) > MAX_CHARS:
            raise ValueError(f'Общий контекст слишком большой (лимит {MAX_CHARS} символов). Уберите лишние файлы.')
        return payload, tuple(f'F{index}: {Path(value).name}' for index, value in enumerate(paths, 1))
