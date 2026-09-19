"""AI游戏搭子 - 桌面悬浮窗（P4）

用 pywebview（系统 Edge WebView2）把网页版包成一个小巧的置顶悬浮窗：
- 始终置顶，可拖到屏幕任意位置，不遮挡全屏游戏（游戏用无边框窗口模式时浮于其上）
- 启动前自动探测后端服务，未启动则给出提示
- 关闭窗口不影响后端服务（下次再开即恢复）

用法：.venv/Scripts/python.exe game_companion/pet_window.py [--url URL] [--width W] [--height H]
"""

import argparse
import sys
import urllib.request

import webview

DEFAULT_URL = "http://localhost:12393"


def server_alive(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - 任何异常都视为未启动
        return False


def main():
    parser = argparse.ArgumentParser(description="AI Game Companion Pet Window")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--width", type=int, default=420)
    parser.add_argument("--height", type=int, default=640)
    args = parser.parse_args()

    if not server_alive(args.url):
        print(f"[错误] 后端服务未运行或无法访问: {args.url}")
        print("请先双击 启动游戏搭子.bat 启动后端，再打开悬浮窗。")
        input("按回车退出...")
        sys.exit(1)

    window = webview.create_window(
        "AI游戏搭子",
        args.url,
        width=args.width,
        height=args.height,
        on_top=True,          # 始终置顶
        resizable=True,
        min_size=(320, 480),
        confirm_close=False,
    )
    # Edge WebView2 内核，Windows 自带无需安装
    webview.start(gui="edgechromium", debug=False)


if __name__ == "__main__":
    main()
