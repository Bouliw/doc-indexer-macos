"""End-to-end tests on fictitious documents. Needs macOS and the ocr binary next to extract_texts.py
(swiftc -O ocr.swift -o ocr). Run: python3 -m unittest discover -s tests -v"""
import http.server
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import extract_texts  # noqa: E402

# Builds a PDF with one page per image, each page sized like A4 (no text layer: a scan)
JXA_IMAGES_TO_PDF = (
    'function run(argv){ObjC.import("PDFKit");ObjC.import("AppKit");var d=$.PDFDocument.alloc.init;'
    'for(var i=1;i<argv.length;i++){var img=$.NSImage.alloc.initWithContentsOfFile(argv[i]);var s=img.size;'
    'var k=842/Math.max(s.width,s.height);img.setSize($.NSMakeSize(s.width*k,s.height*k));'
    'd.insertPageAtIndex($.PDFPage.alloc.initWithImage(img),i-1)}d.writeToFile(argv[0]);return "ok"}'
)

# Copies a PDF and adds a filled-in text field to its first page, as Preview does when filling a form
JXA_FILL_FORM = (
    'function run(argv){ObjC.import("PDFKit");ObjC.import("AppKit");'
    'var d=$.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));'
    'var a=$.PDFAnnotation.alloc.initWithBoundsForTypeWithProperties($.NSMakeRect(72,72,300,20),'
    '$.PDFAnnotationSubtypeWidget,$());a.widgetFieldType=$.PDFAnnotationWidgetSubtypeText;'
    'a.fieldName=$("name");a.widgetStringValue=$(argv[2]);d.pageAtIndex(0).addAnnotation(a);'
    'd.writeToFile(argv[1]);return "ok"}'
)


# Adds three unticked checkboxes to the first page of a PDF
JXA_CHECKBOXES = (
    'function run(argv){ObjC.import("PDFKit");ObjC.import("AppKit");'
    'var d=$.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));var p=d.pageAtIndex(0);'
    'for(var i=0;i<3;i++){var a=$.PDFAnnotation.alloc.initWithBoundsForTypeWithProperties($.NSMakeRect(72,72+30*i,20,20),'
    '$.PDFAnnotationSubtypeWidget,$());a.widgetFieldType=$.PDFAnnotationWidgetSubtypeButton;'
    'a.widgetControlType=$.kPDFWidgetCheckBoxControl;a.fieldName=$("box"+i);a.buttonWidgetState=0;p.addAnnotation(a)}'
    'd.writeToFile(argv[1]);return "ok"}'
)

# Black text on a transparent PNG, as exported by many apps
JXA_TRANSPARENT_PNG = (
    'function run(argv){ObjC.import("AppKit");var img=$.NSImage.alloc.initWithSize($.NSMakeSize(900,200));img.lockFocus;'
    '$(argv[1]).drawAtPointWithAttributes($.NSMakePoint(20,70),'
    '$({"NSFont":$.NSFont.systemFontOfSize(48),"NSColor":$.NSColor.blackColor}));img.unlockFocus;'
    '$.NSBitmapImageRep.imageRepWithData(img.TIFFRepresentation).representationUsingTypeProperties('
    '$.NSBitmapImageFileTypePNG,$()).writeToFileAtomically(argv[0],true);return "ok"}'
)


def white_png(path, w=620, h=877):
    """A blank scan: a white PNG written with the standard library."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    rows = b''.join(b'\x00' + b'\xff' * (w * 3) for _ in range(h))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def jxa(script, *args):
    subprocess.run(['osascript', '-l', 'JavaScript', '-e', script, *map(str, args)], check=True, capture_output=True)


@unittest.skipUnless(sys.platform == 'darwin', 'needs macOS')
class ExtractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not extract_texts.OCR_BIN.exists():
            raise unittest.SkipTest('build the OCR binary first: swiftc -O ocr.swift -o ocr')
        cls.tmp = Path(tempfile.mkdtemp())
        cls.samples = cls.tmp / 'samples'
        subprocess.run([str(ROOT / 'examples' / 'make_samples.sh'), str(cls.samples)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.src = Path(tempfile.mkdtemp(dir=self.tmp)) / 'docs'
        shutil.copytree(self.samples, self.src)

    def run_extract(self, *args, **env):
        """Runs extract_texts.py and returns {path: (status, words)} from the manifest."""
        subprocess.run([sys.executable, str(ROOT / 'extract_texts.py'), str(self.src), *args],
                       check=True, capture_output=True, text=True, env=dict(os.environ, **env))
        lines = (self.src / '.doc-index' / '_manifest.tsv').read_text(encoding='utf-8').splitlines()[1:]
        return {f[0]: (f[4], int(f[5])) for f in (line.split('\t') for line in lines)}

    def status(self, *args, **env):
        return {k: v[0] for k, v in self.run_extract(*args, **env).items()}

    def text(self, rel):
        return (self.src / '.doc-index' / (rel + '.txt')).read_text(encoding='utf-8')

    def test_demo_documents(self):
        self.assertEqual(self.status(), {
            'bills/acme-energy-invoice.pdf': 'extracted', 'health/pharmacy-receipt.jpg': 'extracted',
            'housing/lease-agreement.docx': 'extracted', 'housing/rent-receipt-scan.pdf': 'extracted',
            'notes/todo.md': 'extracted', 'notes/voice-memo.m4a': 'skipped'})
        self.assertIn('RENT RECEIPT', self.text('housing/rent-receipt-scan.pdf'))
        self.assertIn('[OCR]', self.text('health/pharmacy-receipt.jpg'))
        self.assertTrue(all(s == 'up to date' for k, s in self.status().items() if not k.endswith('.m4a')))

    def test_every_page_of_a_scan_is_read(self):
        jxa(JXA_IMAGES_TO_PDF, self.src / 'three-pages.pdf', *[self.src / 'health' / 'pharmacy-receipt.jpg'] * 3)
        self.assertEqual(self.status()['three-pages.pdf'], 'extracted')
        text = self.text('three-pages.pdf')
        self.assertIn('--- page 3 ---', text)
        self.assertEqual(text.upper().count('PHARMACY'), 3)

    def test_every_page_of_a_tiff_is_read(self):
        page = self.tmp / 'page.tif'
        subprocess.run(['sips', '-s', 'format', 'tiff', str(self.src / 'health' / 'pharmacy-receipt.jpg'), '--out', str(page)],
                       check=True, capture_output=True)
        subprocess.run(['tiffutil', '-cat', str(page), str(page), '-out', str(self.src / 'fax.tif')], check=True, capture_output=True)
        self.assertEqual(self.status()['fax.tif'], 'extracted')
        self.assertIn('--- page 2 ---', self.text('fax.tif'))
        self.assertEqual(self.text('fax.tif').upper().count('PHARMACY'), 2)

    def test_form_field_values_are_kept(self):
        jxa(JXA_FILL_FORM, self.src / 'bills' / 'acme-energy-invoice.pdf', self.src / 'form.pdf', 'Jane Doe, file AB-1234')
        self.status()
        self.assertIn('[Form fields]\nJane Doe, file AB-1234', self.text('form.pdf'))

    def test_saved_web_pages(self):
        requests = []

        class Recorder(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(404)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(('127.0.0.1', 0), Recorder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}'
        page = self.src / 'notes' / 'confirmation.html'
        page.write_text(f'<html><head><link rel="stylesheet" href="{url}/style.css"></head><body><h1>Request received</h1>'
                        f'<p>Your file number is <b>AB-1234</b>.</p><img src="{url}/pixel.gif"></body></html>', encoding='utf-8')
        subprocess.run(['textutil', '-noload', '-convert', 'webarchive', str(page), '-output', str(self.src / 'notes' / 'saved.webarchive')],
                       check=True, capture_output=True)
        requests.clear()  # only the extraction is watched
        status = self.status()
        self.assertEqual((status['notes/confirmation.html'], status['notes/saved.webarchive']), ('extracted', 'extracted'))
        self.assertIn('Your file number is AB-1234', self.text('notes/confirmation.html'))
        self.assertIn('Request received', self.text('notes/saved.webarchive'))
        self.assertEqual(requests, [])  # nothing leaves the Mac, not even the images of a saved page

    def test_checkboxes_are_left_out(self):
        jxa(JXA_CHECKBOXES, self.src / 'bills' / 'acme-energy-invoice.pdf', self.src / 'form.pdf')
        self.status()
        self.assertNotIn('Off', self.text('form.pdf'))

    def test_transparent_image(self):
        jxa(JXA_TRANSPARENT_PNG, self.src / 'notes' / 'label.png', 'Invoice number 4521 paid')
        self.assertEqual(self.status()['notes/label.png'], 'extracted')
        self.assertIn('4521', self.text('notes/label.png'))

    def test_unsupported_file(self):
        (self.src / 'notes' / 'drawing.sketch').write_bytes(b'PK\x03\x04')
        self.assertEqual(self.status()['notes/drawing.sketch'], 'unsupported')

    def test_windows_and_utf16_text_files(self):
        (self.src / 'notes' / 'bank.csv').write_bytes('Item;Amount\nCafé crème;3,20\n'.encode('cp1252'))
        (self.src / 'notes' / 'export.csv').write_text('Crème brûlée;6,50\n', encoding='utf-16')
        self.status()
        self.assertIn('Café crème', self.text('notes/bank.csv'))
        self.assertIn('Crème brûlée', self.text('notes/export.csv'))

    def test_blank_scan_stays_flagged(self):
        white_png(self.tmp / 'white.png')
        jxa(JXA_IMAGES_TO_PDF, self.src / 'blank-scan.pdf', self.tmp / 'white.png')
        self.assertEqual(self.run_extract()['blank-scan.pdf'], ('empty (check)', 0))  # page markers are not words
        self.assertEqual(self.run_extract()['blank-scan.pdf'], ('empty (check)', 0))  # not "up to date" on later runs

    @unittest.skipIf(os.geteuid() == 0, 'root can read any file')
    def test_failed_file_is_retried(self):
        broken = self.src / 'notes' / 'unreadable.txt'
        broken.write_text('A note that cannot be read yet.', encoding='utf-8')
        broken.chmod(0)
        self.addCleanup(broken.chmod, 0o644)
        self.assertEqual(self.status()['notes/unreadable.txt'], 'error: PermissionError')
        self.assertEqual(self.status()['notes/unreadable.txt'], 'error: PermissionError')
        broken.chmod(0o644)
        self.assertEqual(self.status()['notes/unreadable.txt'], 'extracted')

    def test_images_wait_for_the_ocr_binary(self):
        status = self.status(DOC_INDEXER_OCR=str(self.tmp / 'not-built-yet'))
        self.assertEqual(status['health/pharmacy-receipt.jpg'], 'error: NoOCRBinary')
        self.assertEqual(status['housing/rent-receipt-scan.pdf'], 'error: NoOCRBinary')
        self.assertEqual(self.status()['health/pharmacy-receipt.jpg'], 'extracted')  # once the binary exists

    def test_document_replaced_by_an_older_copy(self):
        self.status()
        older = self.tmp / 'older.md'
        older.write_text('Older version of the note, kept on a USB stick.\n', encoding='utf-8')
        os.utime(older, (1_600_000_000, 1_600_000_000))  # September 2020
        shutil.copy2(older, self.src / 'notes' / 'todo.md')
        self.assertEqual(self.status()['notes/todo.md'], 'extracted')
        self.assertIn('USB stick', self.text('notes/todo.md'))

    def test_texts_of_deleted_or_renamed_documents_are_removed(self):
        self.status()
        (self.src / 'bills' / 'acme-energy-invoice.pdf').rename(self.src / 'bills' / 'acme-september.pdf')
        (self.src / 'housing' / 'lease-agreement.docx').unlink()
        self.status()
        texts = sorted(str(p.relative_to(self.src / '.doc-index')) for p in (self.src / '.doc-index').rglob('*.txt'))
        self.assertEqual(texts, ['bills/acme-september.pdf.txt', 'health/pharmacy-receipt.jpg.txt',
                                 'housing/rent-receipt-scan.pdf.txt', 'notes/todo.md.txt'])

    @unittest.skipIf(os.geteuid() == 0, 'root can read any folder')
    def test_unreadable_folder_keeps_its_texts(self):
        self.status()
        folder = self.src / 'housing'
        folder.chmod(0)
        self.addCleanup(folder.chmod, 0o755)
        self.status()
        self.assertTrue((self.src / '.doc-index' / 'housing' / 'lease-agreement.docx.txt').exists())

    def test_folder_renamed_by_letter_case(self):
        self.status()
        (self.src / 'housing').rename(self.src / 'Housing')
        status = self.status()
        self.assertEqual(status['Housing/lease-agreement.docx'], 'extracted')
        names = {p.name for p in (self.src / '.doc-index').iterdir()}
        self.assertIn('Housing', names)
        self.assertIn('lease-agreement.docx.txt', {p.name for p in (self.src / '.doc-index' / 'Housing').iterdir()})

    def test_output_folder_containing_the_source_is_refused(self):
        r = subprocess.run([sys.executable, str(ROOT / 'extract_texts.py'), str(self.src), '--out', str(self.src.parent)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn('must not be the source folder or contain it', r.stderr)
        same_folder_other_case = str(self.src.parent / self.src.name.upper())  # macOS ignores letter case
        r = subprocess.run([sys.executable, str(ROOT / 'extract_texts.py'), str(self.src), '--out', same_folder_other_case],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_original_files_are_never_modified(self):
        before = {p: p.stat().st_mtime for p in self.src.rglob('*') if p.is_file()}
        self.run_extract('--force')
        self.assertEqual(before, {p: p.stat().st_mtime for p in before})


class SafetyTest(unittest.TestCase):
    """Plain text files only: fast checks that need no OCR."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / 'docs'
        (self.src / 'notes').mkdir(parents=True)
        (self.src / 'notes' / 'todo.md').write_text('Renew the home insurance before November.\n', encoding='utf-8')

    def run_extract(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'extract_texts.py'), str(self.src), *args],
                              capture_output=True, text=True, env=dict(os.environ, DOC_INDEXER_OCR=str(self.tmp / 'no-ocr')))

    def test_control_characters_are_not_printed(self):
        (self.tmp / 'elsewhere').mkdir()
        (self.src / '\x1b[31mred').symlink_to(self.tmp / 'elsewhere')
        r = self.run_extract()
        self.assertEqual(r.returncode, 0)
        self.assertIn('folder link not followed', r.stderr)
        self.assertIn('?[31mred', r.stderr)
        self.assertNotIn('\x1b', r.stderr + r.stdout)

    @unittest.skipIf(os.geteuid() == 0, 'root can read any folder')
    def test_control_characters_are_not_printed_for_unreadable_folders(self):
        folder = self.src / '\x1b[31mlocked'
        folder.mkdir()
        folder.chmod(0)
        self.addCleanup(folder.chmod, 0o755)
        r = self.run_extract()
        self.assertIn('cannot read', r.stderr)
        self.assertIn('?[31mlocked', r.stderr)
        self.assertNotIn('\x1b', r.stderr + r.stdout)

    def test_only_texts_of_the_manifest_are_removed(self):
        out = self.tmp / 'out'
        (self.src / 'notes' / 'old.md').write_text('An old note that will be deleted.\n', encoding='utf-8')
        self.assertEqual(self.run_extract('--out', str(out)).returncode, 0)
        own = out / 'mine' / 'article.txt'  # a file of the user that happens to look like one of ours
        own.parent.mkdir()
        own.write_text('# Source: a newspaper article\n\nMy own notes on it.\n', encoding='utf-8')
        (self.src / 'notes' / 'old.md').unlink()
        self.assertEqual(self.run_extract('--out', str(out)).returncode, 0)
        self.assertFalse((out / 'notes' / 'old.md.txt').exists())
        self.assertTrue((out / 'notes' / 'todo.md.txt').exists())
        self.assertEqual(own.read_text(encoding='utf-8'), '# Source: a newspaper article\n\nMy own notes on it.\n')

    def test_folder_with_files_but_no_manifest_is_refused(self):
        out = self.tmp / 'Documents'
        out.mkdir()
        (out / 'letter.txt').write_text('# Source: a letter I am writing\n', encoding='utf-8')
        r = self.run_extract('--out', str(out))
        self.assertEqual(r.returncode, 2)
        self.assertIn('no _manifest.tsv', r.stderr)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['letter.txt'])

    @unittest.skipIf(os.geteuid() == 0, 'root can read any folder')
    def test_texts_kept_while_a_folder_is_unreadable_are_removed_later(self):
        self.run_extract()
        folder = self.src / 'notes'
        folder.chmod(0)
        self.addCleanup(folder.chmod, 0o755)
        self.run_extract()
        folder.chmod(0o755)
        (folder / 'todo.md').unlink()
        self.run_extract()
        self.assertFalse((self.src / '.doc-index' / 'notes' / 'todo.md.txt').exists())

    def test_empty_folder_and_stopped_first_run_are_accepted(self):
        out = self.tmp / 'out'
        out.mkdir()
        self.assertEqual(self.run_extract('--out', str(out)).returncode, 0)
        (out / '_manifest.tsv').write_text('path\ttype\tsize_kb\tmodified\tstatus\twords\n', encoding='utf-8')
        self.assertEqual(self.run_extract('--out', str(out)).returncode, 0)  # as left by a run stopped halfway
        self.assertIn('notes/todo.md\t', (out / '_manifest.tsv').read_text(encoding='utf-8'))

    def test_texts_are_readable_by_their_owner_only(self):
        old = os.umask(0o022)  # the usual default, which left the texts readable by every account
        self.addCleanup(os.umask, old)
        self.run_extract()
        out = self.src / '.doc-index'
        for p in (out, out / 'notes'):
            self.assertEqual(p.stat().st_mode & 0o777, 0o700, p)
        for p in (out / 'notes' / 'todo.md.txt', out / '_manifest.tsv'):
            self.assertEqual(p.stat().st_mode & 0o777, 0o600, p)

    def test_file_links_are_not_followed(self):
        secret = self.tmp / 'credentials'
        secret.write_text('aws_secret_access_key = not-a-real-key\n', encoding='utf-8')
        (self.src / 'notes' / 'innocent.txt').symlink_to(secret)
        r = self.run_extract()
        self.assertEqual(r.returncode, 0)
        self.assertIn('file link not followed, not indexed', r.stderr)
        out = self.src / '.doc-index'
        self.assertFalse((out / 'notes' / 'innocent.txt.txt').exists())
        self.assertNotIn('innocent.txt', (out / '_manifest.tsv').read_text(encoding='utf-8'))
        self.assertTrue((out / 'notes' / 'todo.md.txt').exists())


class WordsTest(unittest.TestCase):
    def test_header_and_markers_are_not_words(self):
        text = '# Source: a/b c.pdf\n\n[OCR]\n--- page 1 ---\n\n\n--- page 2 ---\nTwo words\n[Form fields]\nJane'
        self.assertEqual(extract_texts.body_words(text), 3)


if __name__ == '__main__':
    unittest.main()
