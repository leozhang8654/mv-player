// 菜单栏与应用生命周期

import Cocoa

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var main: MainWindowController!

    func applicationDidFinishLaunching(_ note: Notification) {
        NSApp.setActivationPolicy(.regular)
        buildMenu()
        main = MainWindowController()
        main.showWindow(nil)
        main.window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        main.boot()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ note: Notification) {
        // launchd 常驻的服务不动;只收拾自己拉起来的那个
        main?.server.stopIfOurs()
    }

    // MARK: - 菜单

    private func buildMenu() {
        let bar = NSMenu()

        // 应用
        let appItem = NSMenuItem()
        bar.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于 MV 播放器",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "重启后台服务", action: #selector(restartServer), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "隐藏 MV 播放器",
                        action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = appMenu.addItem(withTitle: "隐藏其他",
                                         action: #selector(NSApplication.hideOtherApplications(_:)),
                                         keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "显示全部",
                        action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出 MV 播放器",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        // 编辑(⌘V 粘贴歌单全靠它)
        let editItem = NSMenuItem()
        bar.addItem(editItem)
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        let redo = editMenu.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "z")
        redo.keyEquivalentModifierMask = [.command, .shift]
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu

        // 播放(复用网页上的按钮)
        let playItem = NSMenuItem()
        bar.addItem(playItem)
        let playMenu = NSMenu(title: "播放")
        playMenu.addItem(withTitle: "播放/暂停", action: #selector(togglePlay), keyEquivalent: "p")
        let leftKey = String(utf16CodeUnits: [unichar(NSLeftArrowFunctionKey)], count: 1)
        let rightKey = String(utf16CodeUnits: [unichar(NSRightArrowFunctionKey)], count: 1)
        let prev = playMenu.addItem(withTitle: "上一首", action: #selector(prevSong), keyEquivalent: leftKey)
        prev.keyEquivalentModifierMask = [.command]
        let next = playMenu.addItem(withTitle: "下一首", action: #selector(nextSong), keyEquivalent: rightKey)
        next.keyEquivalentModifierMask = [.command]
        playMenu.addItem(.separator())
        playMenu.addItem(withTitle: "随机播放", action: #selector(toggleShuffle), keyEquivalent: "")
        playMenu.addItem(withTitle: "单曲循环", action: #selector(toggleLoop), keyEquivalent: "")
        playMenu.addItem(.separator())
        playMenu.addItem(withTitle: "视频全屏", action: #selector(videoFullscreen), keyEquivalent: "f")
        playItem.submenu = playMenu

        // 显示
        let viewItem = NSMenuItem()
        bar.addItem(viewItem)
        let viewMenu = NSMenu(title: "显示")
        viewMenu.addItem(withTitle: "重新载入", action: #selector(reload), keyEquivalent: "r")
        viewMenu.addItem(.separator())
        viewMenu.addItem(withTitle: "实际大小", action: #selector(zoomReset), keyEquivalent: "0")
        viewMenu.addItem(withTitle: "放大", action: #selector(zoomIn), keyEquivalent: "+")
        let zoomAlt = viewMenu.addItem(withTitle: "放大", action: #selector(zoomIn), keyEquivalent: "=")
        zoomAlt.isHidden = true
        viewMenu.addItem(withTitle: "缩小", action: #selector(zoomOut), keyEquivalent: "-")
        viewMenu.addItem(.separator())
        let fs = viewMenu.addItem(withTitle: "进入全屏幕",
                                  action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fs.keyEquivalentModifierMask = [.command, .control]
        viewItem.submenu = viewMenu

        // 窗口
        let winItem = NSMenuItem()
        bar.addItem(winItem)
        let winMenu = NSMenu(title: "窗口")
        winMenu.addItem(withTitle: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        winMenu.addItem(withTitle: "缩放", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        winMenu.addItem(.separator())
        winMenu.addItem(withTitle: "关闭", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        winItem.submenu = winMenu

        // 帮助
        let helpItem = NSMenuItem()
        bar.addItem(helpItem)
        let helpMenu = NSMenu(title: "帮助")
        let openBrowser = helpMenu.addItem(withTitle: "在浏览器中打开", action: #selector(openInBrowser), keyEquivalent: "o")
        openBrowser.keyEquivalentModifierMask = [.command, .shift]
        helpMenu.addItem(withTitle: "拷贝手机访问地址", action: #selector(copyLANAddress), keyEquivalent: "")
        helpMenu.addItem(.separator())
        helpMenu.addItem(withTitle: Cfg.isBundled ? "打开曲库文件夹" : "打开项目文件夹",
                         action: #selector(openProjectFolder), keyEquivalent: "")
        helpMenu.addItem(withTitle: "查看服务日志", action: #selector(openLog), keyEquivalent: "")
        helpItem.submenu = helpMenu

        NSApp.mainMenu = bar
        NSApp.windowsMenu = winMenu
        NSApp.helpMenu = helpMenu
    }

    // MARK: - 菜单动作

    @objc private func togglePlay()       { main.click("play-btn") }
    @objc private func nextSong()         { main.click("next-btn") }
    @objc private func prevSong()         { main.click("prev-btn") }
    @objc private func toggleShuffle()    { main.click("shuffle-btn") }
    @objc private func toggleLoop()       { main.click("loop-btn") }
    @objc private func videoFullscreen()  { main.click("fs-btn") }
    @objc private func reload()           { main.reloadPage() }
    @objc private func zoomReset()        { main.setZoom(1.0) }
    @objc private func zoomIn()           { main.setZoom(main.zoom + 0.1) }
    @objc private func zoomOut()          { main.setZoom(main.zoom - 0.1) }

    @objc private func openInBrowser() { NSWorkspace.shared.open(Cfg.home) }

    @objc private func openProjectFolder() {
        NSWorkspace.shared.open(URL(fileURLWithPath: Cfg.isBundled ? Cfg.dataDir : Cfg.projectRoot))
    }

    @objc private func openLog() {
        let path = FileManager.default.fileExists(atPath: Cfg.logPath)
            ? Cfg.logPath : NSHomeDirectory() + "/Library/Logs/mv-player.log"
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }

    @objc private func copyLANAddress() {
        let a = NSAlert()
        if let ip = lanIP() {
            let addr = "http://\(ip):\(Cfg.port)"
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(addr, forType: .string)
            a.messageText = "地址已拷贝"
            a.informativeText = "\(addr)\n\n同一 Wi-Fi 下的手机 / iPad 在浏览器里打开就能放。"
        } else {
            a.messageText = "拿不到局域网地址"
            a.informativeText = "这台 Mac 好像没连 Wi-Fi 或有线网络。"
        }
        a.addButton(withTitle: "好")
        a.runModal()
    }

    @objc private func restartServer() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            try? self.main.server.restart()
            DispatchQueue.main.async { self.main.reloadPage() }
        }
    }
}
