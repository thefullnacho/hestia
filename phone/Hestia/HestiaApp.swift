import SwiftUI

@main
struct HestiaApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var link: Link
    @StateObject private var notifier: Notifier
    @StateObject private var bridge: Bridge
    @StateObject private var documents: Documents
    @Environment(\.scenePhase) private var phase

    init() {
        let link = Link()
        let notifier = Notifier(link: link)
        _link = StateObject(wrappedValue: link)
        _notifier = StateObject(wrappedValue: notifier)
        _bridge = StateObject(wrappedValue: Bridge(link: link, notifier: notifier))
        _documents = StateObject(wrappedValue: Documents(link: link))
    }

    var body: some Scene {
        WindowGroup {
            RootView(link: link, notifier: notifier, bridge: bridge, documents: documents)
        }
        // The model runs only while the app is open: dial the box in the foreground, hang up
        // after. Opening the app also syncs the reminders.
        .onChange(of: phase) { _, now in
            switch now {
            case .active:
                link.start()
                Task {
                    await notifier.setUp()
                    await notifier.sync(reason: "app opened")
                }
            case .background:
                link.stop()
            default:
                break
            }
        }
    }
}

/// The box's /app once the app is set up; the settings until then. A two-finger long press on
/// the page, or a page that will not load, brings the settings back.
struct RootView: View {
    @ObservedObject var link: Link
    @ObservedObject var notifier: Notifier
    @ObservedObject var bridge: Bridge
    @ObservedObject var documents: Documents

    var body: some View {
        Group {
            if link.configured, let box = link.savedBox {
                WebContainer(bridge: bridge, url: box.appending(path: "app"))
                    .ignoresSafeArea()
                    .overlay {
                        if let error = bridge.loadError {
                            LoadFailed(error: error, status: link.status,
                                       retry: { bridge.loadError = nil; bridge.webView?.reload() },
                                       settings: { bridge.wantsSettings = true })
                        }
                    }
                    .sheet(isPresented: $bridge.wantsDocuments) {
                        NavigationStack {
                            DocumentsView(documents: documents)
                                .toolbar {
                                    ToolbarItem(placement: .confirmationAction) {
                                        Button("Done") { bridge.wantsDocuments = false }
                                    }
                                }
                        }
                    }
            } else {
                NavigationStack { SettingsView(link: link, notifier: notifier, documents: documents) }
            }
        }
        .sheet(isPresented: $bridge.wantsSettings) {
            NavigationStack {
                SettingsView(link: link, notifier: notifier, documents: documents)
                    .toolbar {
                        ToolbarItem(placement: .confirmationAction) {
                            Button("Done") { bridge.wantsSettings = false }
                        }
                    }
            }
        }
    }
}

struct LoadFailed: View {
    let error: String
    let status: String
    let retry: () -> Void
    let settings: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Text("Can't reach the box").font(.title2.bold())
            Text(error).multilineTextAlignment(.center).foregroundStyle(.secondary)
            Text("Model socket: \(status)").font(.footnote).foregroundStyle(.secondary)
            HStack {
                Button("Retry", action: retry).buttonStyle(.borderedProminent)
                Button("Settings", action: settings).buttonStyle(.bordered)
            }
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(.background)
    }
}

struct SettingsView: View {
    @ObservedObject var link: Link
    @ObservedObject var notifier: Notifier
    @ObservedObject var documents: Documents

    var body: some View {
        Form {
            Section("Box") {
                TextField("https://box.your-tailnet.ts.net:8443", text: $link.boxAddress)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .keyboardType(.URL)
                SecureField("device token", text: $link.token)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("Save and connect") { link.save() }
            }
            if link.configured {
                Section {
                    NavigationLink("Documents in the library") { DocumentsView(documents: documents) }
                }
            }
            Section("Status") {
                LabeledContent("Socket", value: link.status)
                LabeledContent("Model", value: link.modelName)
                if let p = link.download {
                    ProgressView(value: p) { Text("Downloading the model") }
                }
                if !link.lastAnswer.isEmpty {
                    Text(link.lastAnswer).font(.footnote.monospacedDigit())
                }
                if !notifier.lastSync.isEmpty {
                    LabeledContent("Reminders", value: notifier.lastSync)
                }
                LabeledContent("Push", value: pushState)
            }
            Section("Log") {
                ForEach(Array(link.log.enumerated()), id: \.offset) { _, line in
                    Text(line).font(.caption.monospaced())
                }
            }
        }
        .navigationTitle("Hestia")
    }

    private var pushState: String {
        if UserDefaults.standard.string(forKey: AppDelegate.tokenKey) != nil { return "registered" }
        return UserDefaults.standard.string(forKey: "push_error") ?? "not registered"
    }
}
