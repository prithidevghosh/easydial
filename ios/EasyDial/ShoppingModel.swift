import Foundation
import Observation

@Observable @MainActor
final class ShoppingModel {
    var session: ShoppingSession?
    var config: AppConfig?
    var busy = false
    var error: String?
    var transcript = ""
    var draft = ""
    var replaceLineID: String?
    var backendURL = UserDefaults.standard.string(forKey: "backendURL") ?? "http://127.0.0.1:8000"
    let voice = VoiceRecorder()
    private var client = APIClient(url: URL(string: "http://127.0.0.1:8000")!)

    func connect() async {
        guard let url = URL(string: backendURL), ["http", "https"].contains(url.scheme), url.host != nil else {
            error = "Enter a valid server URL in connection settings."
            return
        }
        voice.cancel()
        session = nil
        config = nil
        client = APIClient(url: url)
        UserDefaults.standard.set(backendURL, forKey: "backendURL")
        await perform {
            self.config = try await self.client.request("config")
            let created: SessionCreated = try await self.client.request("sessions", method: "POST")
            self.client.token = created.token
            self.session = created.session
            self.replaceLineID = nil
            self.transcript = ""
        }
    }

    func search(_ query: String) async {
        guard let session else { return }
        await perform {
            self.session = try await self.client.request("sessions/\(session.id)/search", method: "POST",
                                                         body: ["query": query, "expected_version": session.version])
        }
    }

    func sendText(_ text: String) async {
        guard let session, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        voice.stopPlayback()
        await perform {
            self.session = try await self.client.request("sessions/\(session.id)/turn", method: "POST",
                                                         body: ["text": text, "expected_version": session.version])
            self.draft = ""
        }
    }

    func edit(kind: String, offerID: String? = nil, lineID: String? = nil, quantity: Int = 1) async {
        guard let session else { return }
        await perform {
            var body: [String: Any] = ["kind": kind, "expected_version": session.version, "quantity": quantity]
            if let offerID { body["offer_id"] = offerID }
            if let lineID { body["line_id"] = lineID }
            self.session = try await self.client.request("sessions/\(session.id)/basket", method: "POST", body: body)
            self.replaceLineID = nil
        }
    }

    func review() async {
        guard let session else { return }
        voice.stopPlayback()
        await perform {
            self.session = try await self.client.request("sessions/\(session.id)/review", method: "POST",
                                                         body: ["expected_version": session.version])
        }
    }

    func confirm() async {
        guard let session, let review = session.review else { return }
        await perform {
            self.session = try await self.client.request("sessions/\(session.id)/confirm", method: "POST",
                                                         body: ["expected_version": session.version, "review_id": review.id, "confirmed": true])
        }
    }

    func toggleRecording() async {
        if voice.isRecording {
            guard let file = voice.stop(), let session else { return }
            defer { try? FileManager.default.removeItem(at: file) }
            await perform {
                self.transcript = try await self.client.transcribe(sessionID: session.id, file: file)
            }
            if error == nil {
                await sendText(transcript)
                if error == nil { await speak() }
            }
        } else {
            error = nil
            guard config?.voiceAvailable == true else {
                error = "Voice isn't connected yet. Try the sample searches below."
                return
            }
            voice.onLimit = { [weak self] in Task { await self?.toggleRecording() } }
            do { try await voice.start() } catch { self.error = error.localizedDescription }
        }
    }

    func speak() async {
        guard let session else { return }
        await perform { try self.voice.play(await self.client.speech(sessionID: session.id)) }
    }

    private func perform(_ action: () async throws -> Void) async {
        guard !busy else { return }
        busy = true
        error = nil
        defer { busy = false }
        do { try await action() }
        catch let failure as ServiceError {
            error = failure.message
            if failure.status == 409, let session {
                self.session = try? await client.request("sessions/\(session.id)")
            }
        } catch {
            self.error = "Couldn't reach EasyDial. Check the server connection in Settings and try again."
        }
    }
}
