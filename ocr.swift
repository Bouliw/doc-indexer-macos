// OCR for images (png, jpg, heic, tiff...) and scanned PDFs with the macOS Vision framework.
// Usage: ./ocr <file> [<file> ...]  -> recognized text on stdout
// PDF pages are rendered at 300 dpi on a white background, then read one by one.
// Languages: OCR_LANGUAGES, comma-separated, in priority order (default: en-US,fr-FR).
// Build: swiftc -O ocr.swift -o ocr
import Foundation
import Vision
import ImageIO
import PDFKit

let languages = (ProcessInfo.processInfo.environment["OCR_LANGUAGES"] ?? "en-US,fr-FR")
    .split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
let pdfDPI: CGFloat = 300

func recognize(_ img: CGImage) -> String {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = languages
    req.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: img, options: [:])
    try? handler.perform([req])
    let lines = (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    return lines.joined(separator: "\n")
}

func pdfPages(_ url: URL) -> [CGImage] {
    guard let doc = PDFDocument(url: url) else { return [] }
    return (0..<doc.pageCount).compactMap { i in
        guard let page = doc.page(at: i) else { return nil }
        // thumbnail() keeps the aspect ratio and the page rotation, so a square box fits both orientations
        let box = page.bounds(for: .mediaBox)
        let side = max(box.width, box.height) * pdfDPI / 72
        let thumb = page.thumbnail(of: NSSize(width: side, height: side), for: .mediaBox)
        return thumb.cgImage(forProposedRect: nil, context: nil, hints: nil)
    }
}

func ocr(_ path: String) -> String {
    let url = URL(fileURLWithPath: path)
    if url.pathExtension.lowercased() == "pdf" {
        let pages = pdfPages(url)
        return pages.enumerated().map { "--- page \($0.offset + 1) ---\n" + recognize($0.element) }
            .joined(separator: "\n\n")
    }
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return "" }
    return recognize(img)
}

for p in CommandLine.arguments.dropFirst() {
    print(ocr(p))
}
