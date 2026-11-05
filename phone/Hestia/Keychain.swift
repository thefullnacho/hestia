import Foundation
import Security

/// The device token lives in the Keychain, this device only (PHONE-APP.md, "The connection").
enum Keychain {
    private static let query: [String: Any] = [
        kSecClass as String: kSecClassGenericPassword,
        kSecAttrService as String: "hestia",
        kSecAttrAccount as String: "device-token",
    ]

    static var token: String? {
        get {
            var q = query
            q[kSecReturnData as String] = true
            q[kSecMatchLimit as String] = kSecMatchLimitOne
            var out: CFTypeRef?
            guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
            return String(data: data, encoding: .utf8)
        }
        set {
            SecItemDelete(query as CFDictionary)
            guard let newValue, !newValue.isEmpty else { return }
            var q = query
            q[kSecValueData as String] = Data(newValue.utf8)
            q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            SecItemAdd(q as CFDictionary, nil)
        }
    }
}
