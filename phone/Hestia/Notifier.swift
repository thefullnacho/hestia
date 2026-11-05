import Foundation
import UIKit
import UserNotifications

/// Reminders as local notifications, and the push token for the content-free ping
/// (PHONE-APP.md, "Notifications"). The box's reminder rows are fetched and scheduled with iOS
/// here, with their text, so no Apple server ever sees them. Rows only, no model.
@MainActor
final class Notifier: NSObject, ObservableObject, UNUserNotificationCenterDelegate {
    @Published var lastSync = ""
    let link: Link
    private var syncing = false
    private let center = UNUserNotificationCenter.current()
    private static let syncKey = "reminder_sync"            // the box's ETag for what is scheduled

    init(link: Link) {
        self.link = link
        super.init()
        center.delegate = self
    }

    /// Ask once for permission, then register for the ping. iOS hands the token to AppDelegate.
    func setUp() async {
        _ = try? await center.requestAuthorization(options: [.alert, .sound, .badge])
        UIApplication.shared.registerForRemoteNotifications()
    }

    struct Reminder: Decodable { let id: String; let text: String; let due_at: String }
    struct Listing: Decodable { let reminders: [Reminder]; let sync: String; let truncated: Bool? }

    /// GET /v1/phone/reminders and, when the list changed, replace every pending notification.
    func sync(reason: String) async {
        guard !syncing, let box = link.savedBox, !link.token.isEmpty else { return }
        syncing = true
        defer { syncing = false }
        var comps = URLComponents(url: box.appending(path: "v1/phone/reminders"), resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "days", value: "14")]
        var request = URLRequest(url: comps.url!)
        request.setValue("Bearer \(link.token)", forHTTPHeaderField: "Authorization")
        let known = UserDefaults.standard.string(forKey: Self.syncKey)
        if let known { request.setValue("\"\(known)\"", forHTTPHeaderField: "If-None-Match") }
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            switch status {
            case 304:
                lastSync = "reminders unchanged (\(reason))"
            case 200:
                let listing = try JSONDecoder().decode(Listing.self, from: data)
                if listing.sync == known {
                    lastSync = "reminders unchanged (\(reason))"
                    return
                }
                let scheduled = await schedule(listing.reminders)
                UserDefaults.standard.set(listing.sync, forKey: Self.syncKey)
                lastSync = "\(scheduled) reminders scheduled (\(reason))" + (listing.truncated == true ? ", list cut at 64" : "")
            case 401:
                lastSync = "reminders: the box rejected the device token"
            case 503:
                lastSync = "reminders: the box could not read whose phone this is"
            default:
                lastSync = "reminders: HTTP \(status)"
            }
        } catch {
            lastSync = "reminders: \(error.localizedDescription)"
        }
    }

    private func schedule(_ reminders: [Reminder]) async -> Int {
        center.removeAllPendingNotificationRequests()      // every pending one is Hestia's
        let parse = ISO8601DateFormatter()
        let parseFractional = ISO8601DateFormatter()
        parseFractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var count = 0
        for r in reminders.prefix(64) {
            guard let due = parse.date(from: r.due_at) ?? parseFractional.date(from: r.due_at),
                  due > Date() else { continue }
            let content = UNMutableNotificationContent()
            content.title = "Hestia"
            content.body = r.text
            content.sound = .default
            content.threadIdentifier = "reminders"
            // an absolute moment: the offset in due_at fixes it, whatever zone the phone is in
            let parts = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute, .second], from: due)
            let trigger = UNCalendarNotificationTrigger(dateMatching: parts, repeats: false)
            do {
                try await center.add(UNNotificationRequest(identifier: r.id, content: content, trigger: trigger))
                count += 1
            } catch {
                continue
            }
        }
        return count
    }

    // Shown even while the app is open.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification)
        async -> UNNotificationPresentationOptions {
        [.banner, .list, .sound]
    }

    // Tapping one only needs to open the app: it connects, the box drains waiting work, the page syncs.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {}
}

/// Hands iOS's push token to the socket hello, as lowercase hex.
final class AppDelegate: NSObject, UIApplicationDelegate {
    static let tokenKey = "push_token"
    static let tokenChanged = Notification.Name("hestia.pushToken")

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        guard UserDefaults.standard.string(forKey: Self.tokenKey) != hex else { return }
        UserDefaults.standard.set(hex, forKey: Self.tokenKey)
        NotificationCenter.default.post(name: Self.tokenChanged, object: nil)
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        UserDefaults.standard.set(error.localizedDescription, forKey: "push_error")
    }
}
