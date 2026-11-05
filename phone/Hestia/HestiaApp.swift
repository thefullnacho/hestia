import SwiftUI

@main
struct HestiaApp: App {
    @StateObject private var link = Link()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            ContentView(link: link)
        }
        // The model runs only while the app is open: dial the box in the foreground, hang up after.
        .onChange(of: phase) { _, now in
            switch now {
            case .active: link.start()
            case .background: link.stop()
            default: break
            }
        }
    }
}

struct ContentView: View {
    @ObservedObject var link: Link

    var body: some View {
        NavigationStack {
            Form {
                Section("Box") {
                    TextField("https://box.your-tailnet.ts.net:8444", text: $link.boxAddress)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                    SecureField("device token", text: $link.token)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button("Save and connect") { link.save() }
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
                }
                Section("Log") {
                    ForEach(Array(link.log.enumerated()), id: \.offset) { _, line in
                        Text(line).font(.caption.monospaced())
                    }
                }
            }
            .navigationTitle("Hestia")
        }
    }
}
