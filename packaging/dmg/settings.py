# -*- coding: utf-8 -*-
# dmgbuild 配置(由 packaging/build_macos.sh 调用):
#   dmgbuild -s packaging/dmg/settings.py -D app=<.app 路径> -D readme=<说明文件> "MV 播放器" out.dmg
# 图标坐标与 make_background.py 里的背景设计一致。
import os

app = defines["app"]            # noqa: F821  (dmgbuild 注入)
readme = defines["readme"]      # noqa: F821

format = "UDZO"
filesystem = "HFS+"
files = [app, readme]
symlinks = {"应用程序": "/Applications"}
icon_locations = {
    os.path.basename(app): (170, 190),
    "应用程序": (490, 190),
    os.path.basename(readme): (330, 392),
}
background = defines["background"]   # noqa: F821  background.png(同目录的 @2x 自动用于高分屏)
window_rect = ((200, 140), (660, 480))
default_view = "icon-view"
icon_size = 96
text_size = 13
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
show_icon_preview = False
include_icon_view_settings = True
