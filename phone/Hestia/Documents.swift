import Foundation
import PDFKit
import SwiftUI
import UniformTypeIdentifiers
@preconcurrency import Vision
@preconcurrency import VisionKit

/// A household's own papers into the library (PHONE-APP.md, "Documents into the library").
/// Pages are read here with Apple's on-device text recognition, and only the text goes to the
/// box: never an image. No model on either end.
enum PageReader {
    static let maxPages = 300
    static let maxBytes = 2_000_000
    static let maxTitle = 200

    enum ReadError: LocalizedError {
        case unreadable, tooManyPages(Int), tooLong
        var errorDescription: String? {
            switch self {
            case .unreadable: return "That file could not be opened as a PDF."
            case .tooManyPages(let n): return "That is \(n) pages; the library takes up to \(maxPages) at a time."
            case .tooLong: return "That is more text than the library takes in one document (about 2 MB)."
            }
        }
    }

    /// Lines top to bottom, joined with newlines. Vision's origin is the bottom left, so a
    /// higher y is higher on the page; lines at the same height go left to right.
    static func read(_ image: CGImage, orientation: CGImagePropertyOrientation = .up) throws -> String {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        try VNImageRequestHandler(cgImage: image, orientation: orientation).perform([request])
        let lines = (request.results ?? []).sorted {
            ($0.boundingBox.midY, -$0.boundingBox.minX) > ($1.boundingBox.midY, -$1.boundingBox.minX)
        }
        return lines.compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
    }

    /// The pages from the document camera, in order. A page with no text is "", kept.
    static func read(scan: VNDocumentCameraScan, progress: @Sendable (Int, Int) -> Void) throws -> [String] {
        guard scan.pageCount <= maxPages else { throw ReadError.tooManyPages(scan.pageCount) }
        var pages: [String] = []
        for i in 0..<scan.pageCount {
            progress(i, scan.pageCount)
            let image = scan.imageOfPage(at: i)
            pages.append(try image.cgImage.map { try read($0, orientation: .init(image.imageOrientation)) } ?? "")
        }
        return try checked(pages)
    }

    /// A PDF from Files: its own text layer where a page has one, otherwise the page drawn and read
    /// like a photo. Decided page by page, since a PDF can mix both.
    static func read(pdf url: URL, progress: @Sendable (Int, Int) -> Void) throws -> [String] {
        guard let doc = PDFDocument(url: url) else { throw ReadError.unreadable }
        guard doc.pageCount <= maxPages else { throw ReadError.tooManyPages(doc.pageCount) }
        var pages: [String] = []
        for i in 0..<doc.pageCount {
            progress(i, doc.pageCount)
            guard let page = doc.page(at: i) else { pages.append(""); continue }
            if let text = page.string, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                pages.append(text)
            } else if let image = render(page) {
                pages.append(try read(image))
            } else {
                pages.append("")
            }
        }
        return try checked(pages)
    }

    /// A page drawn about 2,200 px on its long side, white behind it, its rotation applied.
    private static func render(_ page: PDFPage) -> CGImage? {
        guard let ref = page.pageRef else { return nil }
        let box = ref.getBoxRect(.mediaBox)
        let turned = ref.rotationAngle % 180 != 0
        let size = turned ? CGSize(width: box.height, height: box.width) : box.size
        guard size.width > 0, size.height > 0 else { return nil }
        let scale = min(4, 2200 / max(size.width, size.height))
        let w = Int(size.width * scale), h = Int(size.height * scale)
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        ctx.setFillColor(CGColor(gray: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
        ctx.scaleBy(x: scale, y: scale)
        // getDrawingTransform only ever shrinks, so it maps onto the unscaled size
        ctx.concatenate(ref.getDrawingTransform(.mediaBox, rect: CGRect(origin: .zero, size: size),
                                                rotate: 0, preserveAspectRatio: true))
        ctx.drawPDFPage(ref)
        return ctx.makeImage()
    }

    private static func checked(_ pages: [String]) throws -> [String] {
        if pages.reduce(0, { $0 + $1.utf8.count }) > maxBytes { throw ReadError.tooLong }
        return pages
    }

    /// The first line with letters in it on page 1, as the title to suggest.
    static func suggestedTitle(_ pages: [String]) -> String {
        let line = pages.first?.split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .first { $0.contains(where: \.isLetter) }
        return String((line ?? "").prefix(maxTitle))
    }
}

extension CGImagePropertyOrientation {
    init(_ o: UIImage.Orientation) {
        switch o {
        case .up: self = .up
        case .down: self = .down
        case .left: self = .left
        case .right: self = .right
        case .upMirrored: self = .upMirrored
        case .downMirrored: self = .downMirrored
        case .leftMirrored: self = .leftMirrored
        case .rightMirrored: self = .rightMirrored
        @unknown default: self = .up
        }
    }
}

/// The box's end: GET, POST and DELETE /v1/phone/library with the device token.
@MainActor
final class Documents: ObservableObject {
    struct Doc: Decodable, Identifiable {
        let id: String?
        let title: String
        let pages: Int?
        let passages: Int?
        let year: Int?
        var listID: String { id ?? title }
    }
    struct Listing: Decodable { let scanned: [Doc]; let shelf: [Doc] }
    struct Filed: Decodable { let id: String; let title: String; let pages: Int; let passages: Int; let embedded: Bool?; let note: String? }

    @Published var scanned: [Doc] = []
    @Published var shelf: [Doc] = []
    @Published var listError: String?
    let link: Link

    init(link: Link) { self.link = link }

    private func request(_ path: String, method: String = "GET") throws -> URLRequest {
        guard let box = link.savedBox, link.configured else { throw LinkError.notConfigured }
        var r = URLRequest(url: box.appending(path: path))
        r.httpMethod = method
        r.setValue("Bearer \(link.deviceToken)", forHTTPHeaderField: "Authorization")
        r.timeoutInterval = 120
        return r
    }

    private func call(_ r: URLRequest) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(for: r)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else {
            let said = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
            switch status {
            case 401: throw LinkError.rejected
            case 413: throw LinkError.box(said ?? "That document is too long to file in one go.")
            default: throw LinkError.box(said ?? "The box answered \(status).")
            }
        }
        return data
    }

    func refresh() async {
        do {
            let listing = try JSONDecoder().decode(Listing.self, from: try await call(request("v1/phone/library")))
            scanned = listing.scanned
            shelf = listing.shelf
            listError = nil
        } catch {
            listError = error.localizedDescription
        }
    }

    /// The same id again replaces the document, so a retry never makes a second copy.
    func file(id: String, title: String, pages: [String]) async throws -> Filed {
        var r = try request("v1/phone/library", method: "POST")
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try JSONSerialization.data(withJSONObject: ["id": id, "title": title, "pages": pages])
        let filed = try JSONDecoder().decode(Filed.self, from: try await call(r))
        link.note("library: filed \(filed.pages) pages, \(filed.passages) passages")   // counts only
        await refresh()
        return filed
    }

    func remove(_ id: String) async {
        do {
            _ = try await call(request("v1/phone/library/\(id)", method: "DELETE"))
            link.note("library: removed a document")
        } catch {
            listError = error.localizedDescription
        }
        await refresh()
    }
}

/// What the person sees: the documents filed from phones (each removable), the operator's shelf,
/// and the two ways in.
struct DocumentsView: View {
    @ObservedObject var documents: Documents
    @State private var scanning = false
    @State private var importing = false
    @State private var draft: Draft?
    @State private var reading: String?
    @State private var readError: String?

    struct Draft: Identifiable {
        let id = UUID().uuidString
        var title: String
        let pages: [String]
    }

    var body: some View {
        List {
            Section {
                Button { scanning = true } label: { Label("Scan pages", systemImage: "doc.viewfinder") }
                    .disabled(!VNDocumentCameraViewController.isSupported || reading != nil)
                Button { importing = true } label: { Label("Add a PDF from Files", systemImage: "doc.badge.plus") }
                    .disabled(reading != nil)
                if let reading {
                    HStack { ProgressView(); Text(reading).foregroundStyle(.secondary) }
                }
                if let readError { Text(readError).foregroundStyle(.red) }
            } footer: {
                Text("Pages are read on this phone. Only their text goes to the box, never the pictures.")
            }
            Section("Filed from phones") {
                if documents.scanned.isEmpty { Text("None yet").foregroundStyle(.secondary) }
                ForEach(documents.scanned, id: \.listID) { doc in
                    row(doc)
                }
                .onDelete { offsets in
                    let ids = offsets.compactMap { documents.scanned[$0].id }
                    Task { for id in ids { await documents.remove(id) } }
                }
            }
            if !documents.shelf.isEmpty {
                Section("The shelf") {
                    ForEach(documents.shelf, id: \.listID) { row($0) }
                }
            }
            if let e = documents.listError {
                Section { Text(e).foregroundStyle(.red) }
            }
        }
        .navigationTitle("Library")
        .task { await documents.refresh() }
        .refreshable { await documents.refresh() }
        .fullScreenCover(isPresented: $scanning) {
            DocumentCamera { scan in
                scanning = false
                if let scan { read { try PageReader.read(scan: scan, progress: $0) } }
            }
            .ignoresSafeArea()
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.pdf]) { result in
            guard case .success(let url) = result else { return }
            read {
                let open = url.startAccessingSecurityScopedResource()
                defer { if open { url.stopAccessingSecurityScopedResource() } }
                return try PageReader.read(pdf: url, progress: $0)
            }
        }
        .sheet(item: $draft) { d in
            NavigationStack { FileSheet(documents: documents, draft: d) }
        }
    }

    private func row(_ doc: Documents.Doc) -> some View {
        VStack(alignment: .leading) {
            Text(doc.title)
            Text([doc.pages.map { "\($0) pages" }, doc.passages.map { "\($0) passages" }, doc.year.map(String.init)]
                    .compactMap { $0 }.joined(separator: " · "))
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    /// Reading runs off the main thread; the page count shows as it goes.
    private func read(_ work: @escaping @Sendable (@escaping @Sendable (Int, Int) -> Void) throws -> [String]) {
        readError = nil
        reading = "Reading…"
        Task {
            do {
                let pages = try await Task.detached(priority: .userInitiated) {
                    try work { i, n in Task { @MainActor in reading = "Reading page \(i + 1) of \(n)" } }
                }.value
                reading = nil
                draft = Draft(title: PageReader.suggestedTitle(pages), pages: pages)
            } catch {
                reading = nil
                readError = error.localizedDescription
            }
        }
    }
}

/// Name it and file it. The draft's id is kept across retries, so a resend replaces.
struct FileSheet: View {
    @ObservedObject var documents: Documents
    @State var draft: DocumentsView.Draft
    @State private var sending = false
    @State private var filed: Documents.Filed?
    @State private var error: String?
    @Environment(\.dismiss) private var dismiss

    private var title: String { draft.title.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var blank: Int { draft.pages.filter { $0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }.count }

    var body: some View {
        Form {
            Section("Title") {
                TextField("Dishwasher manual", text: $draft.title)
                    .disabled(filed != nil)
            }
            Section {
                LabeledContent("Pages", value: "\(draft.pages.count)")
                if blank > 0 { LabeledContent("With no text found", value: "\(blank)") }
                if let first = draft.pages.first(where: { !$0.isEmpty }) {
                    Text(first.prefix(400) + (first.count > 400 ? "…" : ""))
                        .font(.caption.monospaced()).foregroundStyle(.secondary)
                }
            } header: { Text("What was read") }
            if let filed {
                Section("Filed") {
                    Text("\(filed.passages) passages are searchable. Ask about it in chat.")
                    if let note = filed.note { Text(note).foregroundStyle(.orange) }
                    else if filed.embedded == false { Text("Search by meaning waits on the box.").foregroundStyle(.orange) }
                }
            }
            if let error { Section { Text(error).foregroundStyle(.red) } }
        }
        .navigationTitle(filed == nil ? "File it" : "Filed")
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                if filed == nil { Button("Cancel") { dismiss() } }
            }
            ToolbarItem(placement: .confirmationAction) {
                if filed != nil {
                    Button("Done") { dismiss() }
                } else if sending {
                    ProgressView()
                } else {
                    Button(error == nil ? "File" : "Try again") { send() }
                        .disabled(title.isEmpty || title.count > PageReader.maxTitle)
                }
            }
        }
        .interactiveDismissDisabled(sending)
    }

    private func send() {
        sending = true
        error = nil
        Task {
            do { filed = try await documents.file(id: draft.id, title: title, pages: draft.pages) }
            catch { self.error = error.localizedDescription }
            sending = false
        }
    }
}

/// VisionKit's document camera: edge finding, multiple pages, retakes.
struct DocumentCamera: UIViewControllerRepresentable {
    let done: (VNDocumentCameraScan?) -> Void

    func makeUIViewController(context: Context) -> VNDocumentCameraViewController {
        let vc = VNDocumentCameraViewController()
        vc.delegate = context.coordinator
        return vc
    }
    func updateUIViewController(_ vc: VNDocumentCameraViewController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(done: done) }

    final class Coordinator: NSObject, VNDocumentCameraViewControllerDelegate {
        let done: (VNDocumentCameraScan?) -> Void
        init(done: @escaping (VNDocumentCameraScan?) -> Void) { self.done = done }
        func documentCameraViewController(_ c: VNDocumentCameraViewController, didFinishWith scan: VNDocumentCameraScan) { done(scan) }
        func documentCameraViewControllerDidCancel(_ c: VNDocumentCameraViewController) { done(nil) }
        func documentCameraViewController(_ c: VNDocumentCameraViewController, didFailWithError error: Error) { done(nil) }
    }
}
