// OCR for images (png, jpg, heic, tiff...) and scanned PDFs with the macOS Vision framework.
// Usage: ./ocr <file> [<file> ...]  -> recognized text on stdout
// PDF pages are rendered at 300 dpi on a white background, then read one by one; so are the pages of a TIFF.
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

// Vision reads dark text on a transparent background as dark on dark: images with alpha are drawn on white first
func onWhite(_ img: CGImage) -> CGImage {
    switch img.alphaInfo {
    case .none, .noneSkipFirst, .noneSkipLast: return img
    default: break
    }
    let rect = CGRect(x: 0, y: 0, width: img.width, height: img.height)
    // Same color space as the image: an opaque image comes out pixel for pixel identical
    let space = img.colorSpace.flatMap { $0.model == .rgb ? $0 : nil } ?? CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(data: nil, width: img.width, height: img.height, bitsPerComponent: 8, bytesPerRow: 0,
                              space: space, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
    else { return img }
    ctx.setFillColor(CGColor(gray: 1, alpha: 1))
    ctx.fill(rect)
    ctx.draw(img, in: rect)
    return ctx.makeImage() ?? img
}

// Each page is rendered and read before the next one, so memory stays flat on long scans
func pdfText(_ url: URL) -> String {
    guard let doc = PDFDocument(url: url) else { return "" }
    var pages: [String] = []
    for i in 0..<doc.pageCount {
        autoreleasepool {
            guard let page = doc.page(at: i) else { return }
            // thumbnail() keeps the aspect ratio and the page rotation, so a square box fits both orientations
            let box = page.bounds(for: .mediaBox)
            let side = max(box.width, box.height) * pdfDPI / 72
            let thumb = page.thumbnail(of: NSSize(width: side, height: side), for: .mediaBox)
            guard let img = thumb.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return }
            pages.append("--- page \(i + 1) ---\n" + recognize(img))
        }
    }
    return pages.joined(separator: "\n\n")
}

func ocr(_ path: String) -> String {
    let url = URL(fileURLWithPath: path)
    if url.pathExtension.lowercased() == "pdf" {
        return pdfText(url)
    }
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else { return "" }
    // A TIFF from a scanner or a fax can hold several pages; other formats are read from their first image
    let isTIFF = ["tif", "tiff"].contains(url.pathExtension.lowercased())
    let count = isTIFF ? CGImageSourceGetCount(src) : 1
    if count <= 1 {
        guard let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return "" }
        return recognize(onWhite(img))
    }
    var pages: [String] = []
    for i in 0..<count {
        autoreleasepool {
            if let img = CGImageSourceCreateImageAtIndex(src, i, nil) {
                pages.append("--- page \(i + 1) ---\n" + recognize(onWhite(img)))
            }
        }
    }
    return pages.joined(separator: "\n\n")
}

for p in CommandLine.arguments.dropFirst() {
    print(ocr(p))
}
