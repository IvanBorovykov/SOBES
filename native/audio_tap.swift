import Foundation
import CoreAudio
import AVFoundation

// A private, unmuted tap reads system playback without changing its output device.
final class AudioTap {
    private var tap = AudioObjectID(0)
    private var device = AudioObjectID(0)
    private var io: AudioDeviceIOProcID?
    private let queue = DispatchQueue(label: "system-audio-tap")
    private var converter: AVAudioConverter?
    private var inputFormat: AVAudioFormat?
    private let outputFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                             sampleRate: 16000, channels: 1, interleaved: false)!

    private func check(_ status: OSStatus, _ operation: String) throws {
        if status != noErr {
            throw NSError(domain: "SOBES.AudioTap", code: Int(status),
                          userInfo: [NSLocalizedDescriptionKey: "\(operation) (код \(status)). Проверьте разрешение macOS «Запись экрана и системного аудио»."])
        }
    }

    func start() throws {
        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        description.name = "Interview Assistant system audio"
        description.isPrivate = true
        description.muteBehavior = .unmuted
        try check(AudioHardwareCreateProcessTap(description, &tap), "Не удалось создать захват системного звука")
        var address = AudioObjectPropertyAddress(mSelector: kAudioTapPropertyFormat,
            mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        var asbd = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout.size(ofValue: asbd))
        try check(AudioObjectGetPropertyData(tap, &address, 0, nil, &size, &asbd), "Не удалось прочитать формат звука")
        guard let format = AVAudioFormat(streamDescription: &asbd),
              let converter = AVAudioConverter(from: format, to: outputFormat) else {
            throw NSError(domain: "SOBES.AudioTap", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Не удалось преобразовать системный звук в 16 кГц mono"])
        }
        inputFormat = format
        self.converter = converter
        let settings: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Interview Assistant private audio",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapUIDKey: description.uuid.uuidString,
                                             kAudioSubTapDriftCompensationKey: true]]
        ]
        try check(AudioHardwareCreateAggregateDevice(settings as CFDictionary, &device), "Не удалось подключить системный звук")
        try check(AudioDeviceCreateIOProcIDWithBlock(&io, device, queue) { [weak self] _, input, _, _, _ in
            self?.receive(input)
        }, "Не удалось подключить аудиобуфер")
        try check(AudioDeviceStart(device, io), "Не удалось запустить системный звук")
        FileHandle.standardError.write(Data("READY\n".utf8))
    }

    private func receive(_ list: UnsafePointer<AudioBufferList>) {
        guard let format = inputFormat, let converter = converter,
              let input = AVAudioPCMBuffer(pcmFormat: format,
                bufferListNoCopy: list, deallocator: nil) else { return }
        let capacity = AVAudioFrameCount(ceil(Double(input.frameLength) * 16000 / format.sampleRate)) + 32
        guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else { return }
        var supplied = false
        var error: NSError?
        let status = converter.convert(to: output, error: &error) { _, state in
            if supplied { state.pointee = .noDataNow; return nil }
            supplied = true
            state.pointee = .haveData
            return input
        }
        if status == .error {
            FileHandle.standardError.write(Data(("Ошибка преобразования системного звука: \(error?.localizedDescription ?? "неизвестная ошибка")\n").utf8))
            exit(1)
        }
        guard let floats = output.floatChannelData?[0], output.frameLength > 0 else { return }
        var pcm = [Int16](repeating: 0, count: Int(output.frameLength))
        for i in pcm.indices {
            let value = floats[i].isFinite ? floats[i] : 0
            pcm[i] = Int16(max(-32768, min(32767, value * 32768)))
        }
        pcm.withUnsafeBytes { FileHandle.standardOutput.write(Data($0)) }
    }

    func stop() {
        if let io = io {
            AudioDeviceStop(device, io)
            AudioDeviceDestroyIOProcID(device, io)
            self.io = nil
        }
        queue.sync {} // Finish callbacks before destroying their input device and converter.
        if device != 0 { AudioHardwareDestroyAggregateDevice(device); device = 0 }
        if tap != 0 { AudioHardwareDestroyProcessTap(tap); tap = 0 }
    }
}

let capture = AudioTap()
var signals = [DispatchSourceSignal]()
for number in [SIGINT, SIGTERM] {
    signal(number, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: number, queue: .main)
    source.setEventHandler { capture.stop(); exit(0) }
    source.resume()
    signals.append(source)
}
do {
    try capture.start()
} catch {
    capture.stop()
    FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
    exit(1)
}
dispatchMain()
