import Foundation
import ScreenCaptureKit
import AVFoundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

final class AudioOutput: NSObject, SCStreamOutput, SCStreamDelegate {
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fail("Захват звука остановлен: \(error.localizedDescription)")
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sample.isValid,
              let description = sample.formatDescription else { return }
        let format = AVAudioFormat(cmAudioFormatDescription: description)
        guard format.sampleRate == 16000, format.channelCount == 1, format.commonFormat == .pcmFormatFloat32 else {
            fail("Неожиданный формат системного аудио: \(format)")
        }
        guard let input = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(sample.numSamples))
        else { fail("Не удалось выделить аудиобуфер") }
        input.frameLength = AVAudioFrameCount(sample.numSamples)
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(sample, at: 0,
            frameCount: Int32(sample.numSamples), into: input.mutableAudioBufferList)
        guard status == noErr, let floats = input.floatChannelData?[0] else {
            fail("Неподдерживаемый формат системного аудио")
        }
        var pcm = [Int16](repeating: 0, count: Int(input.frameLength))
        for i in pcm.indices {
            let value = floats[i].isFinite ? floats[i] : 0
            pcm[i] = Int16(max(-32768, min(32767, value * 32768)))
        }
        pcm.withUnsafeBytes { FileHandle.standardOutput.write(Data($0)) }
    }
}

let output = AudioOutput()
var capture: SCStream?

func availableDisplay() async throws -> SCDisplay {
    var waiting = false
    while true {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        if let display = content.displays.first { return display }
        if !waiting {
            FileHandle.standardError.write(Data("WAITING_DISPLAY\n".utf8))
            waiting = true
        }
        try await Task.sleep(for: .seconds(2))
    }
}

Task { @MainActor in
    do {
        let display = try await availableDisplay()
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.sampleRate = 16000
        config.channelCount = 1
        config.excludesCurrentProcessAudio = true
        // No screen output is attached. Only PCM audio is written to stdout.
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(seconds: 1, preferredTimescale: 1)
        let stream = SCStream(filter: filter, configuration: config, delegate: output)
        try stream.addStreamOutput(output, type: .audio, sampleHandlerQueue: DispatchQueue(label: "system-audio"))
        capture = stream
        try await stream.startCapture()
        FileHandle.standardError.write(Data("READY\n".utf8))
    } catch {
        fail("Не удалось захватить звук компьютера: \(error.localizedDescription). Проверьте разрешение macOS «Запись экрана и системного аудио».")
    }
}
dispatchMain()
