import AVFoundation
import Foundation
import Observation

@Observable @MainActor
final class VoiceRecorder {
    private var recorder: AVAudioRecorder?
    private var player: AVAudioPlayer?
    private var deadline: Task<Void, Never>?
    private(set) var isRecording = false
    private(set) var seconds = 0
    var onLimit: (() -> Void)?

    func start() async throws {
        stopPlayback()
        let allowed = await AVAudioApplication.requestRecordPermission()
        guard allowed else {
            throw ServiceError(message: "Allow microphone access in iPhone Settings to speak, or type your request below.", status: 0)
        }
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
        try session.setActive(true)
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("easydial-\(UUID().uuidString).m4a")
        recorder = try AVAudioRecorder(url: url, settings: [AVFormatIDKey: kAudioFormatMPEG4AAC,
                                                          AVSampleRateKey: 24000, AVNumberOfChannelsKey: 1,
                                                          AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue])
        guard recorder?.record() == true else { throw ServiceError(message: "The microphone couldn't start. Please try again.", status: 0) }
        seconds = 0
        isRecording = true
        deadline = Task { [weak self] in
            for value in 1...60 {
                try? await Task.sleep(for: .seconds(1))
                guard !Task.isCancelled, let self, self.isRecording else { return }
                self.seconds = value
            }
            self?.onLimit?()
        }
    }

    func stop() -> URL? {
        deadline?.cancel()
        guard isRecording else { return nil }
        recorder?.stop()
        isRecording = false
        return recorder?.url
    }

    func cancel() {
        if let url = stop() { try? FileManager.default.removeItem(at: url) }
        stopPlayback()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    func play(_ data: Data) throws {
        try AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio)
        try AVAudioSession.sharedInstance().setActive(true)
        player = try AVAudioPlayer(data: data)
        player?.play()
    }

    func stopPlayback() { player?.stop() }
}
