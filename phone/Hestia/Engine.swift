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
/// printed, stop words are cut and never streamed, and a shared prompt prefix is reused.
actor Engine {
    nonisolated let path: String
    private let model: OpaquePointer
    private let ctx: OpaquePointer
    private let vocab: OpaquePointer
    private let nCtx: Int
    private let nBatch: Int
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

        // Reuse the cached prefix. A recurrent model (LFM3 is hybrid) may refuse a partial
        // removal; then the cache starts over.
        let t0 = Date()
        var keep = zip(cached, tokens).prefix { $0 == $1 }.count
        keep = min(keep, tokens.count - 1)                  // always decode one, for its logits
        let mem = llama_get_memory(ctx)
        if keep < cached.count && !llama_memory_seq_rm(mem, 0, Int32(keep), -1) {
            llama_memory_clear(mem, true)
            keep = 0
        }
        cached = Array(tokens[..<keep])
        var start = keep
        while start < tokens.count {
            try Task.checkCancellation()
            let end = min(start + nBatch, tokens.count)
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

    // MARK: - llama.cpp

    private func decode(_ batchTokens: [llama_token]) throws {
        var toks = batchTokens
        let rc = toks.withUnsafeMutableBufferPointer { buf in
            llama_decode(ctx, llama_batch_get_one(buf.baseAddress, Int32(buf.count)))
        }
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
