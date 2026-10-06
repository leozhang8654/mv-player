// 生成 App 图标:紫粉渐变圆角方 + 白色播放三角,输出成 .iconset
// 用法: makeicon <输出目录.iconset>

import Cocoa

let sizes: [(px: Int, name: String)] = [
    (16, "icon_16x16"), (32, "icon_16x16@2x"),
    (32, "icon_32x32"), (64, "icon_32x32@2x"),
    (128, "icon_128x128"), (256, "icon_128x128@2x"),
    (256, "icon_256x256"), (512, "icon_256x256@2x"),
    (512, "icon_512x512"), (1024, "icon_512x512@2x"),
]

func srgb(_ hex: UInt32) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 0xff) / 255,
            green: CGFloat((hex >> 8) & 0xff) / 255,
            blue: CGFloat(hex & 0xff) / 255, alpha: 1)
}

func drawIcon(side: CGFloat) {
    // macOS 图标惯例:圆角方占画布约 80%,四周留投影余地
    let inset = side * 0.0879
    let box = NSRect(x: inset, y: inset, width: side - inset * 2, height: side - inset * 2)
    let radius = box.width * 0.2237

    let ctx = NSGraphicsContext.current!.cgContext
    ctx.saveGState()
    ctx.setShadow(offset: CGSize(width: 0, height: -side * 0.012),
                  blur: side * 0.028,
                  color: NSColor(white: 0, alpha: 0.35).cgColor)
    let plate = NSBezierPath(roundedRect: box, xRadius: radius, yRadius: radius)
    NSColor.black.setFill()
    plate.fill()
    ctx.restoreGState()

    // 底色:左上紫 → 右下粉
    ctx.saveGState()
    plate.addClip()
    NSGradient(colors: [srgb(0x7B3FE4), srgb(0xB43CD6), srgb(0xF0407F)],
               atLocations: [0, 0.52, 1], colorSpace: .sRGB)!
        .draw(in: box, angle: -55)

    // 顶部一层微光,避免死板
    NSGradient(starting: NSColor(white: 1, alpha: 0.22),
               ending: NSColor(white: 1, alpha: 0))!
        .draw(in: NSRect(x: box.minX, y: box.midY, width: box.width, height: box.height / 2), angle: -90)
    ctx.restoreGState()

    // 播放三角:圆角、微微偏右使视觉居中
    let r = box.width * 0.20
    let cx = box.midX + box.width * 0.022
    let cy = box.midY
    let pts = (0..<3).map { i -> NSPoint in
        let a = CGFloat(i) * (.pi * 2 / 3)
        return NSPoint(x: cx + r * cos(a), y: cy + r * sin(a))
    }
    let tri = NSBezierPath()
    tri.move(to: pts[0])
    tri.line(to: pts[1])
    tri.line(to: pts[2])
    tri.close()
    tri.lineJoinStyle = .round
    tri.lineWidth = box.width * 0.085          // 描边 + 填充 = 圆角三角
    NSColor.white.setFill()
    NSColor.white.setStroke()
    tri.fill()
    tri.stroke()
}

// MARK: - 输出

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "AppIcon.iconset"
try? FileManager.default.createDirectory(atPath: out, withIntermediateDirectories: true)

for (px, name) in sizes {
    guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px,
                                     bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                     isPlanar: false, colorSpaceName: .deviceRGB,
                                     bytesPerRow: 0, bitsPerPixel: 0) else { continue }
    rep.size = NSSize(width: px, height: px)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    NSGraphicsContext.current?.imageInterpolation = .high
    drawIcon(side: CGFloat(px))
    NSGraphicsContext.restoreGraphicsState()
    if let png = rep.representation(using: .png, properties: [:]) {
        try? png.write(to: URL(fileURLWithPath: "\(out)/\(name).png"))
    }
}
print("图标已生成:\(out)")
