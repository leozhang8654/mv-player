# -*- coding: utf-8 -*-
"""程序文件与数据文件的位置。

- 源码运行:两者都在项目目录(与以前完全一样)。
- 打包版:程序在只读的安装包里,数据(曲库、视频、设置)由启动器通过环境变量
  MV_DATA_DIR 指到用户目录,如 ~/Movies/MV播放器 或 %USERPROFILE%\\Videos\\MV播放器。
"""
import os

CODE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.environ.get("MV_DATA_DIR") or CODE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
