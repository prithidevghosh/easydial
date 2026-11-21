import Foundation

struct ServiceError: LocalizedError {
    let message: String
    let status: Int
    var errorDescription: String? { message }
}

@MainActor
final class APIClient {
    var baseURL: URL
    var token: String?
    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    init(url: URL) { baseURL = url }

    func request<T: Decodable>(_ path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> T {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 180
        if let body {
            request.httpBody = try JSONSerialization.data(withJSONObject: body)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let data = try await send(request)
        return try decoder.decode(T.self, from: data)
    }

    func transcribe(sessionID: String, file: URL) async throws -> String {
        let boundary = UUID().uuidString
        var request = URLRequest(url: baseURL.appendingPathComponent("sessions/\(sessionID)/transcribe"))
        request.httpMethod = "POST"
        request.timeoutInterval = 90
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        var data = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"voice.m4a\"\r\nContent-Type: audio/mp4\r\n\r\n".utf8)
        data.append(try Data(contentsOf: file))
        data.append(Data("\r\n--\(boundary)--\r\n".utf8))
        request.httpBody = data
        struct Transcript: Decodable { let text: String }
        return try decoder.decode(Transcript.self, from: await send(request)).text
    }

    func speech(sessionID: String) async throws -> Data {
        var request = URLRequest(url: baseURL.appendingPathComponent("sessions/\(sessionID)/speech"))
        request.httpMethod = "POST"
        request.timeoutInterval = 60
        return try await send(request)
    }

    private func send(_ original: URLRequest) async throws -> Data {
        var request = original
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            let message = payload?["detail"] as? String ?? "That request couldn't be completed. Please try again."
            throw ServiceError(message: message, status: status)
        }
        return data
    }
}
