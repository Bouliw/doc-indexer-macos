#!/usr/bin/env python3
"""Extract the text of every document in a folder into a mirror tree of .txt files.

Usage: python3 extract_texts.py SOURCE_DIR [--out OUT_DIR] [--force]

Only new or modified files are processed (incremental run, based on modification times).
PDF: text layer via PDFKit, or Vision OCR of every page (rendered at 300 dpi) when the PDF is a scan.
Word, RTF, ODT: textutil. Images (png, jpg, heic, tiff...): Vision OCR through the `ocr` binary.
Source files are never modified: everything is written to OUT_DIR, with a _manifest.tsv
listing every file, its status and its word count.
"""
import argparse
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

OCR_BIN = Path(os.environ.get("DOC_INDEXER_OCR", Path(__file__).resolve().parent / "ocr"))

IMAGES = {".png", ".jpg", ".jpeg", ".heic", ".tiff", ".tif", ".webp", ".gif"}
OFFICE = {".docx", ".doc", ".rtf", ".odt"}
PLAIN = {".txt", ".md", ".csv", ".json"}
SKIPPED = {".mp4", ".mov", ".m4a", ".mp3", ".textclipping"}

# Below this many words, a PDF is treated as a scan and sent to OCR.
MIN_PDF_WORDS = 15

JXA_PDF = (
    'function run(argv){ObjC.import("PDFKit");'
    'var d=$.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));'
    'var s=(!d||d.isNil())?$(""):d.string;if(s.isNil()){s=$("")};'
    's.writeToFileAtomicallyEncodingError(argv[1],true,$.NSUTF8StringEncoding,null);return "ok"}'
)


def run(cmd, timeout=600):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return r.stdout


def ocr(path):
    return run([str(OCR_BIN), str(path)]) if OCR_BIN.exists() else ""


def pdf_text(path):
    with tempfile.TemporaryDirectory(prefix="doc-indexer-") as tmp:
        txt_file = Path(tmp) / "pdf.txt"
        run(["osascript", "-l", "JavaScript", "-e", JXA_PDF, str(path), str(txt_file)])
        txt = txt_file.read_text(encoding="utf-8", errors="ignore") if txt_file.exists() else ""
    if len(txt.split()) < MIN_PDF_WORDS:
        # Scanned PDF (no text layer): OCR of every page
        txt = "[OCR]\n" + ocr(path)
    return txt


def extract(path):
    ext = path.suffix.lower()
    if ext == ".pdf":
        return pdf_text(path)
    if ext in OFFICE:
        return run(["textutil", "-convert", "txt", "-stdout", str(path)])
    if ext in PLAIN:
        return path.read_text(encoding="utf-8", errors="ignore")
    if ext in IMAGES:
        return "[OCR]\n" + ocr(path)
    return None


def main():
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
    out.mkdir(parents=True, exist_ok=True)
    if not OCR_BIN.exists():
        print(f"warning: no OCR binary at {OCR_BIN}, images and scans will be empty "
              "(build it with: swiftc -O ocr.swift -o ocr)", file=sys.stderr)

    rows = ["path\ttype\tsize_kb\tmodified\tstatus\twords"]
    counts = Counter()
    for p in sorted(source.rglob("*")):
        rel = p.relative_to(source)
        if p.is_dir() or out in p.parents or any(x.startswith(".") for x in rel.parts):
            continue
        ext = p.suffix.lower()
        size_kb = p.stat().st_size // 1024
        mtime = p.stat().st_mtime
        modified = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        target = out / (str(rel) + ".txt")
        status = "up to date"
        if ext in SKIPPED:
            status = "skipped"
        elif args.force or not target.exists() or target.stat().st_mtime < mtime:
            try:
                txt = extract(p)
            except Exception as e:  # noqa: BLE001 - one bad file must not stop the run
                txt = ""
                status = f"error: {e.__class__.__name__}"
            if txt is None:
                status = "unsupported"
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"# Source: {rel}\n\n{txt}", encoding="utf-8")
                if not status.startswith("error"):
                    status = "extracted" if len(txt.split()) > 3 else "empty (check)"
        words = 0
        if status not in ("skipped", "unsupported") and target.exists():
            words = len(target.read_text(errors="ignore").split())
        rows.append(f"{rel}\t{ext or '-'}\t{size_kb}\t{modified}\t{status}\t{words}")
        counts[status.split(":")[0]] += 1
        print(f"{status:<14} {words:>6}  {rel}")

    (out / "_manifest.tsv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    summary = ", ".join(f"{n} {s}" for s, n in counts.most_common())
    print(f"\n{sum(counts.values())} files: {summary}\nText and manifest in {out_arg}")


if __name__ == "__main__":
    main()
