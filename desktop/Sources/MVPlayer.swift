// MV 播放器 —— macOS 原生外壳
//
// 真正干活的还是 Flask 服务(app.py),这层只负责把它包成一个 App:
//   1. 双击启动时确保本地服务在跑(已由 launchd 常驻则直接复用,不重复起)。
//      两种模式:开发版指向项目文件夹(Info.plist 的 MVProjectRoot);
//      发布版的后端随 App 附带(Contents/Resources/backend),数据放 ~/Movies/MV播放器
//   2. 用 WKWebView 开原生窗口显示界面,而不是丢进浏览器标签页
//   3. 补回浏览器白送的东西:菜单栏、⌘V 粘贴、全屏、缩放、窗口位置记忆

import Cocoa
import WebKit
import Darwin

// MARK: - 配置

enum Cfg {
    /// 端口:默认 8471,可用环境变量 MV_PORT 改(测试时避免和已在运行的服务冲突)
    static let port: UInt16 = UInt16(ProcessInfo.processInfo.environment["MV_PORT"] ?? "") ?? 8471
    static var home: URL { URL(string: "http://127.0.0.1:\(port)/")! }
    static let bg = NSColor(srgbRed: 0x0e / 255.0, green: 0x0f / 255.0, blue: 0x13 / 255.0, alpha: 1)
    static let launchdLabel = "com.leozhang.mv-player"
    static var logPath: String {
        NSHomeDirectory() + (isBundled ? "/Library/Logs/MV播放器.log" : "/Library/Logs/mv-player-app.log")
    }

    /// 发布版附带的后端可执行文件;开发版没有,返回 nil
    static var bundledServer: String? {
        guard let res = Bundle.main.resourcePath else { return nil }
        let p = res + "/backend/MVPlayerServer"
        return FileManager.default.isExecutableFile(atPath: p) ? p : nil
    }
    static var isBundled: Bool { bundledServer != nil }

    /// 发布版的曲库/视频目录(可用 MV_DATA_DIR 覆盖)
    static var dataDir: String {
        ProcessInfo.processInfo.environment["MV_DATA_DIR"] ?? NSHomeDirectory() + "/Movies/MV播放器"
    }

    /// 项目目录:打包时写进 Info.plist 的 MVProjectRoot,兜底 ~/MV播放器
    static var projectRoot: String {
        var candidates: [String] = []
        if let p = Bundle.main.object(forInfoDictionaryKey: "MVProjectRoot") as? String, !p.isEmpty {
            candidates.append((p as NSString).expandingTildeInPath)
        }
        candidates.append(NSHomeDirectory() + "/MV播放器")
        for p in candidates where FileManager.default.fileExists(atPath: p + "/app.py") { return p }
        return candidates[0]
    }
}

// MARK: - 小工具

/// 端口通不通(本地回环,连上就算服务活着)
func portIsOpen(_ port: UInt16, timeout: TimeInterval = 0.4) -> Bool {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    if fd < 0 { return false }
    defer { close(fd) }
    var tv = timeval(tv_sec: Int(timeout),
                     tv_usec: Int32((timeout - floor(timeout)) * 1_000_000))
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
    var addr = sockaddr_in()
    addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = port.bigEndian
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    let rc = withUnsafePointer(to: &addr) { ptr in
        ptr.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            connect(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
        }
    }
    return rc == 0
}

/// 本机在局域网里的 IPv4(给手机/iPad 访问用)
func lanIP() -> String? {
    var head: UnsafeMutablePointer<ifaddrs>?
    guard getifaddrs(&head) == 0, let first = head else { return nil }
    defer { freeifaddrs(head) }
    var best: String?
    for ifa in sequence(first: first, next: { $0.pointee.ifa_next }) {
        let flags = Int32(ifa.pointee.ifa_flags)
        guard flags & IFF_UP != 0, flags & IFF_LOOPBACK == 0,
              let sa = ifa.pointee.ifa_addr, sa.pointee.sa_family == UInt8(AF_INET) else { continue }
        var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
        guard getnameinfo(sa, socklen_t(sa.pointee.sa_len), &host, socklen_t(host.count),
                          nil, 0, NI_NUMERICHOST) == 0 else { continue }
        let ip = String(cString: host)
        let name = String(cString: ifa.pointee.ifa_name)
        if name == "en0" { return ip }          // Wi-Fi 优先
        if best == nil { best = ip }
    }
    return best
}

func which(_ tool: String) -> String? {
    for dir in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"] {
        let p = dir + "/" + tool
        if FileManager.default.isExecutableFile(atPath: p) { return p }
    }
    return nil
}

/// 文件是否带 macOS 的「来自互联网」隔离标记
func hasQuarantine(_ path: String) -> Bool {
    getxattr(path, "com.apple.quarantine", nil, 0, 0, 0) >= 0
}

/// 递归去掉隔离标记(App 在只读位置运行时会失败,调用方再用 hasQuarantine 检查)
func clearQuarantine(_ path: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/xattr")
    p.arguments = ["-dr", "com.apple.quarantine", path]
    p.standardOutput = FileHandle.nullDevice
    p.standardError = FileHandle.nullDevice
    try? p.run()
    p.waitUntilExit()
}

// MARK: - 后台服务

final class Server {
    /// 只有这个 App 亲手拉起来的进程才归它管;launchd 常驻的那个不碰
    private var child: Process?
    var isOurs: Bool { child?.isRunning == true }

    enum Failure: LocalizedError {
        case noProject(String), noPython(String), didNotStart(String), quarantined(String)
        var errorDescription: String? {
            switch self {
            case .quarantined(let app) where app.contains("/AppTranslocation/"):
                return "请先把「MV播放器」拖进「应用程序」文件夹,再从那里打开。\n"
                    + "(直接从下载文件夹或安装盘里运行时,macOS 不允许它启动附带的后台程序。)"
            case .quarantined(let app):
                return "macOS 拦截了附带的后台程序。请打开「终端」执行下面这行,然后重新打开:\n\n"
                    + "xattr -dr com.apple.quarantine \"\(app)\""
            case .noProject(let p):  return "找不到项目文件夹:\n\(p)/app.py 不存在。"
            case .noPython(let p):   return "找不到 Python:\n\(p) 不可执行,虚拟环境可能被删了。"
            case .didNotStart(let s): return "后台服务启动失败。\(s)"
            }
        }
    }

    /// 起服务(已经在跑就直接返回)。阻塞式,放后台线程调用。
    func ensureRunning() throws {
        if portIsOpen(Cfg.port) { return }

        let p = Process()
        if let server = Cfg.bundledServer {
            // 发布版:直接运行附带的后端,工具链(yt-dlp/ffmpeg/deno)也在包里。
            // 浏览器下载的 App 里每个文件都带「隔离」标记;用户点「仍要打开」同意的只是 App 本身,
            // 附带的后端是单独的程序,macOS 会再拦一次,而且是卡在启动处、不报错(界面一直打不开)。
            // 所以启动前先去掉包内后端的标记 —— 等同于 README 里让用户手动执行的 xattr 命令。
            let backend = (server as NSString).deletingLastPathComponent
            clearQuarantine(backend)
            if hasQuarantine(server) { throw Failure.quarantined(Bundle.main.bundlePath) }
            try? FileManager.default.createDirectory(atPath: Cfg.dataDir, withIntermediateDirectories: true)
            p.executableURL = URL(fileURLWithPath: server)
            p.arguments = ["--no-browser", "--data-dir", Cfg.dataDir, "--port", String(Cfg.port)]
            p.currentDirectoryURL = URL(fileURLWithPath: Cfg.dataDir)
            var env = ProcessInfo.processInfo.environment
            env["PYTHONUNBUFFERED"] = "1"
            p.environment = env
        } else {
            try configureDevServer(p)
        }
        try launch(p)
    }

    /// 开发版:用项目里的 .venv 跑 app.py
    private func configureDevServer(_ p: Process) throws {
        let root = Cfg.projectRoot
        guard FileManager.default.fileExists(atPath: root + "/app.py") else {
            throw Failure.noProject(root)
        }
        let venv = root + "/.venv/bin/python"
        let python = FileManager.default.isExecutableFile(atPath: venv) ? venv : "/usr/bin/python3"
        guard FileManager.default.isExecutableFile(atPath: python) else { throw Failure.noPython(venv) }

        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["app.py"]
        p.currentDirectoryURL = URL(fileURLWithPath: root)
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + (env["PATH"] ?? "/usr/bin:/bin")
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
    }

    private func launch(_ p: Process) throws {
        FileManager.default.createFile(atPath: Cfg.logPath, contents: nil)
        if let log = FileHandle(forWritingAtPath: Cfg.logPath) {
            log.seekToEndOfFile()
            p.standardOutput = log
            p.standardError = log
        }
        do { try p.run() } catch { throw Failure.didNotStart(error.localizedDescription) }
        child = p

        // 等端口起来:开发版最多 25 秒(首次可能要迁移 library.json);
        // 发布版首次运行 macOS 要校验附带的程序,给到 90 秒
        for _ in 0..<(Cfg.isBundled ? 450 : 125) {
            if portIsOpen(Cfg.port) { return }
            if !p.isRunning { break }
            Thread.sleep(forTimeInterval: 0.2)
        }
        let tail = (try? String(contentsOfFile: Cfg.logPath, encoding: .utf8))?
            .split(separator: "\n").suffix(6).joined(separator: "\n") ?? ""
        throw Failure.didNotStart(tail.isEmpty ? "端口 \(Cfg.port) 一直没起来。" : "\n\n日志末尾:\n" + tail)
    }

    func stopIfOurs() {
        guard let p = child, p.isRunning else { return }
        p.terminate()
        child = nil
    }

    /// 重启:自己拉起的直接换一个,launchd 托管的交给 launchctl
    func restart() throws {
        if isOurs {
            stopIfOurs()
            Thread.sleep(forTimeInterval: 0.6)
        } else if !Cfg.isBundled {
            let t = Process()
            t.executableURL = URL(fileURLWithPath: "/bin/launchctl")
            t.arguments = ["kickstart", "-k", "gui/\(getuid())/\(Cfg.launchdLabel)"]
            try? t.run()
            t.waitUntilExit()
            Thread.sleep(forTimeInterval: 1.0)
            for _ in 0..<50 where !portIsOpen(Cfg.port) { Thread.sleep(forTimeInterval: 0.2) }
        }
        try ensureRunning()
    }
}

// MARK: - 启动中 / 出错时的覆盖层

final class StatusView: NSView {
    private let spinner = NSProgressIndicator()
    private let titleLabel = NSTextField(labelWithString: "")
    private let detailLabel = NSTextField(labelWithString: "")
    private let button = NSButton(title: "", target: nil, action: nil)
    private var onClick: (() -> Void)?

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.backgroundColor = Cfg.bg.cgColor

        spinner.style = .spinning
        spinner.controlSize = .regular

        titleLabel.font = .systemFont(ofSize: 17, weight: .semibold)
        titleLabel.textColor = NSColor(white: 0.93, alpha: 1)
        titleLabel.alignment = .center

        detailLabel.font = .systemFont(ofSize: 12)
        detailLabel.textColor = NSColor(white: 0.62, alpha: 1)
        detailLabel.alignment = .center
        detailLabel.maximumNumberOfLines = 12
        detailLabel.lineBreakMode = .byWordWrapping
        detailLabel.preferredMaxLayoutWidth = 460
        detailLabel.isSelectable = true

        button.bezelStyle = .rounded
        button.target = self
        button.action = #selector(tapped)

        let stack = NSStackView(views: [spinner, titleLabel, detailLabel, button])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 14
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: centerYAnchor),
            stack.widthAnchor.constraint(lessThanOrEqualToConstant: 480),
        ])
    }

    required init?(coder: NSCoder) { fatalError() }

    @objc private func tapped() { onClick?() }

    func showLoading(_ text: String) {
        spinner.isHidden = false
        spinner.startAnimation(nil)
        titleLabel.stringValue = text
        detailLabel.isHidden = true
        button.isHidden = true
        isHidden = false
    }

    func showError(_ text: String, detail: String, action: String, handler: @escaping () -> Void) {
        spinner.stopAnimation(nil)
        spinner.isHidden = true
        titleLabel.stringValue = text
        detailLabel.stringValue = detail
        detailLabel.isHidden = detail.isEmpty
        button.title = action
        button.isHidden = false
        onClick = handler
        isHidden = false
    }
}

// MARK: - 主窗口

final class MainWindowController: NSWindowController, WKNavigationDelegate, WKUIDelegate {
    let server = Server()
    private(set) var webView: WKWebView!
    private var status: StatusView!
    private var loadedOnce = false

    convenience init() {
        let win = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820),
                           styleMask: [.titled, .closable, .miniaturizable, .resizable],
                           backing: .buffered, defer: false)
        win.title = "MV 播放器"
        win.minSize = NSSize(width: 760, height: 520)
        win.backgroundColor = Cfg.bg
        win.appearance = NSAppearance(named: .darkAqua)
        win.tabbingMode = .disallowed
        win.center()
        win.setFrameAutosaveName("MVPlayerMainWindow")
        self.init(window: win)
        setupContent()
    }

    private func setupContent() {
        let conf = WKWebViewConfiguration()
        conf.mediaTypesRequiringUserActionForPlayback = []          // 播完自动下一首不用点一下
        conf.suppressesIncrementalRendering = false
        if #available(macOS 12.3, *) {
            conf.preferences.isElementFullscreenEnabled = true      // 视频 F 键全屏
        } else {
            conf.preferences.setValue(true, forKey: "fullScreenEnabled")
        }
        conf.preferences.setValue(true, forKey: "developerExtrasEnabled")  // 右键「检查元素」

        webView = WKWebView(frame: .zero, configuration: conf)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        webView.allowsMagnification = true
        if #available(macOS 12.0, *) {
            webView.underPageBackgroundColor = Cfg.bg                // 滚动回弹时别闪白底
        }
        webView.translatesAutoresizingMaskIntoConstraints = false

        status = StatusView(frame: .zero)
        status.translatesAutoresizingMaskIntoConstraints = false

        let root = NSView()
        root.wantsLayer = true
        root.layer?.backgroundColor = Cfg.bg.cgColor
        root.addSubview(webView)
        root.addSubview(status)
        NSLayoutConstraint.activate([
            webView.topAnchor.constraint(equalTo: root.topAnchor),
            webView.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            webView.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            status.topAnchor.constraint(equalTo: root.topAnchor),
            status.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            status.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            status.trailingAnchor.constraint(equalTo: root.trailingAnchor),
        ])
        window?.contentView = root
    }

    // MARK: 启动流程

    func boot() {
        status.showLoading(portIsOpen(Cfg.port) ? "正在载入…" : "正在启动 MV 播放器…")
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            do {
                try self.server.ensureRunning()
                DispatchQueue.main.async { self.webView.load(URLRequest(url: Cfg.home)) }
            } catch {
                DispatchQueue.main.async { self.showBootError(error) }
            }
        }
    }

    private func showBootError(_ error: Error) {
        var detail = error.localizedDescription
        if Cfg.isBundled {
            detail += "\n\n日志:\(Cfg.logPath)"
            status.showError("没能启动后台服务", detail: detail, action: "重试") { [weak self] in
                self?.boot()
            }
            return
        }
        var missing: [String] = []
        if which("yt-dlp") == nil { missing.append("yt-dlp") }
        if which("ffmpeg") == nil { missing.append("ffmpeg") }
        if !missing.isEmpty {
            detail += "\n\n另外没找到:\(missing.joined(separator: "、"))\n用 brew install \(missing.joined(separator: " ")) 装上。"
        }
        status.showError("没能启动后台服务", detail: detail, action: "重试") { [weak self] in
            self?.boot()
        }
    }

    @objc func reloadPage() {
        if loadedOnce { webView.reload() } else { boot() }
    }

    /// 让网页自己的按钮干活(菜单项复用页面逻辑,不另起一套)
    func click(_ elementID: String) {
        guard loadedOnce else { return }
        webView.evaluateJavaScript("document.getElementById('\(elementID)')?.click()")
    }

    func setZoom(_ z: CGFloat) {
        webView.pageZoom = min(max(z, 0.5), 2.5)
    }

    var zoom: CGFloat { webView.pageZoom }

    // MARK: WKNavigationDelegate

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loadedOnce = true
        status.isHidden = true
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        guard !loadedOnce else { return }
        status.showError("页面没打开", detail: error.localizedDescription, action: "重试") { [weak self] in
            self?.boot()
        }
    }

    /// 站外链接交给默认浏览器,App 里只留本地页面
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = navigationAction.request.url,
           let host = url.host, host != "127.0.0.1", host != "localhost",
           url.scheme?.hasPrefix("http") == true {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    // MARK: WKUIDelegate —— 网页里的 alert / confirm / prompt

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let a = NSAlert(); a.messageText = message; a.addButton(withTitle: "好")
        a.beginSheetModal(for: window!) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let a = NSAlert(); a.messageText = message
        a.addButton(withTitle: "好"); a.addButton(withTitle: "取消")
        a.beginSheetModal(for: window!) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        let a = NSAlert(); a.messageText = prompt
        a.addButton(withTitle: "好"); a.addButton(withTitle: "取消")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        field.stringValue = defaultText ?? ""
        a.accessoryView = field
        a.beginSheetModal(for: window!) { completionHandler($0 == .alertFirstButtonReturn ? field.stringValue : nil) }
    }
}
