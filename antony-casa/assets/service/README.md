# 服务页配图

放五个服务各自的实拍图。**文件名必须用服务 id**，改 `mock/service.js` 时按名字对得上：

| 文件名 | 序号 | 服务 |
|---|---|---|
| `design.jpg` | 01 | 全案设计服务 |
| `hardfit.jpg` | 02 | 硬装施工服务 |
| `buyer.jpg` | 03 | 商品买手服务 |
| `aftersale.jpg` | 04 | 商品售后服务 |
| `resale.jpg` | 05 | 商品流转服务 |

## 规格

- **比例约 2:1 的横图**。卡头是 `750rpx × 360rpx` 的容器，`mode="aspectFill"` 会裁掉溢出部分，
  竖图或方图会被裁得只剩中间一条
- **建议 1500 × 720 像素左右**，再大在手机上看不出差别，只是白白拖慢加载
- **单张控制在 200KB 以内**。图走 COS 网络加载，不占小程序包，但首屏等待时间是真实的
- 格式 `.jpg` / `.png` / `.webp` 都收，优先 jpg
- **画面左上角留空**：序号和服务名会压在图上（`shot-title`），那块位置太花会看不清字

## 放好之后

```bash
cd server
uv run python scripts/upload_static.py     # 传到 COS 的 static/service/ 并回读校验
```

然后把 `mock/service.js` 里五个 `image` 改成 `` `${STATIC_BASE}/service/<id>.jpg` ``。

图**不进小程序包**（`project.config.json` 的 `packOptions.ignore` 已排除本目录），
留在仓库里是为了版本追溯和换图有源。换图就是替换同名文件再跑一次脚本，不用发版。

## 没图的时候

`image` 留空即可——服务页会自动走深色纯文字卡头（`service.wxml` 里的 `shot-plain`），
不会出裂图。当前用的是首页画廊的图，跟首页重复，有实拍后应尽快换掉。
