import Combine
import SwiftUI
import UIKit
import WebKit

/// The box's /app in a web view, with the native bridge (PHONE-APP.md, "The web view", version 1).
/// The page posts {v, type, ...} to the "hestia" handler; the app answers through
/// window.hestiaBridge.receive. Nothing goes to the page before its hello.
@MainActor
final class Bridge: NSObject, ObservableObject, WKScriptMessageHandler, WKNavigationDelegate {
    static let version = 1

    @Published var loadError: String?
    @Published var wantsSettings = false
    @Published var wantsDocuments = false
    weak var webView: WKWebView?
    weak var receipts: Receipts?
    let link: Link
    let notifier: Notifier
    let speech = Speech()
    private var greeted = false                  // the page said hello since it last loaded
    private var watching: AnyCancellable?

    init(link: Link, notifier: Notifier) {
        self.link = link
        self.notifier = notifier
        super.init()
        speech.send = { [weak self] msg in self?.send(msg) }
        watching = link.$connection.removeDuplicates().sink { [weak self] c in
            self?.send(c.message)
        }
    }

    // MARK: page -> app

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        var body = message.body as? [String: Any]
        if body == nil, let s = message.body as? String, let data = s.data(using: .utf8) {
            body = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        }
        guard let body, let type = body["type"] as? String else { return }
        link.note("page: \(type)")              // the type only, never what was said or spoken
        guard (body["v"] as? NSNumber)?.intValue == Self.version else {
            // a page on another version: our hello tells it so, and it turns its bridge off
            greeted = true
            sendHello()
            return
        }
        switch type {
        case "hello":
            greeted = true
            sendHello()
            send(link.connection.message)
        case "listen":
            speech.listen()
        case "listen_stop":
            speech.stopListening()
        case "speak":
            speech.speak(id: body["id"].map { "\($0)" } ?? "", text: body["text"] as? String ?? "")
        case "speak_stop":
            speech.stopSpeaking()
        case "documents":
            wantsDocuments = true                // scan or import into the library, natively
        case "receipt":
            receipts?.start()                    // the camera, then the lines to the box
        case "sync_notifications":
            Task { await notifier.sync(reason: "the page asked") }
        default:
            break                                // unknown types are ignored, both ways
        }
    }

    // MARK: app -> page

    private func sendHello() {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?"
        send(["type": "hello", "speech": speech.available, "documents": true, "receipts": true, "app": "\(version) (\(build))"])
    }

    func send(_ msg: [String: Any]) {
        guard greeted, let webView else { return }
        if let type = msg["type"] as? String, type != "transcript" || msg["final"] as? Bool == true {
            link.note("app: \(type)")
        }
        var m = msg
        m["v"] = Self.version
        guard let data = try? JSONSerialization.data(withJSONObject: m),
              let json = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.hestiaBridge && window.hestiaBridge.receive(\(json))")
    }

    /// The Today page, fetched fresh: a receipt's items wait there. The page's router skips a
    /// view it is already on, so on Today it refreshes instead.
    func showToday() {
        guard let webView else { return }
        // core.js declares Hestia with const: global, but not a property of window
        webView.evaluateJavaScript("""
            (function () {
              if (typeof Hestia === 'undefined' || !Hestia.views || !location.pathname.startsWith('/app')) return false;
              if (Hestia.current === 'today') Hestia.views.today.enter(); else location.hash = 'today';
              return true;
            })()
            """) { [weak self] result, _ in
            guard result as? Bool != true, let box = self?.link.savedBox,
                  let url = URL(string: box.appending(path: "app").absoluteString + "#today") else { return }
            webView.load(URLRequest(url: url))
        }
    }

    // MARK: navigation

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        // a new page load: it will say hello again; what the old page started ends quietly
        greeted = false
        speech.reset()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loadError = nil
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        let e = error as NSError
        // 102 is the cancel above (frame load interrupted), whose loadError is already set
        if e.domain == WKErrorDomain || e.domain == "WebKitErrorDomain", e.code == 102 { return }
        if e.code == NSURLErrorCancelled { return }
        loadError = error.localizedDescription
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled { loadError = error.localizedDescription }
    }

    /// The box's own pages stay in the app; anything else opens in Safari.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping @MainActor (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url, let box = link.savedBox else { return decisionHandler(.allow) }
        let sameBox = url.host() == box.host() && url.port == box.port
        if sameBox || url.scheme == "about" || url.scheme == "blob" || url.scheme == "data" {
            decisionHandler(.allow)
        } else {
            UIApplication.shared.open(url)
            decisionHandler(.cancel)
        }
    }

    /// An error status is a failed load, not a page: tailscale serve answers 502 with an empty
    /// body when the brain behind it is down, which would otherwise show as a blank page.
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping @MainActor (WKNavigationResponsePolicy) -> Void) {
        if response.isForMainFrame, let http = response.response as? HTTPURLResponse, http.statusCode >= 400 {
            loadError = http.statusCode == 502 || http.statusCode == 503 || http.statusCode == 504
                ? "The box answered \(http.statusCode): its HTTPS front is up, but the brain behind it is not."
                : "The box answered \(http.statusCode) for \(response.response.url?.path() ?? "the page")."
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        webView.reload()
    }
}

/// Keeps the user content controller from holding the bridge strongly.
private final class WeakHandler: NSObject, WKScriptMessageHandler {
    weak var target: WKScriptMessageHandler?
    init(_ target: WKScriptMessageHandler) { self.target = target }
    func userContentController(_ c: WKUserContentController, didReceive message: WKScriptMessage) {
        target?.userContentController(c, didReceive: message)
    }
}

struct WebContainer: UIViewRepresentable {
    let bridge: Bridge
    let url: URL

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        // registered before /app loads, so bridge.js finds it
        config.userContentController.add(WeakHandler(bridge), name: "hestia")
        // iOS zooms into a text field whose font is under 16 px and leaves the page zoomed.
        // In the app the page never needs zooming, so hold the scale at 1. (The lasting fix is
        // a 16 px input font in clients/app.css, which helps Safari too.)
        config.userContentController.addUserScript(WKUserScript(source: """
            (function () {
              var m = document.querySelector('meta[name=viewport]');
              if (!m) { m = document.createElement('meta'); m.name = 'viewport'; document.head.appendChild(m); }
              if (!/maximum-scale/.test(m.content)) m.content += ', maximum-scale=1';
            })();
            """, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = bridge
        web.allowsBackForwardNavigationGestures = true
        web.scrollView.contentInsetAdjustmentBehavior = .never   // the page pads for the notch itself
#if DEBUG
        web.isInspectable = true                                  // Safari's Develop menu
#endif
        let press = UILongPressGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.pressed(_:)))
        press.numberOfTouchesRequired = 2
        press.minimumPressDuration = 0.8
        press.cancelsTouchesInView = false
        press.delegate = context.coordinator
        web.addGestureRecognizer(press)
        bridge.webView = web
        web.load(URLRequest(url: url))
        context.coordinator.loaded = url
        return web
    }

    func updateUIView(_ web: WKWebView, context: Context) {
        if context.coordinator.loaded != url {                    // the box address changed
            context.coordinator.loaded = url
            web.load(URLRequest(url: url))
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator(bridge: bridge) }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        let bridge: Bridge
        var loaded: URL?
        init(bridge: Bridge) { self.bridge = bridge }

        @MainActor @objc func pressed(_ g: UILongPressGestureRecognizer) {
            if g.state == .began { bridge.wantsSettings = true }
        }

        func gestureRecognizer(_ g: UIGestureRecognizer, shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool {
            true
        }
    }
}
