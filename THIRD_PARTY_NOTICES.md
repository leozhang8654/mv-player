# 第三方组件说明

MV 播放器的安装包附带以下第三方程序与库,它们各自遵循原有的许可证。
MV 播放器只是在本机调用它们,不修改其源码。

| 组件 | 用途 | 许可证 | 源码 |
|---|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 搜索与下载视频 | The Unlicense | https://github.com/yt-dlp/yt-dlp |
| [FFmpeg](https://ffmpeg.org) | 合并音视频、截取封面、响度与画面检测 | GPL v3(所附为 GPL 构建) | https://ffmpeg.org/download.html |
| [Deno](https://deno.com) | yt-dlp 解析 YouTube 所需的 JavaScript 运行时 | MIT | https://github.com/denoland/deno |
| [Python](https://www.python.org) | 运行后台服务 | PSF License | https://www.python.org/downloads/source/ |
| [Flask](https://flask.palletsprojects.com) / Werkzeug | 本地网页服务 | BSD-3-Clause | https://github.com/pallets/flask |
| [OpenCC](https://github.com/yichen0831/opencc-python) | 繁简转换(歌名匹配) | Apache-2.0 | https://github.com/yichen0831/opencc-python |

FFmpeg 构建来源:

- macOS:<https://ffmpeg.martin-riedl.de>(静态构建,附带构建脚本与对应源码链接)
- Windows:<https://github.com/yt-dlp/FFmpeg-Builds>(GPL 构建,源码与构建脚本见该仓库)

根据 GPL,如需所附 FFmpeg 二进制对应的完整源码,可从上述构建来源获取,
或在本项目 Issues 中提出,我们会提供获取方式。
