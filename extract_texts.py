#!/usr/bin/env python3
"""Extract the text of every document in a folder into a mirror tree of .txt files.

Usage: python3 extract_texts.py SOURCE_DIR [--out OUT_DIR] [--force]

Only new or changed files are processed: each .txt keeps the modification date of its source,
and a file is extracted again as soon as that date differs (newer or older).
PDF: text layer and form field values via PDFKit, or Vision OCR of every page (rendered at 300 dpi)
when the PDF is a scan. Word, RTF, ODT and saved web pages: textutil. Images (png, jpg, heic, tiff...): Vision OCR
through the `ocr` binary. Source files are never modified: everything is written to OUT_DIR, with a
_manifest.tsv listing every file, its status and its word count. Texts of documents deleted or
renamed since the last run are removed from OUT_DIR.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

OCR_BIN = Path(os.environ.get("DOC_INDEXER_OCR", Path(__file__).resolve().parent / "ocr"))

IMAGES = {".png", ".jpg", ".jpeg", ".heic", ".tiff", ".tif", ".webp", ".gif"}
# Word and OpenDocument files, and web pages saved from a browser (confirmation pages, e-mails)
TEXTUTIL = {".docx", ".doc", ".rtf", ".odt", ".html", ".htm", ".webarchive"}
PLAIN = {".txt", ".md", ".csv", ".json"}
SKIPPED = {".mp4", ".mov", ".m4a", ".mp3", ".textclipping"}
IGNORED_NAMES = {"Icon\r"}  # Finder's custom folder icon, not a document

# Below this many words, a PDF is treated as a scan and sent to OCR.
MIN_PDF_WORDS = 15

# A .txt starts with this line; only files listed in the manifest and starting with it are ever removed from OUT_DIR
HEADER = "# Source: "

MANIFEST = "_manifest.tsv"
MANIFEST_HEADER = "path\ttype\tsize_kb\tmodified\tstatus\twords"

# Lines added by the extraction, not by the document
MARKER = re.compile(r"\[OCR\]|\[Form fields\]|--- page \d+ ---")

# Text layer of a PDF, then the values typed into its form fields (PDFKit leaves them out of the text);
# checkboxes and radio buttons are left out: their "Off"/"Yes" values mean nothing without their label
JXA_PDF = (
    'function run(argv){ObjC.import("PDFKit");'
    'var d=$.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));var s="",f=[];'
    'if(d&&!d.isNil()){var t=d.string;if(!t.isNil()){s=t.js}'
    'for(var i=0;i<d.pageCount;i++){var a=d.pageAtIndex(i).annotations;'
    'for(var j=0;j<a.count;j++){var w=a.objectAtIndex(j),v=w.widgetStringValue;'
    'if(!v.isNil()&&v.length>0&&w.widgetFieldType.js!="/Btn"){f.push(v.js)}}}}'
    'if(f.length){s+="\\n[Form fields]\\n"+f.join("\\n")}'
    '$(s).writeToFileAtomicallyEncodingError(argv[1],true,$.NSUTF8StringEncoding,null);return "ok"}'
)

# Two dates closer than this are the same: FAT and HFS+ volumes store them to 1 or 2 seconds
MTIME_TOLERANCE = 2


class NoOCRBinary(Exception):
    """The ocr binary is not built yet: nothing is written, so the file is retried once it is."""


def run(cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.stdout


def ocr(path):
    if not OCR_BIN.exists():
        raise NoOCRBinary
    return run([str(OCR_BIN), str(path)])


def printable(path):
    """A file name as printed or written in the manifest: composed accents (as typed on a keyboard) and no
    control characters, so a crafted name cannot move the cursor or recolor the terminal."""
    return re.sub(r"[\x00-\x1f\x7f]", "?", unicodedata.normalize("NFC", str(path)))


def count_words(text):
    """Words of a text, without the lines added by the extraction."""
    return sum(len(line.split()) for line in text.splitlines() if not MARKER.fullmatch(line.strip()))


def body_words(text):
    """Words of an extracted .txt file, without its header."""
    return count_words(text.partition("\n")[2])


def pdf_text(path):
    with tempfile.TemporaryDirectory(prefix="doc-indexer-") as tmp:
        txt_file = Path(tmp) / "pdf.txt"
        run(["osascript", "-l", "JavaScript", "-e", JXA_PDF, str(path), str(txt_file)])
        txt = txt_file.read_text(encoding="utf-8", errors="ignore") if txt_file.exists() else ""
    if count_words(txt) < MIN_PDF_WORDS:
        # Little or no text layer: a scan, read by OCR on every page (the text layer wins if OCR finds less)
        scanned = ocr(path)
        if count_words(scanned) >= count_words(txt):
            txt = "[OCR]\n" + scanned
    return txt


def read_plain(path):
    """Text files exported from Windows or Excel are often UTF-16 or Windows-1252, not UTF-8."""
    data = path.read_bytes()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def extract(path):
    ext = path.suffix.lower()
    if ext == ".pdf":
        return pdf_text(path)
    if ext in TEXTUTIL:
        # -noload: a saved web page never fetches its remote images or styles, so nothing leaves the Mac
        return run(["textutil", "-noload", "-convert", "txt", "-stdout", str(path)])
    if ext in PLAIN:
        return read_plain(path)
    if ext in IMAGES:
        return "[OCR]\n" + ocr(path)
    return None


def documents(source, out, unread):
    """Files to index: not hidden, outside the output folder. Unreadable folders are reported and added to
    `unread`; links to folders or files are reported and not followed."""
    out_st = out.stat()

    def unreadable(err):
        print(f"warning: cannot read {printable(err.filename)} ({err.strerror}), not indexed", file=sys.stderr)
        unread.append(err.filename)

    for folder, subfolders, files in os.walk(source, onerror=unreadable):
        keep = []
        for name in subfolders:
            path = os.path.join(folder, name)
            if name.startswith("."):
                continue
            if os.path.islink(path):
                print(f"warning: folder link not followed, not indexed: {printable(path)}", file=sys.stderr)
                continue
            if os.path.samestat(os.stat(path), out_st):  # also true when the path differs only by letter case
                continue
            keep.append(name)
        subfolders[:] = keep
        for name in files:
            if name.startswith(".") or name in IGNORED_NAMES:
                continue
            path = Path(folder) / name
            if path.is_symlink():  # it could point to any file of the Mac, outside the folder to index
                print(f"warning: file link not followed, not indexed: {printable(path)}", file=sys.stderr)
                continue
            yield path


def index_file(p, rel, target, force):
    """Extracts one file if needed and returns its status."""
    ext = p.suffix.lower()
    if ext in SKIPPED:
        return "skipped"
    mtime = p.stat().st_mtime
    if not force and target.exists() and abs(target.stat().st_mtime - mtime) <= MTIME_TOLERANCE:
        return "up to date"
    try:
        txt = extract(p)
    except Exception as e:  # noqa: BLE001 - one bad file must not stop the run
        # Nothing is written, so the file is tried again on the next run
        return f"error: {e.__class__.__name__}"
    if txt is None:
        return "unsupported"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{HEADER}{rel}\n\n{txt}", encoding="utf-8")
    os.utime(target, (mtime, mtime))  # the .txt carries the date of the version it was extracted from
    return "extracted"


def listed(out):
    """Lines of the manifest of the previous run, by document name as written there."""
    manifest = out / MANIFEST
    if not manifest.exists():
        return {}
    lines = manifest.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]
    return {line.split("\t")[0]: line for line in lines if line}


def remove_stale(out, gone):
    """Removes the texts of documents deleted or renamed since the last run, and the folders they leave empty.
    Only texts listed in the previous manifest are ever removed: other files of OUT_DIR are left alone."""
    removed = []
    for name in gone:
        if Path(name).is_absolute() or ".." in Path(name).parts:
            continue
        t = out / (name + ".txt")
        if not t.is_file():
            continue
        with open(t, encoding="utf-8", errors="ignore") as f:
            if not f.readline().startswith(HEADER):
                continue
        t.unlink()
        removed.append(t)
    for d in sorted({p for t in removed for p in t.parents if out in p.parents}, reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    return len(removed)


def main():
    os.umask(0o077)  # texts of identity papers and bank statements: folders 0700, files 0600, for this account only
    parser = argparse.ArgumentParser(
        description="Extract the text of a folder of documents (PDF, Word, images) into .txt files."
    )
    parser.add_argument("source", type=Path, help="folder to index")
    parser.add_argument("-o", "--out", type=Path, help="output folder (default: SOURCE/.doc-index)")
    parser.add_argument("--force", action="store_true", help="re-extract every file, even unchanged ones")
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    if not source.is_dir():
        parser.error(f"not a folder: {source}")
    out_arg = args.out or args.source / ".doc-index"
    out = out_arg.expanduser().resolve()
    if out.exists() and any(os.path.samefile(out, p) for p in (source, *source.parents)):  # letter case included
        parser.error("the output folder must not be the source folder or contain it")
    try:
        os.listdir(source)
    except OSError as e:
        parser.error(f"cannot read {source}: {e.strerror}")
    if out.is_dir() and any(out.iterdir()) and not (out / MANIFEST).exists():
        parser.error(f"{out} already holds files but no {MANIFEST}: choose an empty folder or a previous output folder")
    out.mkdir(parents=True, exist_ok=True)
    if not (out / MANIFEST).exists():
        # Written first, so a first run stopped halfway leaves a folder that the next run accepts
        (out / MANIFEST).write_text(MANIFEST_HEADER + "\n", encoding="utf-8")
    if not OCR_BIN.exists():
        print(f"warning: no OCR binary at {OCR_BIN}: images and scans stay in error until it is built "
              "(swiftc -O ocr.swift -o ocr)", file=sys.stderr)

    rows = [MANIFEST_HEADER]
    counts = Counter()
    unread = []
    docs = sorted(documents(source, out, unread))
    # Texts of deleted or renamed documents go first: a folder renamed by letter case only is then
    # extracted again under its new name. If a folder could not be read, its texts are kept this time.
    previous = listed(out)
    gone = previous.keys() - {printable(p.relative_to(source)) for p in docs}
    removed = 0 if unread else remove_stale(out, gone)
    for p in docs:
        rel = p.relative_to(source)
        target = out / (str(rel) + ".txt")
        size_kb, modified, words = 0, "-", 0
        try:
            st = p.stat()
            size_kb, modified = st.st_size // 1024, datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
            status = index_file(p, rel, target, args.force)
            if status in ("extracted", "up to date"):
                words = body_words(target.read_text(encoding="utf-8", errors="ignore"))
                if words <= 3:
                    status = "empty (check)"  # on every run, not only the one that extracted the file
        except OSError as e:  # file gone during the run, name too long for the output folder...
            status = f"error: {e.__class__.__name__}"
        name = printable(rel)
        rows.append(f"{name}\t{p.suffix.lower() or '-'}\t{size_kb}\t{modified}\t{status}\t{words}")
        counts[status.split(":")[0]] += 1
        print(f"{status:<14} {words:>6}  {name}")

    if unread:
        rows += [previous[name] for name in sorted(gone)]  # texts kept this time stay listed, to be removed later
    (out / MANIFEST).write_text("\n".join(rows) + "\n", encoding="utf-8")
    summary = ", ".join(f"{n} {s}" for s, n in counts.most_common()) or "nothing to index"
    print(f"\n{sum(counts.values())} files: {summary}")
    if removed:
        print(f"{removed} text(s) of deleted or renamed documents removed")
    if unread:
        print("Some folders could not be read: the texts of their documents are kept until they can be.")
    print(f"Text and manifest in {out_arg}")


if __name__ == "__main__":
    main()
