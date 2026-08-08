"""把实拍原图压成网页可用的规格，就地替换，原图留一份到 _source/。

换图流程是：把原图丢进 antony-casa/assets/<组>/，跑这个脚本，再跑 upload_static.py。

为什么需要它：相机和手机直出的图动辄 5-27MB、宽 4000-5700px，直接上传的话首屏
要加载几十兆。而每次手动敲 ffmpeg 很容易漏掉某张、或者两次用的参数不一样。

**幂等**：只处理宽度超过上限的文件。已经压过的图宽度必然不超标，重复跑会跳过，
不会二次压缩——JPEG 每压一次都掉一次质量，反复跑不能把图跑烂。

用法（在 server 目录下）：
    uv run python scripts/optimize_assets.py --dry-run   # 只看会处理哪些
    uv run python scripts/optimize_assets.py             # 实际处理

依赖本机的 ffmpeg / ffprobe（活动海报和上一批实拍也是用它压的）。
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ASSETS_ROOT = REPO_ROOT / "antony-casa" / "assets"


def _pick_ffmpeg() -> tuple[str, str, bool]:
    """挑一套可用的 ffmpeg/ffprobe，并说明要不要转路径。

    这个项目是 Windows + WSL 混着用的（后端 venv 在 WSL 里，ffmpeg 装在 Windows 侧，
    见 server/deploy/README.md 里同类的环境约束）。WSL 里没有原生 ffmpeg 时回落到
    Windows 的 ffmpeg.exe——但它认不得 /mnt/d/... 这样的路径，得用 wslpath 转成 D:\\...
    """
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return "ffmpeg", "ffprobe", False
    if shutil.which("ffmpeg.exe") and shutil.which("ffprobe.exe"):
        return "ffmpeg.exe", "ffprobe.exe", True
    raise SystemExit("找不到 ffmpeg / ffprobe，装一个再跑")


FFMPEG, FFPROBE, NEEDS_WSLPATH = _pick_ffmpeg()


def native(path: Path) -> str:
    """交给 ffmpeg 的路径。走 Windows 的 exe 时要转成 Windows 形式。"""
    if not NEEDS_WSLPATH:
        return str(path)
    out = subprocess.run(
        ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()

# 每组图的最大宽度。按它在页面上实际占多大定，不是越大越好：
#   hero  首屏整屏铺，桌面视口按 1920 算，1:1 正好
#   space 展厅实拍是竖图，1440 宽对应 1920 高，手机全宽时也够锐利
#   其余  服务页配图之类，1600 封顶
#
# 这几个值同时是「不再处理」的判定线，所以要和现有素材的实际规格对齐：
# 定得比现有图还小，每跑一次就会把它们再压一遍，白掉质量。
MAX_WIDTH: dict[str, int] = {"hero-": 1920, "space-": 1440}
DEFAULT_MAX_WIDTH = 1600

# ffmpeg 的 -qscale:v，2-31 越小越好。4 在实拍照片上肉眼看不出损失
QUALITY = 4

SOURCE_DIR_NAME = "_source"
SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def limit_for(name: str) -> int:
    for prefix, width in MAX_WIDTH.items():
        if name.startswith(prefix):
            return width
    return DEFAULT_MAX_WIDTH


def probe_width(path: Path) -> int:
    """读图片宽度。用 ffprobe 而不是猜文件大小：体积小也可能是张宽而糊的图。"""
    out = subprocess.run(
        [
            FFPROBE, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width",
            "-of", "csv=p=0",
            native(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip().split(",")[0])


def compress(src: Path, dest: Path, max_width: int) -> None:
    """缩到 max_width 并转成 jpg。

    scale 用 min(w,iw) 而不是死写宽度：原图本来就比上限窄时放大只会更糊、还更占体积。
    -2 保证高度是偶数，否则 mjpeg 编码器会拒绝。
    """
    subprocess.run(
        [
            FFMPEG, "-loglevel", "error", "-y",
            "-i", native(src),
            "-vf", f"scale='min({max_width},iw)':-2",
            # PNG 可能带 alpha，JPEG 不支持，统一转成 yuvj420p
            "-pix_fmt", "yuvj420p",
            "-qscale:v", str(QUALITY),
            native(dest),
        ],
        check=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="压缩实拍原图到网页规格")
    parser.add_argument("--dry-run", action="store_true", help="只列出会处理的文件")
    args = parser.parse_args()

    targets: list[tuple[Path, int, int]] = []
    for group_dir in sorted(p for p in ASSETS_ROOT.iterdir() if p.is_dir()):
        for path in sorted(group_dir.iterdir()):
            if path.is_dir() or path.suffix.lower() not in SUFFIXES:
                continue
            max_width = limit_for(path.name)
            width = probe_width(path)
            if width > max_width:
                targets.append((path, width, max_width))

    if not targets:
        print("没有超标的图片，全部已经是网页规格")
        return

    print(f"需要处理 {len(targets)} 张：\n")
    for path, width, max_width in targets:
        size_mb = path.stat().st_size / 1024 / 1024
        print(f"  {path.parent.name}/{path.name:<16} {width}px {size_mb:6.1f} MB  → {max_width}px")

    if args.dry_run:
        print("\n--dry-run，没有实际改动")
        return

    print()
    for path, _, max_width in targets:
        # 原图先挪进 _source/ 再压：ffmpeg 不能原地读写同一个文件。
        # 这份原图也是唯一的重压来源，_source/ 已在 .gitignore 里，不进版本库
        source_dir = path.parent / SOURCE_DIR_NAME
        source_dir.mkdir(exist_ok=True)
        original = source_dir / path.name
        shutil.move(str(path), original)

        # 统一出 jpg：png 的实拍照片体积是 jpg 的好几倍，而且用不上无损和透明
        dest = path.with_suffix(".jpg")
        compress(original, dest, max_width)

        before = original.stat().st_size / 1024
        after = dest.stat().st_size / 1024
        print(f"  ✓ {dest.parent.name}/{dest.name:<16} {before:8.0f} KB → {after:6.0f} KB")

        # 原来是 png 的，压完是 jpg，同名的 png 已经被移走了，这里不用再删
        if path.suffix.lower() != ".jpg":
            print(f"    （{path.name} 已转成 {dest.name}，记得代码里的引用也用 .jpg）")

    print(f"\n原图留在各组的 {SOURCE_DIR_NAME}/ 下（不进版本库）。接着跑 upload_static.py 上传")


if __name__ == "__main__":
    sys.exit(main())
