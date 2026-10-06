# -*- coding: utf-8 -*-
"""打包版(macOS / Windows)的启动入口。

源码运行仍然是 `python app.py`;这个文件只给 PyInstaller 打出来的安装包用:
  1. 数据放到用户目录(~/Movies/MV播放器、%USERPROFILE%\\Videos\\MV播放器),安装包本身只读
  2. 把随包附带的 yt-dlp / ffmpeg / ffprobe / deno 放进 PATH
  3. yt-dlp 复制一份到数据目录,每天自动更新(YouTube 改版频繁,旧版很快会失效)
  4. Windows 版启动后自动打开浏览器;macOS 版由原生窗口外壳负责显示

用法: MVPlayer [--data-dir 目录] [--port 端口] [--no-browser] [--smoke]
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

APP_DIR_NAME = "MV播放器"
IS_WIN = sys.platform.startswith("win")
EXE = ".exe" if IS_WIN else ""
UPDATE_INTERVAL = 24 * 3600


def install_dir():
    """安装目录:打包版是可执行文件所在目录,源码运行是本文件所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def default_data_dir():
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Movies", APP_DIR_NAME)
    if IS_WIN:
        return os.path.join(home, "Videos", APP_DIR_NAME)
    return os.path.join(home, APP_DIR_NAME)


def _version(exe):
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, timeout=30,
                             encoding="utf-8", errors="replace")
        return out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else ""
    except Exception:
        return ""


def prepare_tools(data_dir):
    """把附带工具放进 PATH;yt-dlp 用数据目录里那份(可自更新)。

    只在缺失时同步复制(很快);版本比较和更新放到后台,不拖慢启动。
    """
    bundled = os.path.join(install_dir(), "bin")
    if os.path.isdir(bundled):
        os.environ["PATH"] = bundled + os.pathsep + os.environ.get("PATH", "")
    src = os.path.join(bundled, "yt-dlp" + EXE)
    if not os.path.isfile(src):
        return None
    tools = os.path.join(data_dir, "tools")
    os.makedirs(tools, exist_ok=True)
    dst = os.path.join(tools, "yt-dlp" + EXE)
    if not os.path.isfile(dst):
        _install(src, dst)
    os.environ["MV_YTDLP"] = dst
    return dst


def _install(src, dst):
    # copyfile 不带扩展属性:避免把安装包的「隔离」标记带过去,导致 macOS 拦截运行
    shutil.copyfile(src, dst + ".tmp")
    if not IS_WIN:
        os.chmod(dst + ".tmp", 0o755)
    os.replace(dst + ".tmp", dst)


def update_ytdlp(ytdlp, data_dir):
    """后台:附带的版本更新就覆盖过去;再每天最多一次 `yt-dlp -U`(失败无所谓,下次再试)。"""
    src = os.path.join(install_dir(), "bin", os.path.basename(ytdlp))
    try:
        # 版本号是 YYYY.MM.DD,按字符串比较即可
        if os.path.isfile(src) and _version(ytdlp) < _version(src):
            _install(src, ytdlp)
    except OSError:
        pass
    stamp = os.path.join(data_dir, "tools", ".last-update")
    try:
        if time.time() - os.path.getmtime(stamp) < UPDATE_INTERVAL:
            return
    except OSError:
        pass
    try:
        subprocess.run([ytdlp, "-U"], capture_output=True, timeout=300)
        with open(stamp, "w") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if port_open(port):
            return True
        time.sleep(0.2)
    return False


def smoke_test():
    """构建机上的自检:服务能起来、页面和接口能访问、附带工具都能运行。"""
    import app  # noqa: 环境变量设好之后再导入

    ok = True
    for tool in ("yt-dlp", "ffmpeg", "ffprobe", "deno"):
        exe = (os.environ.get("MV_YTDLP") if tool == "yt-dlp" else None) or shutil.which(tool)
        version = _version(exe) if exe else ""
        if tool in ("ffmpeg", "ffprobe") and exe and not version:
            out = subprocess.run([exe, "-version"], capture_output=True, encoding="utf-8", errors="replace")
            version = out.stdout.splitlines()[0] if out.returncode == 0 and out.stdout else ""
        print("%-8s %s" % (tool, version or "缺失!"))
        ok = ok and bool(version)
    threading.Thread(target=app.main, daemon=True).start()
    if not wait_port(app.PORT):
        print("服务没有起来")
        return 1
    for path in ("/", "/api/state", "/static/app.js"):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (app.PORT, path), timeout=10) as r:
            print("GET %-16s %d" % (path, r.status))
            ok = ok and r.status == 200
    print("自检通过" if ok else "自检失败")
    return 0 if ok else 1


def setup_console():
    """Windows 控制台/管道默认不是 UTF-8,打印中文会崩;统一成 UTF-8,并设窗口标题。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW("MV 播放器")
        except Exception:
            pass


def main():
    setup_console()
    ap = argparse.ArgumentParser(prog="MVPlayer")
    ap.add_argument("--data-dir", default=os.environ.get("MV_DATA_DIR") or default_data_dir())
    ap.add_argument("--port", type=int, default=int(os.environ.get("MV_PORT") or 8471))
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器(macOS 外壳用)")
    ap.add_argument("--smoke", action="store_true", help="自检后退出(构建机用)")
    args = ap.parse_args()

    if args.smoke:
        args.port = free_port()
    data_dir = os.path.abspath(args.data_dir)
    os.makedirs(data_dir, exist_ok=True)
    os.environ["MV_DATA_DIR"] = data_dir
    os.environ["MV_PORT"] = str(args.port)
    os.environ.setdefault("PYTHONUTF8", "1")
    settings = os.path.join(data_dir, "download-settings.json")
    if not os.path.exists(settings):
        # 新安装默认不读浏览器登录状态(免得一上来就弹钥匙串授权);需要时在「下载设置」里选
        with open(settings, "w", encoding="utf-8") as f:
            f.write('{"browser": "none", "profile": "", "allow_variant": true}')
    ytdlp = prepare_tools(data_dir)
    if args.smoke:
        sys.exit(smoke_test())

    url = "http://127.0.0.1:%d/" % args.port
    if port_open(args.port):
        # 已经有一个在运行:Windows 直接打开浏览器即可,不再起第二个服务
        print("MV 播放器已经在运行:", url)
        if not args.no_browser:
            webbrowser.open(url)
        return
    if ytdlp:
        threading.Thread(target=update_ytdlp, args=(ytdlp, data_dir), daemon=True).start()
    if not args.no_browser:
        threading.Thread(target=lambda: wait_port(args.port) and webbrowser.open(url), daemon=True).start()

    print("=" * 56)
    print("  MV 播放器正在运行")
    print("  浏览器访问:", url)
    print("  曲库与视频保存在:", data_dir)
    if IS_WIN:
        print("  关闭这个窗口即退出(下载中的歌下次启动会继续)")
    print("=" * 56)
    import app  # noqa: 环境变量设好之后再导入(app 导入时就会读取数据目录)
    app.main()


if __name__ == "__main__":
    main()
