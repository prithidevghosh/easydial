// Reproducible vector-drawn app icon. Run from ios/: swift tools/generate-icon.swift
import AppKit

let size = NSSize(width: 1024, height: 1024)
let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 1024, pixelsHigh: 1024,
                             bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                             isPlanar: false, colorSpaceName: .deviceRGB,
                             bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
let background = NSColor(srgbRed: 0.075, green: 0.227, blue: 0.447, alpha: 1)
background.setFill()
NSBezierPath(rect: NSRect(origin: .zero, size: size)).fill()
NSColor(srgbRed: 0.55, green: 0.75, blue: 1, alpha: 0.12).setFill()
NSBezierPath(ovalIn: NSRect(x: 132, y: 132, width: 760, height: 760)).fill()
NSColor.white.setFill()
NSBezierPath(roundedRect: NSRect(x: 427, y: 400, width: 170, height: 320), xRadius: 85, yRadius: 85).fill()
let arc = NSBezierPath()
arc.lineWidth = 38
arc.lineCapStyle = .round
arc.move(to: NSPoint(x: 332, y: 485))
arc.line(to: NSPoint(x: 332, y: 440))
arc.curve(to: NSPoint(x: 692, y: 440), controlPoint1: NSPoint(x: 332, y: 202), controlPoint2: NSPoint(x: 692, y: 202))
arc.line(to: NSPoint(x: 692, y: 485))
NSColor.white.setStroke()
arc.stroke()
let stem = NSBezierPath()
stem.lineWidth = 38
stem.lineCapStyle = .round
stem.move(to: NSPoint(x: 512, y: 270))
stem.line(to: NSPoint(x: 512, y: 210))
stem.move(to: NSPoint(x: 427, y: 210))
stem.line(to: NSPoint(x: 597, y: 210))
stem.stroke()
NSGraphicsContext.restoreGraphicsState()
let png = bitmap.representation(using: .png, properties: [:])!
try png.write(to: URL(fileURLWithPath: "EasyDial/Assets.xcassets/AppIcon.appiconset/AppIcon.png"))
