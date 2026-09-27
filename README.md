# Boss 直聘网页版自动打招呼

本地网页工具：微信扫码保存本机登录摘要，复用微信小程序登录态搜索职位并批量打招呼。

## 登录

**微信扫码**：点「获取二维码」→ 手机微信扫一扫确认 → 凭证写入本机 `data/session.json`（已 gitignore，不会入库）。

> 搜索 / 打招呼依赖微信中已登录的 **BOSS 直聘** 小程序；扫码只保存本机摘要，不替代小程序登录。

## 前置

1. 启动服务后访问 <http://127.0.0.1:8787>，扫码登录
2. 搜索 / 打招呼前：在微信中打开并登录 **BOSS直聘** 小程序，并保持本机小程序调试通道可用

## 启动

```bash
cd boss-greet-web
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8787
```

浏览器打开 <http://127.0.0.1:8787>

## 说明

- 搜索：`/wapi/zpgeek/miniapp/search/joblist.json`
- 详情（补活跃）：`/wapi/zpgeek/miniapp/job/detail.json`
- 打招呼：`/wapi/zpgeek/miniapp/friend/add.json`
- 「当前在线 / 最近活跃」为客户端筛选
- 招呼语使用平台默认；城市取当前求职期望
