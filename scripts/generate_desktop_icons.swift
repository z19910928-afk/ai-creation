import AppKit
import Foundation

// Extend the existing green "墨" brand mark into native application icons.
let destination = URL(fileURLWithPath: CommandLine.arguments.dropFirst().first ?? "desktop/assets")
let files = FileManager.default
let iconset = destination.appendingPathComponent("icon.iconset")
try files.createDirectory(at: iconset, withIntermediateDirectories: true)

func png(_ size: Int) throws -> Data {
    let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
        isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
    )!
    let context = NSGraphicsContext(bitmapImageRep: bitmap)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    context.cgContext.scaleBy(x: CGFloat(size) / 1024, y: CGFloat(size) / 1024)
    NSColor(calibratedRed: 23 / 255, green: 107 / 255, blue: 101 / 255, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 64, y: 64, width: 896, height: 896),
                 xRadius: 190, yRadius: 190).fill()
    let font = NSFont(name: "Songti SC", size: 610) ?? NSFont.systemFont(ofSize: 610, weight: .semibold)
    let text = NSAttributedString(string: "墨", attributes: [.font: font, .foregroundColor: NSColor.white])
    let bounds = text.size()
    text.draw(at: NSPoint(x: (1024 - bounds.width) / 2, y: (1024 - bounds.height) / 2))
    context.flushGraphics()
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])!
}

for size in [16, 32, 128, 256, 512] {
    try png(size).write(to: iconset.appendingPathComponent("icon_\(size)x\(size).png"))
    try png(size * 2).write(to: iconset.appendingPathComponent("icon_\(size)x\(size)@2x.png"))
}
try png(1024).write(to: destination.appendingPathComponent("icon.png"))
let tool = Process()
tool.executableURL = URL(fileURLWithPath: "/usr/bin/iconutil")
tool.arguments = ["-c", "icns", iconset.path, "-o", destination.appendingPathComponent("icon.icns").path]
try tool.run()
tool.waitUntilExit()
guard tool.terminationStatus == 0 else { exit(1) }

func littleEndian<T: FixedWidthInteger>(_ value: T) -> Data {
    var value = value.littleEndian
    return withUnsafeBytes(of: &value) { Data($0) }
}
let sizes = [16, 24, 32, 48, 64, 128, 256]
let images = try sizes.map { try png($0) }
var ico = littleEndian(UInt16(0)) + littleEndian(UInt16(1)) + littleEndian(UInt16(sizes.count))
var offset = 6 + 16 * sizes.count
for (size, image) in zip(sizes, images) {
    ico.append(contentsOf: [UInt8(size % 256), UInt8(size % 256), 0, 0])
    ico.append(littleEndian(UInt16(1)))
    ico.append(littleEndian(UInt16(32)))
    ico.append(littleEndian(UInt32(image.count)))
    ico.append(littleEndian(UInt32(offset)))
    offset += image.count
}
for image in images { ico.append(image) }
try ico.write(to: destination.appendingPathComponent("icon.ico"))
try files.removeItem(at: iconset)
print("Generated icon.png, icon.icns and icon.ico.")
