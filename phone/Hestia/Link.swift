import Foundation
import UIKit

enum LinkError: LocalizedError {
    case box(String), notConfigured, rejected
    var errorDescription: String? {
        switch self {
        case .box(let s): return s
        case .notConfigured: return "set the box address and device token"
        case .rejected: return "the box rejected the device token"
        }
    }
}

/// The phone's end of /v1/model/socket (PHONE-APP.md, "The connection"). The app dials the box
/// while it is in the foreground, says which model it runs, and answers each "complete" in turn.
/// benchmarks/phone-render/fake_phone.py is the same behavior in Python.
@MainActor
final class Link: ObservableObject {
    @Published var status = "not connected" { didSet { updateConnection() } }
    @Published var modelName = ModelFile.current()?.lastPathComponent ?? "none"
    @Published var download: Double? { didSet { updateConnection() } }
    @Published var lastAnswer = ""
    @Published var log: [String] = []
    @Published var boxAddress = UserDefaults.standard.string(forKey: "box") ?? ""
    @Published var token = Keychain.token ?? ""
    /// What the app connects with: the fields above as last saved, not as being typed.
    @Published private(set) var savedBox: URL? = Link.parse(UserDefaults.standard.string(forKey: "box") ?? "")
    private(set) var deviceToken = Keychain.token ?? ""
    /// The phone's model socket as the page's tag shows it (bridge "connection").
    @Published private(set) var connection = Connection(state: "disconnected", progress: nil, detail: "not connected")

    struct Connection: Equatable {
        var state: String                        // connected | disconnected | downloading
        var progress: Double?
        var detail: String?
        var message: [String: Any] {
            var m: [String: Any] = ["type": "connection", "state": state]
            if let progress { m["progress"] = progress }
            if let detail { m["detail"] = detail }
            return m
        }
    }

    private var runner: Task<Void, Never>?
    private var socket: URLSessionWebSocketTask?
    private var engine: Engine?
    private var sentPushToken: String?
    private var watchingToken: NSObjectProtocol?

    init() {
        // iOS can hand over or rotate the push token after a hello went out: say hello again
        watchingToken = NotificationCenter.default.addObserver(forName: AppDelegate.tokenChanged, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self, self.runner != nil,
                      self.sentPushToken != UserDefaults.standard.string(forKey: AppDelegate.tokenKey) else { return }
                self.note("push token changed; reconnecting to send it")
                self.stop()
                self.start()
            }
        }
    }

    var configured: Bool { savedBox != nil && !deviceToken.isEmpty }

    private func updateConnection() {
        let next: Connection
        if let download {
            next = Connection(state: "downloading", progress: (download * 100).rounded() / 100, detail: nil)
        } else if status.hasPrefix("connected") || status.hasPrefix("answering") {
            next = Connection(state: "connected", progress: nil, detail: nil)
        } else {
            next = Connection(state: "disconnected", progress: nil, detail: status)
        }
        if next != connection { connection = next }
    }

    /// http(s) base of the box as typed; the user may type "100.x.y.z:8734", http:// or ws://.
    var boxURL: URL? { Link.parse(boxAddress) }

    static func parse(_ address: String) -> URL? {
        var s = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        if s.hasPrefix("ws://") { s = "http://" + s.dropFirst(5) }
        if s.hasPrefix("wss://") { s = "https://" + s.dropFirst(6) }
        if !s.contains("://") { s = "http://" + s }
        if let range = s.range(of: "/v1/model/socket") { s.removeSubrange(range) }
        return URL(string: s)
    }

    func save() {
        UserDefaults.standard.set(boxAddress, forKey: "box")
        Keychain.token = token.isEmpty ? nil : token
        savedBox = boxURL
        deviceToken = token
        stop()
        start()
    }

    func start() {
        guard runner == nil else { return }
        guard configured else { status = LinkError.notConfigured.localizedDescription; return }
        runner = Task { await run() }
    }

    /// iOS closes the socket in the background anyway; closing it first is tidier.
    func stop() {
        runner?.cancel()
        runner = nil
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil
        status = "not connected"
    }

    private func run() async {
        var wait: Double = 1
        while !Task.isCancelled {
            do {
                try await session()
                wait = 1
            } catch is CancellationError {
                return
            } catch {
                if Task.isCancelled { return }
                note("! \(error.localizedDescription)")
                status = "\(error.localizedDescription), retrying in \(Int(wait))s"
            }
            try? await Task.sleep(for: .seconds(wait))
            wait = min(wait * 2, 30)
        }
    }

    private func session() async throws {
        guard let box = savedBox else { throw LinkError.notConfigured }
        var model = ModelFile.current()
        var digest = ""
        if let model {
            status = "checking the model"
            digest = try await ModelFile.sha256(model)
        }

        var comps = URLComponents(url: box.appending(path: "v1/model/socket"), resolvingAgainstBaseURL: false)!
        comps.scheme = comps.scheme == "https" ? "wss" : "ws"
        var request = URLRequest(url: comps.url!)
        request.setValue("Bearer \(deviceToken)", forHTTPHeaderField: "Authorization")
        let ws = URLSession.shared.webSocketTask(with: request)
        ws.maximumMessageSize = 16 << 20                   // prompts are text, but long
        socket = ws
        ws.resume()
        defer { ws.cancel(with: .normalClosure, reason: nil) }

        status = "connecting"
        var hello: [String: Any] = ["type": "hello", "device": UIDevice.current.name, "model_sha256": digest]
        let push = UserDefaults.standard.string(forKey: AppDelegate.tokenKey)
        if let push { hello["push_token"] = push }          // every connect, so a rotated one stays current
        try await send(ws, hello)
        sentPushToken = push
        let ready: [String: Any]
        do {
            ready = try await receive(ws)
        } catch {
            if ws.closeCode.rawValue == 4401 { throw LinkError.rejected }
            throw error
        }
        guard ready["type"] as? String == "ready" else { throw LinkError.box("expected ready") }
        if ready["model_ok"] as? Bool != true {
            ws.cancel(with: .normalClosure, reason: nil)
            guard ready["model_sha256"] is String else { throw LinkError.box("the box has no phone model set") }
            note("the box serves a different model; downloading it")
            status = "downloading the model"
            engine = nil
            download = 0
            defer { download = nil }
            model = try await ModelFile.download(box: box, token: deviceToken) { p in
                Task { @MainActor in self.download = p }
            }
            modelName = model?.lastPathComponent ?? "none"
            note("downloaded \(modelName)")
            return                                         // reconnect with the new sha256
        }

        let engine = try await loadEngine(model!)
        status = "connected, ready"
        note("connected to \(box.host() ?? "the box")")

        // Receive in one task, answer in another, one request at a time in the order they came.
        let (requests, queue) = AsyncStream<CompleteRequest>.makeStream()
        try await withThrowingTaskGroup(of: Void.self) { group in
            group.addTask { @MainActor in
                defer { queue.finish() }
                while true {
                    let msg = try await self.receive(ws)
                    guard msg["type"] as? String == "complete" else { continue }
                    if let req = CompleteRequest(msg) {
                        queue.yield(req)
                    } else {
                        try await self.send(ws, ["type": "error", "id": msg["id"].map { "\($0)" } ?? "",
                                                 "message": "complete without a prompt"])
                    }
                }
            }
            group.addTask { @MainActor in
                for await req in requests {
                    try await self.answer(req, engine: engine, ws: ws)
                }
                throw LinkError.box("the socket closed")
            }
            try await group.next()
            group.cancelAll()
        }
    }

    private func answer(_ req: CompleteRequest, engine: Engine, ws: URLSessionWebSocketTask) async throws {
        status = "answering #\(req.id)"
        do {
            let finish = try await engine.complete(req) { text in
                try await self.send(ws, ["type": "delta", "id": req.id, "text": text])
            }
            try await send(ws, ["type": "done", "id": req.id, "stop_type": finish.stopType,
                                "tokens_evaluated": finish.tokensEvaluated,
                                "tokens_predicted": finish.tokensPredicted])
            let fresh = finish.tokensEvaluated - finish.tokensReused
            lastAnswer = String(format: "#%@: %d prompt tokens (%d reused) in %.2fs, %d out at %.1f t/s, %@",
                                req.id, finish.tokensEvaluated, finish.tokensReused, finish.promptSeconds,
                                finish.tokensPredicted,
                                Double(finish.tokensPredicted) / max(finish.generateSeconds, 0.001), finish.stopType)
            note("#\(req.id) \(fresh) new prompt tokens, \(finish.tokensPredicted) out, \(finish.stopType)")
        } catch is CancellationError {
            throw CancellationError()
        } catch let error as EngineError {
            note("! #\(req.id) \(error.localizedDescription)")
            try await send(ws, ["type": "error", "id": req.id, "message": error.localizedDescription])
        }
        status = "connected, ready"
    }

    private func loadEngine(_ url: URL) async throws -> Engine {
        if let engine, engine.path == url.path { return engine }
        status = "loading the model"
        engine = nil
        let path = url.path
        let loaded = try await Task.detached(priority: .userInitiated) { try Engine(path: path) }.value
        engine = loaded
        note("loaded \(await loaded.describe())")
        return loaded
    }

    // MARK: - wire

    private func send(_ ws: URLSessionWebSocketTask, _ obj: [String: Any]) async throws {
        let data = try JSONSerialization.data(withJSONObject: obj)
        try await ws.send(.string(String(decoding: data, as: UTF8.self)))
    }

    private func receive(_ ws: URLSessionWebSocketTask) async throws -> [String: Any] {
        let data: Data
        switch try await ws.receive() {
        case .string(let s): data = Data(s.utf8)
        case .data(let d): data = d
        @unknown default: throw LinkError.box("unknown message")
        }
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw LinkError.box("a message that is not a JSON object")
        }
        return obj
    }

    func note(_ line: String) {
        let stamp = Date().formatted(date: .omitted, time: .standard)
        log.insert("\(stamp) \(line)", at: 0)
        if log.count > 100 { log.removeLast(log.count - 100) }
    }
}
