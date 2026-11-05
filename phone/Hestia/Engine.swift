import Foundation
import llama

/// One "complete" from the box, as PHONE-APP.md's wire format defines it.
struct CompleteRequest: Sendable {
    let id: String
    let prompt: String
    let nPredict: Int
    let temperature: Double
    let stop: [String]
    let wantsJSON: Bool

    init?(_ msg: [String: Any]) {
        guard let prompt = msg["prompt"] as? String else { return nil }
        id = msg["id"].map { "\($0)" } ?? ""
        self.prompt = prompt
        nPredict = (msg["n_predict"] as? NSNumber)?.intValue ?? -1
        temperature = (msg["temperature"] as? NSNumber)?.doubleValue ?? 0.8
        stop = (msg["stop"] as? [String]) ?? []
        wantsJSON = msg["json_schema"] != nil && !(msg["json_schema"] is NSNull)
    }
}

struct Finish: Sendable {
    let stopType: String          // "eos" | "limit" | "word"
    let tokensEvaluated: Int      // the prompt's tokens, as llama-server counts them
    let tokensPredicted: Int
    let tokensReused: Int         // prompt tokens already in the cache from the last request
    let promptSeconds: Double
    let generateSeconds: Double
}

enum EngineError: LocalizedError {
    case load(String), context, tokenize, decode(Int32), tooLong(Int, Int)
    var errorDescription: String? {
        switch self {
        case .load(let p): return "could not load the model at \(p)"
        case .context: return "could not create a llama context"
        case .tokenize: return "could not tokenize the prompt"
        case .decode(let c): return "llama_decode failed (\(c))"
        case .tooLong(let n, let max): return "prompt is \(n) tokens, the context holds \(max)"
        }
    }
}

/// llama.cpp on a string, the way llama-server's /completion runs it, so the box's parity with
/// llama-server (benchmarks/phone-render/) holds here: the prompt is tokenized with special tokens
/// parsed and added, the sampler chain is llama-server's default one, special tokens are not
/// printed, stop words are cut and never streamed, and a shared prompt prefix is reused through
/// context checkpoints.
actor Engine {
    nonisolated let path: String
    private let model: OpaquePointer
    private let ctx: OpaquePointer
    private let vocab: OpaquePointer
    private let nCtx: Int
    private let nBatch: Int
    private let nUbatch: Int
    private var cached: [llama_token] = []

    private static let backend: Void = { llama_backend_init() }()

    init(path: String, contextTokens: Int = 8192) throws {
        _ = Engine.backend
        self.path = path
        var mp = llama_model_default_params()
#if targetEnvironment(simulator)
        mp.n_gpu_layers = 0          // the simulator has no usable Metal for this
#else
        mp.n_gpu_layers = 999
#endif
        guard let model = llama_model_load_from_file(path, mp) else { throw EngineError.load(path) }
        var cp = llama_context_default_params()
        cp.n_ctx = UInt32(contextTokens)
        cp.n_batch = 2048
        cp.n_ubatch = 512
        let threads = Int32(max(1, min(8, ProcessInfo.processInfo.activeProcessorCount - 2)))
        cp.n_threads = threads
        cp.n_threads_batch = threads
        guard let ctx = llama_init_from_model(model, cp) else {
            llama_model_free(model)
            throw EngineError.context
        }
        self.model = model
        self.ctx = ctx
        self.vocab = llama_model_get_vocab(model)
        self.nCtx = Int(llama_n_ctx(ctx))
        self.nBatch = Int(llama_n_batch(ctx))
        self.nUbatch = Int(llama_n_ubatch(ctx))
    }

    deinit {
        llama_free(ctx)
        llama_model_free(model)
    }

    func describe() -> String {
        var buf = [CChar](repeating: 0, count: 256)
        llama_model_desc(model, &buf, buf.count)
        return String(cString: buf) + ", ctx \(nCtx)"
    }

    /// Run one request. `emit` gets text as it is safe to send (never part of a stop word).
    func complete(_ req: CompleteRequest, emit: (String) async throws -> Void) async throws -> Finish {
        let tokens = try tokenize(req.prompt)
        guard !tokens.isEmpty else { throw EngineError.tokenize }
        guard tokens.count < nCtx else { throw EngineError.tooLong(tokens.count, nCtx) }

        let t0 = Date()
        let (keep, shared) = rewind(to: tokens)

        // Read the rest of the prompt, stopping to checkpoint 4 + n_ubatch and 4 tokens before
        // its end, as llama-server does: the next call in a turn re-renders the last few tokens
        // (the assistant header), so a checkpoint just short of them is the one that gets reused.
        // Also checkpoint where this prompt left the last one: that is where the part that stays
        // the same across turns ends (the system prompt and tools, then the history), so the
        // next turn restores there however long the changing tail is. llama-server finds that
        // point from message boundaries, which the phone never sees.
        let marks = Set([tokens.count - 4 - nUbatch, tokens.count - 4, shared].filter { $0 > keep && $0 < tokens.count })
        var start = keep
        while start < tokens.count {
            try Task.checkCancellation()
            if marks.contains(start) { saveCheckpoint() }
            var end = min(start + nBatch, tokens.count)
            if let mark = marks.filter({ $0 > start && $0 < end }).min() { end = mark }
            try decode(Array(tokens[start..<end]))
            start = end
        }
        let t1 = Date()

        let sampler = makeSampler(temperature: req.temperature, json: req.wantsJSON)
        defer { llama_sampler_free(sampler) }
        let stops = req.stop.map { Array($0.utf8) }.filter { !$0.isEmpty }
        let limit = req.nPredict < 0 ? Int.max : req.nPredict
        var text: [UInt8] = []          // complete UTF-8 only
        var pending: [UInt8] = []       // a character's bytes still arriving
        var sent = 0
        var produced = 0
        var stopType = "limit"

        generate: while produced < limit && cached.count < nCtx {
            try Task.checkCancellation()
            let token = llama_sampler_sample(sampler, ctx, -1)
            if llama_vocab_is_eog(vocab, token) { stopType = "eos"; break }
            produced += 1
            pending += piece(token)
            let whole = completeUTF8Prefix(pending)
            text += pending[..<whole]
            pending.removeFirst(whole)

            for stop in stops {
                if let at = find(stop, in: text, from: sent) {
                    if at > sent { try await emit(String(decoding: text[sent..<at], as: UTF8.self)) }
                    sent = text.count
                    stopType = "word"
                    break generate
                }
            }
            let safe = text.count - (stops.map { partialSuffix(of: text, prefixOf: $0) }.max() ?? 0)
            if safe > sent {
                try await emit(String(decoding: text[sent..<safe], as: UTF8.self))
                sent = safe
            }
            try decode([token])
        }
        if stopType != "word" && sent < text.count + pending.count {
            try await emit(String(decoding: text[sent...] + pending, as: UTF8.self))
        }
        return Finish(stopType: stopType, tokensEvaluated: tokens.count, tokensPredicted: produced,
                      tokensReused: keep, promptSeconds: t1.timeIntervalSince(t0),
                      generateSeconds: Date().timeIntervalSince(t1))
    }

    // MARK: - context reuse

    /// The recurrent half of the cache at a point in an earlier prompt. A hybrid model like
    /// LFM3 cannot cut its recurrent state back to an earlier position, so reuse restores one
    /// of these and trims the attention half to match (llama-server's context checkpoints).
    private struct Checkpoint { let nTokens: Int; var used: Int; let data: [UInt8] }
    private var checkpoints: [Checkpoint] = []
    private var clock = 0                                  // for least recently used
    private static let maxCheckpoints = 16
    private static let partial = llama_state_seq_flags(LLAMA_STATE_SEQ_FLAGS_PARTIAL_ONLY)

    /// Bring the cache back to the longest prefix it shares with `tokens`. Returns where reading
    /// resumes, and how far the two prompts agree. At least one token is always left to read,
    /// for the logits of the last.
    private func rewind(to tokens: [llama_token]) -> (keep: Int, shared: Int) {
        let shared = zip(cached, tokens).prefix { $0 == $1 }.count
        let target = min(shared, tokens.count - 1)
        let mem = llama_get_memory(ctx)
        // checkpoints past the shared prefix hold tokens this prompt does not have
        checkpoints.removeAll { $0.nTokens > target }

        if target == cached.count { return (target, shared) }      // a pure extension
        if target > 0 && llama_memory_seq_rm(mem, 0, Int32(target), -1) {
            cached.removeLast(cached.count - target)               // attention-only, or a short rollback
            return (target, shared)
        }
        for (i, cp) in checkpoints.enumerated().reversed() where cp.nTokens > 0 {
            let restored = cp.data.withUnsafeBufferPointer {
                llama_state_seq_set_data_ext(ctx, $0.baseAddress, $0.count, 0, Self.partial)
            }
            // the recurrent state now ends at nTokens - 1; drop the attention cells after it
            if restored == cp.data.count && llama_memory_seq_rm(mem, 0, Int32(cp.nTokens), -1) {
                cached.removeLast(cached.count - cp.nTokens)
                clock += 1
                checkpoints[i].used = clock                        // the front one stays: every first call restores it
                return (cp.nTokens, shared)
            }
        }
        llama_memory_clear(mem, true)
        cached = []
        checkpoints = []
        return (0, shared)
    }

    /// Keep the recurrent state as it stands after `cached`, before reading on.
    private func saveCheckpoint() {
        let n = cached.count
        guard n > 0, !checkpoints.contains(where: { $0.nTokens == n }) else { return }
        let size = llama_state_seq_get_size_ext(ctx, 0, Self.partial)
        guard size > 0 else { return }
        var data = [UInt8](repeating: 0, count: size)
        let got = data.withUnsafeMutableBufferPointer {
            llama_state_seq_get_data_ext(ctx, $0.baseAddress, size, 0, Self.partial)
        }
        guard got == size else { return }
        clock += 1
        checkpoints.append(Checkpoint(nTokens: n, used: clock, data: data))
        checkpoints.sort { $0.nTokens < $1.nTokens }
        if checkpoints.count > Self.maxCheckpoints,              // the least recently used goes
           let stale = checkpoints.indices.min(by: { checkpoints[$0].used < checkpoints[$1].used }) {
            checkpoints.remove(at: stale)
        }
    }

    // MARK: - llama.cpp

    /// Decode tokens at the positions after `cached`, with logits for the last one only.
    private func decode(_ batchTokens: [llama_token]) throws {
        var batch = llama_batch_init(Int32(batchTokens.count), 0, 1)
        defer { llama_batch_free(batch) }
        let base = cached.count
        for (i, token) in batchTokens.enumerated() {
            batch.token[i] = token
            batch.pos[i] = llama_pos(base + i)
            batch.n_seq_id[i] = 1
            batch.seq_id[i]![0] = 0
            batch.logits[i] = i == batchTokens.count - 1 ? 1 : 0
        }
        batch.n_tokens = Int32(batchTokens.count)
        let rc = llama_decode(ctx, batch)
        guard rc == 0 else { throw EngineError.decode(rc) }
        cached += batchTokens
    }

    /// llama-server's /completion: add_special and parse_special both on.
    private func tokenize(_ text: String) throws -> [llama_token] {
        let n = Int32(text.utf8.count)
        var out = [llama_token](repeating: 0, count: Int(n) + 8)
        var got = llama_tokenize(vocab, text, n, &out, Int32(out.count), true, true)
        if got < 0 {
            out = [llama_token](repeating: 0, count: Int(-got))
            got = llama_tokenize(vocab, text, n, &out, Int32(out.count), true, true)
        }
        guard got >= 0 else { throw EngineError.tokenize }
        return Array(out[..<Int(got)])
    }

    /// The token's bytes, special tokens unprinted (llama-server's default).
    private func piece(_ token: llama_token) -> [UInt8] {
        var buf = [CChar](repeating: 0, count: 64)
        var n = llama_token_to_piece(vocab, token, &buf, Int32(buf.count), 0, false)
        if n < 0 {
            buf = [CChar](repeating: 0, count: Int(-n))
            n = llama_token_to_piece(vocab, token, &buf, Int32(buf.count), 0, false)
        }
        return buf[..<max(0, Int(n))].map { UInt8(bitPattern: $0) }
    }

    /// llama-server's default chain at its default settings, or greedy at temperature 0.
    private func makeSampler(temperature: Double, json: Bool) -> UnsafeMutablePointer<llama_sampler> {
        let chain = llama_sampler_chain_init(llama_sampler_chain_default_params())!
        if json {
            // The box asks for a schema only to repair one tool call's arguments. A schema to
            // GBNF converter is C++ outside the framework; for now any JSON object is allowed
            // and the box validates the arguments, as it does for every call.
            llama_sampler_chain_add(chain, llama_sampler_init_grammar(vocab, Self.jsonGrammar, "root"))
        }
        if temperature <= 0 {
            llama_sampler_chain_add(chain, llama_sampler_init_greedy())
        } else {
            llama_sampler_chain_add(chain, llama_sampler_init_top_k(40))
            llama_sampler_chain_add(chain, llama_sampler_init_top_p(0.95, 1))
            llama_sampler_chain_add(chain, llama_sampler_init_min_p(0.05, 1))
            llama_sampler_chain_add(chain, llama_sampler_init_temp(Float(temperature)))
            llama_sampler_chain_add(chain, llama_sampler_init_dist(LLAMA_DEFAULT_SEED))
        }
        return chain
    }

    // From llama.cpp's grammars/json.gbnf (MIT).
    private static let jsonGrammar = #"""
    root   ::= object
    value  ::= object | array | string | number | ("true" | "false" | "null") ws
    object ::= "{" ws ( string ":" ws value ("," ws string ":" ws value)* )? "}" ws
    array  ::= "[" ws ( value ("," ws value)* )? "]" ws
    string ::= "\"" ( [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F]{4}) )* "\"" ws
    number ::= ("-"? ([0-9] | [1-9] [0-9]{0,15})) ("." [0-9]+)? ([eE] [-+]? [0-9] [1-9]{0,15})? ws
    ws ::= | " " | "\n" [ \t]{0,20}
    """#
}

// MARK: - text helpers (free functions so they are testable without a model)

/// How many leading bytes form complete UTF-8; the rest waits for the next token.
func completeUTF8Prefix(_ bytes: [UInt8]) -> Int {
    let n = bytes.count
    for back in 1...min(4, max(1, n)) where n >= back {
        let b = bytes[n - back]
        if b & 0xC0 == 0x80 { continue }                    // continuation byte
        let need = b >= 0xF0 ? 4 : b >= 0xE0 ? 3 : b >= 0xC0 ? 2 : 1
        return need > back ? n - back : n
    }
    return n
}

func find(_ needle: [UInt8], in hay: [UInt8], from: Int) -> Int? {
    guard needle.count <= hay.count else { return nil }
    var i = max(0, from)
    while i + needle.count <= hay.count {
        if hay[i] == needle[0] && hay[i..<(i + needle.count)].elementsEqual(needle) { return i }
        i += 1
    }
    return nil
}

/// The length of the longest tail of `text` that is a proper prefix of `stop`: bytes to hold back.
func partialSuffix(of text: [UInt8], prefixOf stop: [UInt8]) -> Int {
    var k = min(stop.count - 1, text.count)
    while k > 0 {
        if text[(text.count - k)...].elementsEqual(stop[..<k]) { return k }
        k -= 1
    }
    return 0
}
