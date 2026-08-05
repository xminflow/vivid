"""把小程序的静态素材推到 COS。

小程序主包有 2MB 上限，品牌实拍图占了 1.2MB，进包不划算：这些图不参与业务逻辑，
换图也不该走发版。传到 COS 后小程序用固定 URL 引用，图还留在仓库里做版本追溯，
靠 project.config.json 的 packOptions.ignore 排除出包。

与用户上传的区别：用户图走 uploads/ 前缀、键由服务端随机生成、地址现签；
这里的静态素材走 static/ 前缀、路径稳定可预测、直接公开访问。两者只是同一个桶
里的两个前缀，将来桶权限收紧成私有读时，static/ 下的对象要单独设公有读 ACL。

用法（在 server 目录下）：
    uv run python scripts/upload_static.py            # 传 + 校验
    uv run python scripts/upload_static.py --check    # 只校验线上是否可访问

可重复执行：同名对象直接覆盖。
"""

import argparse
import sys
from pathlib import Path

import httpx

# 脚本在 server/scripts 下，仓库根是上两级
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "server"))

from app import cos  # noqa: E402

# 本地目录 -> COS 前缀。只搬品牌素材，tabBar 图标微信强制本地文件，搬不了。
# 加新的一组图就在这里加一行，目录不存在会跳过（素材还没到位时不该让脚本失败）
ASSETS_ROOT = REPO_ROOT / "antony-casa" / "assets"
GROUPS = {
    "home": "static/home",
    "service": "static/service",
}

CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def collect() -> list[tuple[Path, str]]:
    """返回 (本地文件, COS 对象键)。目录不存在或为空就跳过这一组。"""
    items: list[tuple[Path, str]] = []
    for group, prefix in GROUPS.items():
        directory = ASSETS_ROOT / group
        if not directory.is_dir():
            print(f"跳过 {group}/：目录不存在")
            continue

        files = sorted(p for p in directory.iterdir() if p.suffix.lower() in CONTENT_TYPES)
        if not files:
            print(f"跳过 {group}/：目录里没有图片")
            continue

        items.extend((p, f"{prefix}/{p.name}") for p in files)

    if not items:
        raise SystemExit("没有可上传的图片")
    return items


def upload(path: Path, key: str) -> None:
    data = path.read_bytes()
    # Content-Type 要给准：COS 会原样存下来，给成 octet-stream 的话浏览器会当附件下载
    headers = {"Content-Type": CONTENT_TYPES[path.suffix.lower()]}

    resp = httpx.put(cos.presign_put(key), content=data, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise SystemExit(f"上传失败 {key}：HTTP {resp.status_code} {resp.text[:200]}")

    print(f"  ↑ {key:<34} {len(data) / 1024:7.1f} KB")


def check(key: str) -> bool:
    """按公开地址回读，确认小程序那边真能拿到。"""
    resp = httpx.get(cos.object_url(key), timeout=30)
    ok = resp.status_code == 200
    mark = "✓" if ok else "✗"
    detail = f"{len(resp.content) / 1024:7.1f} KB  {resp.headers.get('content-type', '')}"
    print(f"  {mark} {key:<34} {detail if ok else f'HTTP {resp.status_code}'}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="上传小程序静态素材到 COS")
    parser.add_argument("--check", action="store_true", help="只校验线上可访问，不上传")
    args = parser.parse_args()

    if not cos.configured():
        raise SystemExit("缺少 COS 配置，检查 server/.env")

    items = collect()
    print(f"\n桶 {cos.host()}，共 {len(items)} 个文件\n")

    if not args.check:
        print("上传：")
        for path, key in items:
            upload(path, key)
        print()

    print("校验（公开地址回读）：")
    failed = [key for _, key in items if not check(key)]
    if failed:
        raise SystemExit(f"\n{len(failed)} 个文件不可访问")

    total = sum(path.stat().st_size for path, _ in items)
    print(f"\n全部就绪，小程序包可减少 {total / 1024:.0f} KB")
    print(f"静态地址前缀：https://{cos.host()}/static/")


if __name__ == "__main__":
    main()
