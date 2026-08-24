"""把小程序的静态素材推到 COS。

小程序主包有 2MB 上限，品牌实拍图占了 1.2MB，进包不划算：这些图不参与业务逻辑，
换图也不该走发版。传到 COS 后小程序用固定 URL 引用，图还留在仓库里做版本追溯，
靠 project.config.json 的 packOptions.ignore 排除出包。

与用户上传的区别：用户图走 uploads/ 前缀、键由服务端随机生成、地址现签；
这里的静态素材走 static/ 前缀、路径稳定可预测、直接公开访问。两者只是同一个桶
里的两个前缀，将来桶权限收紧成私有读时，static/ 下的对象要单独设公有读 ACL。

⚠️ 默认传的是**生产桶**，不是 .env 里的 COS_BUCKET。
客户端引用这批图的地址是写死的（小程序 utils/config.js 与官网 src/shared/config.ts
的 STATIC_BASE），全都指着生产桶；跟着 .env 走的话，本地开发时换图会传进开发桶，
脚本报成功而线上纹丝不动——静默失败，比直接报错难查得多。要传别处用 --bucket。

用法（在 server 目录下）：
    uv run python scripts/upload_static.py            # 传 + 校验
    uv run python scripts/upload_static.py --check    # 只校验线上是否可访问
    uv run python scripts/upload_static.py --bucket antony-casa-dev-1327365963

可重复执行：同名对象直接覆盖。
"""

import argparse
import os
import sys
from pathlib import Path
from types import ModuleType

import httpx

# 脚本在 server/scripts 下，仓库根是上两级
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "server"))

# 客户端 STATIC_BASE 里写死的那个桶。改这里就要同时改：
#   antony-casa/utils/config.js 的 STATIC_BASE
#   antony-web/src/shared/config.ts 的 STATIC_BASE
#   antony-web/index.html（preconnect / og:image / 首屏 <img> 三处）
STATIC_BUCKET = "antonycasa-pro-1327365963"
STATIC_REGION = "ap-shanghai"

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


def load_cos(bucket: str, region: str) -> ModuleType:
    """按指定桶/地域加载 app.cos。

    那个模块在导入期就把 BUCKET / REGION 读成模块级常量，所以必须在 import 之前
    改环境变量；它内部的 load_dotenv 不覆盖已有的值，这里设的会赢。
    """
    os.environ["COS_BUCKET"] = bucket
    os.environ["COS_REGION"] = region
    from app import cos

    return cos


# trust_env=False：绕开环境里的 HTTPS_PROXY。COS 是境内地址本来就不该走代理，
# 而且实测代理会在图片大小的请求体上直接断连（几十字节的小请求却能过），
# 表现是 httpx.RemoteProtocolError，看着像 COS 的问题，其实一步都没出内网
def make_client() -> httpx.Client:
    return httpx.Client(trust_env=False, timeout=120)


def upload(client: httpx.Client, cos: ModuleType, path: Path, key: str) -> None:
    data = path.read_bytes()
    # Content-Type 要给准：COS 会原样存下来，给成 octet-stream 的话浏览器会当附件下载
    headers = {"Content-Type": CONTENT_TYPES[path.suffix.lower()]}

    resp = client.put(cos.presign_put(key), content=data, headers=headers)
    if resp.status_code != 200:
        raise SystemExit(f"上传失败 {key}：HTTP {resp.status_code} {resp.text[:200]}")

    print(f"  ↑ {key:<34} {len(data) / 1024:7.1f} KB")


def check(client: httpx.Client, cos: ModuleType, key: str) -> bool:
    """按公开地址回读，确认小程序那边真能拿到。"""
    resp = client.get(cos.object_url(key))
    ok = resp.status_code == 200
    mark = "✓" if ok else "✗"
    detail = f"{len(resp.content) / 1024:7.1f} KB  {resp.headers.get('content-type', '')}"
    print(f"  {mark} {key:<34} {detail if ok else f'HTTP {resp.status_code}'}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="上传小程序静态素材到 COS")
    parser.add_argument("--check", action="store_true", help="只校验线上可访问，不上传")
    parser.add_argument(
        "--bucket", default=STATIC_BUCKET, help=f"目标桶，默认 {STATIC_BUCKET}（客户端读的那个）"
    )
    parser.add_argument("--region", default=STATIC_REGION, help=f"目标地域，默认 {STATIC_REGION}")
    args = parser.parse_args()

    cos = load_cos(args.bucket, args.region)
    if not cos.configured():
        raise SystemExit("缺少 COS 配置，检查 server/.env")

    items = collect()
    print(f"\n桶 {cos.host()}，共 {len(items)} 个文件\n")

    client = make_client()
    if not args.check:
        print("上传：")
        for path, key in items:
            upload(client, cos, path, key)
        print()

    print("校验（公开地址回读）：")
    failed = [key for _, key in items if not check(client, cos, key)]
    if failed:
        raise SystemExit(f"\n{len(failed)} 个文件不可访问")

    total = sum(path.stat().st_size for path, _ in items)
    print(f"\n全部就绪，小程序包可减少 {total / 1024:.0f} KB")
    print(f"静态地址前缀：https://{cos.host()}/static/")


if __name__ == "__main__":
    main()
