import CryptoKit
import Foundation

/// The GGUF on this phone: where it lives, its sha256, and fetching the box's when they differ.
enum ModelFile {
    static var directory: URL {
        let dir = URL.applicationSupportDirectory.appending(path: "Models", directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// The model this phone runs, if it has one.
    static func current() -> URL? {
        let files = (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? []
        return files.first { $0.pathExtension == "gguf" }
    }

    /// sha256 of the file, hashed once per file version (about 2 s for 1 GB).
    static func sha256(_ url: URL) async throws -> String {
        let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
        let key = "sha256|\(url.lastPathComponent)|\(attrs[.size] ?? 0)|\((attrs[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0)"
        if let known = UserDefaults.standard.string(forKey: key) { return known }
        let digest = try await Task.detached(priority: .userInitiated) { try hash(url) }.value
        UserDefaults.standard.set(digest, forKey: key)
        return digest
    }

    static func hash(_ url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 4 << 20), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    struct Info: Decodable { let name: String; let size: Int64; let sha256: String }

    /// GET /v1/model/info, then /v1/model/file, check the sha256, and make it the only model here.
    static func download(box: URL, token: String, progress: @escaping @Sendable (Double) -> Void) async throws -> URL {
        var infoReq = URLRequest(url: box.appending(path: "v1/model/info"))
        infoReq.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, resp) = try await URLSession.shared.data(for: infoReq)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
            throw LinkError.box("model info: HTTP \((resp as? HTTPURLResponse)?.statusCode ?? 0)")
        }
        let info = try JSONDecoder().decode(Info.self, from: data)

        var fileReq = URLRequest(url: box.appending(path: "v1/model/file"))
        fileReq.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let tmp = try await Downloader(expected: info.size, progress: progress).fetch(fileReq)
        let got = try await Task.detached(priority: .userInitiated) { try hash(tmp) }.value
        guard got == info.sha256 else {
            try? FileManager.default.removeItem(at: tmp)
            throw LinkError.box("the downloaded model's sha256 does not match the box's")
        }
        for old in (try? FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)) ?? [] {
            try? FileManager.default.removeItem(at: old)
        }
        var dest = directory.appending(path: info.name)
        try FileManager.default.moveItem(at: tmp, to: dest)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true                 // re-downloadable, 1 GB
        try? dest.setResourceValues(values)
        return dest
    }
}

/// A download task with progress; the file is moved out of URLSession's temp before it vanishes.
private final class Downloader: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    let expected: Int64
    let progress: @Sendable (Double) -> Void
    var continuation: CheckedContinuation<URL, Error>?

    init(expected: Int64, progress: @escaping @Sendable (Double) -> Void) {
        self.expected = expected
        self.progress = progress
    }

    func fetch(_ request: URLRequest) async throws -> URL {
        let session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { cont in
                continuation = cont
                session.downloadTask(with: request).resume()
            }
        } onCancel: {
            session.invalidateAndCancel()
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData _: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        let total = totalBytesExpectedToWrite > 0 ? totalBytesExpectedToWrite : expected
        if total > 0 { progress(Double(totalBytesWritten) / Double(total)) }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        let status = (downloadTask.response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else {
            continuation?.resume(throwing: LinkError.box("model file: HTTP \(status)"))
            continuation = nil
            return
        }
        let keep = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString + ".gguf")
        do {
            try FileManager.default.moveItem(at: location, to: keep)
            continuation?.resume(returning: keep)
        } catch {
            continuation?.resume(throwing: error)
        }
        continuation = nil
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error {
            continuation?.resume(throwing: error)
            continuation = nil
        }
    }
}
