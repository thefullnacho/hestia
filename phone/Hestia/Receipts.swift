import PhotosUI
import SwiftUI
@preconcurrency import Vision
@preconcurrency import VisionKit

/// A receipt into the inventory (PHONE-APP.md, "Receipts"). The receipt is read here with Apple's
/// on-device text recognition and only its lines go to the box, never the picture. The box reads
/// the items in code and puts them on the Today page, where a person keeps them.
enum ReceiptReader {
    static let maxLines = 400

    enum ReadError: LocalizedError {
        case noText, tooLong(Int), notAnImage
        var errorDescription: String? {
            switch self {
            case .noText: return "No text was found on that receipt."
            case .tooLong(let n): return "That is \(n) lines; a receipt can be up to \(maxLines)."
            case .notAnImage: return "That photo could not be opened."
            }
        }
    }

    /// One piece of text as Vision found it, before rows: which shot, where, what it says.
    /// Sent with the lines so the box can keep real receipts for tuning the row joining
    /// (only a box with HESTIA_RECEIPT_SAMPLES=1 keeps them).
    struct Piece: Sendable { let page: Int; let box: CGRect; let text: String }

    /// What a receipt read gives: the lines the box reads, and the pieces they came from.
    struct Reading: Sendable {
        let source: String               // "scan" (the document camera) or "photo"
        let lines: [String]
        let pieces: [Piece]
    }

    static func pieces(_ image: CGImage, orientation: CGImagePropertyOrientation = .up, page: Int = 0) throws -> [Piece] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        try VNImageRequestHandler(cgImage: image, orientation: orientation).perform([request])
        return (request.results ?? []).compactMap { o in
            o.topCandidates(1).first.map { Piece(page: page, box: o.boundingBox, text: $0.string) }
        }
    }

    /// The receipt's lines top to bottom, one string per printed line. Vision often returns an
    /// item's name and its price as two pieces, with the gap between them; pieces on the same
    /// row are put back together left to right, so the price stays at the end of its item.
    /// Group pieces whose middles sit within half a line height of the row's first piece.
    /// Vision's origin is the bottom left, so a higher y is higher on the receipt.
    static func rows(_ pieces: [Piece]) -> [String] {
        var rows: [[Piece]] = []
        for p in pieces.sorted(by: { $0.box.midY > $1.box.midY }) {
            if let first = rows.last?.first,
               abs(first.box.midY - p.box.midY) < min(first.box.height, p.box.height) / 2 {
                rows[rows.count - 1].append(p)
            } else {
                rows.append([p])
            }
        }
        return rows.map { row in
            row.sorted { $0.box.minX < $1.box.minX }.map(\.text).joined(separator: " ")
        }
        .map { $0.trimmingCharacters(in: .whitespaces) }
        .filter { !$0.isEmpty }
    }

    /// A long receipt can take more than one shot: the pages' lines, in order.
    static func reading(scan: VNDocumentCameraScan) throws -> Reading {
        var lines: [String] = []
        var all: [Piece] = []
        for i in 0..<scan.pageCount {
            let image = scan.imageOfPage(at: i)
            guard let cg = image.cgImage else { continue }
            let found = try pieces(cg, orientation: .init(image.imageOrientation), page: i)
            all += found
            lines += rows(found)
        }
        return Reading(source: "scan", lines: try checked(lines), pieces: all)
    }

    static func reading(photo data: Data) throws -> Reading {
        guard let image = UIImage(data: data), let cg = image.cgImage else { throw ReadError.notAnImage }
        let found = try pieces(cg, orientation: .init(image.imageOrientation))
        return Reading(source: "photo", lines: try checked(rows(found)), pieces: found)
    }

    private static func checked(_ lines: [String]) throws -> [String] {
        if lines.isEmpty { throw ReadError.noText }
        if lines.count > maxLines { throw ReadError.tooLong(lines.count) }
        return lines
    }
}

/// The scan, the post, and what the person sees while it happens. One receipt at a time; its id
/// is kept until it is filed or dropped, so Try again replaces rather than duplicates.
@MainActor
final class Receipts: ObservableObject {
    struct Item: Decodable { let name: String; let qty: Double?; let unit: String? }
    struct Filed: Decodable { let id: String; let store: String?; let bought: String?; let items: [Item] }

    enum Phase: Equatable {
        case idle, reading, sending
        case failed(String)
        case filed(String)
    }

    @Published var camera = false
    @Published var photos = false
    @Published var photo: PhotosPickerItem?
    @Published var phase = Phase.idle
    /// Set after a 200: the web view goes to the Today page, where the items wait.
    var onFiled: (() -> Void)?
    private var pending: (id: String, reading: ReceiptReader.Reading)?
    private var clearing: Task<Void, Never>?
    let link: Link

    init(link: Link) { self.link = link }

    /// The document camera where there is one; Photos otherwise, or when asked for: a receipt
    /// photographed at the till.
    func start(fromPhotos: Bool = false) {
        guard phase != .reading, phase != .sending else { return }
        if !fromPhotos && VNDocumentCameraViewController.isSupported { camera = true } else { photos = true }
    }

    func scanned(_ scan: VNDocumentCameraScan?) {
        camera = false
        guard let scan else { return }
        read { try ReceiptReader.reading(scan: scan) }
    }

    func picked(_ item: PhotosPickerItem?) {
        photo = nil
        guard let item else { return }
        phase = .reading
        Task {
            do {
                guard let data = try await item.loadTransferable(type: Data.self) else { throw ReceiptReader.ReadError.notAnImage }
                read { try ReceiptReader.reading(photo: data) }
            } catch {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    private func read(_ work: @escaping @Sendable () throws -> ReceiptReader.Reading) {
        clearing?.cancel()
        phase = .reading
        Task {
            do {
                let reading = try await Task.detached(priority: .userInitiated) { try work() }.value
                pending = (UUID().uuidString, reading)
                await send()
            } catch {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    /// Resend the same lines under the same id.
    func send() async {
        guard let pending else { return }
        phase = .sending
        do {
            guard let box = link.savedBox, link.configured else { throw LinkError.notConfigured }
            var r = URLRequest(url: box.appending(path: "v1/phone/receipts"))
            r.httpMethod = "POST"
            r.timeoutInterval = 60
            r.setValue("Bearer \(link.deviceToken)", forHTTPHeaderField: "Authorization")
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            let round = { (v: CGFloat) in (Double(v) * 10_000).rounded() / 10_000 }
            let pieces: [[String: Any]] = pending.reading.pieces.map {
                ["page": $0.page, "text": $0.text,
                 "box": [round($0.box.minX), round($0.box.minY), round($0.box.width), round($0.box.height)]]
            }
            r.httpBody = try JSONSerialization.data(withJSONObject: [
                "id": pending.id, "lines": pending.reading.lines,
                "source": pending.reading.source, "pieces": pieces])
            let (data, response) = try await URLSession.shared.data(for: r)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard status == 200 else {
                let said = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
                switch status {
                case 401: throw LinkError.rejected
                case 413: throw LinkError.box(said ?? "That receipt is too long to send.")
                default: throw LinkError.box(said ?? "The box answered \(status).")
                }
            }
            let filed = try JSONDecoder().decode(Filed.self, from: data)
            self.pending = nil
            link.note("receipt: \(pending.reading.lines.count) lines, \(filed.items.count) items")   // counts only
            let n = filed.items.count
            phase = .filed(n == 0 ? "No items found on that receipt"
                                  : "\(n) item\(n == 1 ? "" : "s") read, waiting on Today")
            if n > 0 { onFiled?() }
            clearing = Task {
                try? await Task.sleep(for: .seconds(4))
                if !Task.isCancelled, case .filed = phase { phase = .idle }
            }
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    func drop() {
        pending = nil
        phase = .idle
    }

    var canRetry: Bool { pending != nil }
}

/// A small card over the page while a receipt is read and sent, and its result.
struct ReceiptBanner: View {
    @ObservedObject var receipts: Receipts

    var body: some View {
        Group {
            switch receipts.phase {
            case .idle:
                EmptyView()
            case .reading:
                card { HStack { ProgressView(); Text("Reading the receipt") } }
            case .sending:
                card { HStack { ProgressView(); Text("Sending its lines to the box") } }
            case .filed(let words):
                card { Label(words, systemImage: "checkmark.circle") }
                    .onTapGesture { receipts.drop() }
            case .failed(let error):
                card {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("The receipt wasn't filed").font(.headline)
                        Text(error).foregroundStyle(.secondary)
                        HStack {
                            if receipts.canRetry {
                                Button("Try again") { Task { await receipts.send() } }.buttonStyle(.borderedProminent)
                            }
                            Button("Dismiss") { receipts.drop() }.buttonStyle(.bordered)
                        }
                    }
                }
            }
        }
        .animation(.default, value: receipts.phase)
    }

    private func card(@ViewBuilder _ content: () -> some View) -> some View {
        content()
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14))
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
            .transition(.move(edge: .bottom).combined(with: .opacity))
    }
}
