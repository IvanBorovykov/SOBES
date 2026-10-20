import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from file_context import FileContext, validate_paths


class FileContextTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.context = FileContext()

    def test_text_has_source_ids_and_original_line_numbers(self):
        path = self.root / 'guide.md'
        path.write_text('First\n\nAnswer: 42\n')
        text, sources = self.context.load([str(path)])
        self.assertIn('"line": 3', text)
        self.assertIn('Answer: 42', text)
        self.assertEqual(sources, ('F1: guide.md',))
        self.assertNotIn(str(self.root), text)

    def test_changes_are_reloaded_and_deleted_files_are_reported(self):
        path = self.root / 'guide.txt'
        path.write_text('old')
        self.context.load([str(path)])
        path.write_text('new content')
        self.assertIn('new content', self.context.load([str(path)])[0])
        path.unlink()
        with self.assertRaises(OSError):
            self.context.load([str(path)])

    def test_cached_file_is_not_extracted_twice(self):
        path = self.root / 'guide.txt'
        path.write_text('cached')
        self.context.load([str(path)])
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('must use cache')):
            self.assertIn('cached', self.context.load([str(path)])[0])

    def test_docx_paragraphs_and_tables_have_text(self):
        path = self.root / 'guide.docx'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Answer</w:t><w:tab/><w:t>code</w:t><w:br/><w:t>next line</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>42</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        text, _ = self.context.load([str(path)])
        self.assertIn('Answer\\tcode', text)
        self.assertIn('next line', text)
        self.assertIn('42', text)

    def test_empty_or_unsupported_files_fail_without_silent_omission(self):
        path = self.root / 'empty.txt'
        path.write_text('')
        with self.assertRaisesRegex(ValueError, 'Нет извлекаемого'):
            self.context.load([str(path)])
        unsupported = self.root / 'data.bin'
        unsupported.write_bytes(b'data')
        with self.assertRaisesRegex(ValueError, 'Неподдерживаемый'):
            validate_paths([str(unsupported)])
        with self.assertRaisesRegex(ValueError, 'Добавьте документы'):
            self.context.load([])

    def test_context_limit_fails_instead_of_truncating(self):
        path = self.root / 'large.txt'
        path.write_text('x' * 300001)
        with self.assertRaisesRegex(ValueError, 'Слишком много'):
            self.context.load([str(path)])

    def test_duplicate_basenames_keep_distinct_citation_ids(self):
        a, b = self.root / 'a', self.root / 'b'
        a.mkdir()
        b.mkdir()
        for directory, content in ((a, 'first'), (b, 'second')):
            (directory / 'same.txt').write_text(content)
        text, sources = self.context.load([str(a/'same.txt'), str(b/'same.txt')])
        self.assertEqual(sources, ('F1: same.txt', 'F2: same.txt'))
        self.assertIn('first', text)
        self.assertIn('second', text)

    def test_pdf_pages_are_extracted_with_page_numbers(self):
        from PIL import Image
        path = self.root / 'scan.pdf'
        Image.new('RGB', (50, 50), 'white').save(path, 'PDF')
        with self.assertRaisesRegex(ValueError, 'OCR'):
            self.context.load([str(path)])

    def test_mixed_pdf_does_not_silently_skip_scanned_pages(self):
        from Quartz import PDFDocument, PDFPage
        from Foundation import NSURL
        from PIL import Image
        # Reuse a generated text PDF and append a real image-only page.
        self.test_text_pdf_keeps_page_citations()
        path = self.root / 'text.pdf'
        scan = self.root / 'scan.pdf'
        Image.new('RGB', (50, 50), 'white').save(scan, 'PDF')
        text_document = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(str(path)))
        scan_document = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(str(scan)))
        text_document.insertPage_atIndex_(scan_document.pageAtIndex_(0), 1)
        mixed = self.root / 'mixed.pdf'
        self.assertTrue(text_document.writeToFile_(str(mixed)))
        with self.assertRaisesRegex(ValueError, 'страницы без извлекаемого текста.*2'):
            self.context.load([str(mixed)])

    def test_text_pdf_keeps_page_citations(self):
        path = self.root / 'text.pdf'
        stream = b'BT /F1 12 Tf 20 100 Td (The answer is 42.) Tj ET'
        objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
                   b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
                   b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
                   b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
                   b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
        pdf = bytearray(b'%PDF-1.4\n')
        offsets = [0]
        for index, value in enumerate(objects, 1):
            offsets.append(len(pdf))
            pdf.extend(f'{index} 0 obj\n'.encode() + value + b'\nendobj\n')
        xref = len(pdf)
        pdf.extend(b'xref\n0 6\n0000000000 65535 f \n')
        for offset in offsets[1:]:
            pdf.extend(f'{offset:010d} 00000 n \n'.encode())
        pdf.extend(f'trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode())
        path.write_bytes(pdf)
        text, _ = self.context.load([str(path)])
        self.assertIn('The answer is 42.', text)
        self.assertIn('"page": 1', text)



if __name__ == '__main__':
    unittest.main()
