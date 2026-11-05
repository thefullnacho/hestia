@preconcurrency import AVFoundation
import Foundation
@preconcurrency import Speech

/// Listening and speaking for the bridge, both on the phone: recognition with
/// requiresOnDeviceRecognition (never Apple's servers) and AVSpeechSynthesizer's voices.
/// Every listen ends with exactly one final transcript or one listen_error; every speak ends
/// with exactly one speak_finished, so neither the mic nor Stop can stick on the page.
@MainActor
final class Speech: NSObject, AVSpeechSynthesizerDelegate {
    var send: (([String: Any]) -> Void)?

    // MARK: listening

    private let recognizer = SFSpeechRecognizer(locale: Locale.current)
    private let engine = AVAudioEngine()
    /// The engine starts and stops here, never on the main thread: CoreAudio can stall for
    /// seconds bringing up the input, and the page must stay live meanwhile.
    private let audio = DispatchQueue(label: "hestia.audio")
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var heard = ""
    private var stopping = false
    private var open = false                     // a listen that has not been ended yet
    private var generation = 0                   // callbacks from an ended listen are ignored

    /// On-device recognition exists on this phone for its language.
    var available: Bool { recognizer?.supportsOnDeviceRecognition == true }

    func listen() {
        if open { end(final: heard) }            // a new listen ends the old one first
        guard let recognizer, recognizer.supportsOnDeviceRecognition else {
            send?(["type": "listen_error", "code": "unavailable",
                   "message": "This phone has no on-device recognition for \(Locale.current.identifier)."])
            return
        }
        open = true
        generation += 1
        let gen = generation
        Task {
            let speechOK = await withCheckedContinuation { cont in
                SFSpeechRecognizer.requestAuthorization { cont.resume(returning: $0 == .authorized) }
            }
            let micOK = await AVAudioApplication.requestRecordPermission()
            guard gen == generation, open else { return }
            guard speechOK, micOK else {
                fail("denied", speechOK ? "Microphone access is off for Hestia." : "Speech recognition is off for Hestia.")
                return
            }
            do {
                try await start(recognizer, gen: gen)
            } catch {
                fail("failed", error.localizedDescription)
            }
        }
    }

    private func start(_ recognizer: SFSpeechRecognizer, gen: Int) async throws {
        try activateAudio()
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = true
        request.addsPunctuation = true
        let engine = self.engine
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            audio.async {
                let input = engine.inputNode
                input.removeTap(onBus: 0)
                input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in
                    request.append(buffer)
                }
                engine.prepare()
                do { try engine.start(); cont.resume() } catch { cont.resume(throwing: error) }
            }
        }
        guard gen == generation, open else {           // stopped while the input came up
            stopAudio()
            return
        }
        self.request = request
        heard = ""
        stopping = false
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            let text = result?.bestTranscription.formattedString
            let isFinal = result?.isFinal ?? false
            let message = error?.localizedDescription
            Task { @MainActor in self?.recognized(text: text, isFinal: isFinal, error: message, gen: gen) }
        }
    }

    private func recognized(text: String?, isFinal: Bool, error: String?, gen: Int) {
        guard gen == generation, open else { return }
        if let text { heard = text }
        if isFinal {
            end(final: heard)
        } else if let error {
            // Stopped by the person, or it heard something: that is the final text. Otherwise
            // recognition gave up on its own with nothing, which the page shows as a failure.
            if stopping || !heard.isEmpty { end(final: heard) } else { fail("failed", error) }
        } else if text != nil {
            send?(["type": "transcript", "text": heard, "final": false])
        }
    }

    func stopListening() {
        guard open else { return }
        stopping = true
        stopAudio()
        request?.endAudio()                      // recognition finishes with what it heard
        let gen = generation
        Task {                                   // and if it never says final, the page still gets one
            try? await Task.sleep(for: .seconds(2))
            if gen == generation && open { end(final: heard) }
        }
    }

    private func end(final text: String) {
        guard open else { return }
        teardown()
        send?(["type": "transcript", "text": text, "final": true])
    }

    private func fail(_ code: String, _ message: String) {
        guard open else { return }
        teardown()
        send?(["type": "listen_error", "code": code, "message": message])
    }

    private func teardown() {
        open = false
        generation += 1
        stopAudio()
        task?.cancel()
        task = nil
        request = nil
    }

    private func stopAudio() {
        let engine = self.engine
        audio.async {
            if engine.isRunning { engine.stop() }
            engine.inputNode.removeTap(onBus: 0)
        }
    }

    // MARK: speaking

    private let synth = AVSpeechSynthesizer()
    private var speaking: [ObjectIdentifier: String] = [:]

    override init() {
        super.init()
        synth.delegate = self
    }

    func speak(id: String, text: String) {
        if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }   // its speak_finished follows
        let words = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !words.isEmpty else {
            send?(["type": "speak_started", "id": id])
            send?(["type": "speak_finished", "id": id])
            return
        }
        do {
            try activateAudio()
        } catch {
            send?(["type": "speak_finished", "id": id, "error": error.localizedDescription])
            return
        }
        let utterance = AVSpeechUtterance(string: words)
        utterance.voice = AVSpeechSynthesisVoice(language: AVSpeechSynthesisVoice.currentLanguageCode())
        speaking[ObjectIdentifier(utterance)] = id
        synth.speak(utterance)
    }

    func stopSpeaking() {
        synth.stopSpeaking(at: .immediate)
    }

    /// The page went away: stop both, telling nobody (the page that asked is gone).
    func reset() {
        let sender = send
        send = nil
        if open { end(final: heard) }
        synth.stopSpeaking(at: .immediate)
        speaking.removeAll()
        send = sender
    }

    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didStart utterance: AVSpeechUtterance) {
        let key = ObjectIdentifier(utterance)
        MainActor.assumeIsolated {
            if let id = speaking[key] { send?(["type": "speak_started", "id": id]) }
        }
    }

    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        let key = ObjectIdentifier(utterance)
        MainActor.assumeIsolated { finished(key) }
    }

    nonisolated func speechSynthesizer(_ s: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        let key = ObjectIdentifier(utterance)
        MainActor.assumeIsolated { finished(key) }
    }

    private func finished(_ key: ObjectIdentifier) {
        if let id = speaking.removeValue(forKey: key) { send?(["type": "speak_finished", "id": id]) }
    }

    // MARK: audio session

    private func activateAudio() throws {
        let session = AVAudioSession.sharedInstance()
        if session.category != .playAndRecord {
            try session.setCategory(.playAndRecord, mode: .default,
                                    options: [.defaultToSpeaker, .duckOthers, .allowBluetoothHFP])
        }
        try session.setActive(true)
    }
}
