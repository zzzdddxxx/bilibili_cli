# B 站扫码登录：完整流程与数据分析报告

> 数据来源：真实抓包（浏览器等效流程，逐请求记录）
> 分析日期：2026-11-03
> 说明：报告中 `SESSDATA` 等持久凭证已脱敏，只展示格式；`ticket`/`qrcode_key` 为一次性会话票据，已过期失效。

---

## 一、总体概览

B 站扫码登录的本质是：**手机 App 确认身份 → 服务器发一次性 ticket → 客户端用 ticket 在多域名兑换成 `SESSDATA` cookie**。

整个流程涉及 **6 个 HTTP 请求**（不含轮询）和 **4 个域名**：

| 序号 | 请求 | 域名 | 作用 |
|---|---|---|---|
| 0 | GET `/` | `www.bilibili.com` | 预热，种基础 cookie |
| 1 | GET `/x/passport-login/web/qrcode/generate` | `passport.bilibili.com` | 生成二维码 |
| 2 | GET `/x/passport-login/web/qrcode/poll` | `passport.bilibili.com` | 轮询扫码状态，**成功后种主站 SESSDATA** |
| 3 | POST `/x/passport-login/web/sso/list` | `passport.bilibili.com` | 获取分域名 ticket |
| 4 | POST `/x/passport-login/web/sso/set` ×3 | `biligame.com`/`bilibili.cn`/`huasheng.cn` | 给各域名种 cookie（SSO 扩散） |

---

## 二、完整数据流

```
手机App（已登录）
      │ 扫码 + 确认
      ▼
CLI ── poll(轮询) ──→ 服务器：手机确认了吗？
      │
      │ ← 服务器确认后，poll 响应 Set-Cookie 种下 SESSDATA（主站 bilibili.com）
      │
CLI ── sso/list ──→ 服务器：我要给哪些域名种 cookie？
      │ ← 返回 3 个 sso/set 地址（各带 ticket）
      │
CLI ── sso/set ×3 ──→ 各域名服务器：用 ticket 换 cookie
      │ ← 每个响应 Set-Cookie 种下 SESSDATA（对应域名）
      │
      ▼
最终：bilibili.com / biligame.com / bilibili.cn / huasheng.cn 全部登录态
```

---

## 三、逐步详解

### 第 0 步：预热主站

| 项 | 内容 |
|---|---|
| 请求行 | `GET https://www.bilibili.com/` |
| 携带 Cookie | 无（首次） |
| 种下 Cookie | `buvid3`（设备指纹）、`b_nut`（时间戳） |

```http
Set-Cookie: buvid3=4FC93230-...; domain=.bilibili.com; expires=2027年
Set-Cookie: b_nut=1793673201; domain=.bilibili.com; expires=2027年
```

**作用**：`buvid3` 是设备指纹（反爬风控的关键标识），后续所有请求都要携带。`domain=.bilibili.com` 表示主域 cookie，子域共享。

### 第 1 步：generate 生成二维码

| 项 | 内容 |
|---|---|
| 请求行 | `GET .../qrcode/generate?source=main-fe-header` |
| 参数 | `source=main-fe-header`（标识登录入口） |
| 携带 Cookie | `b_nut`、`buvid3` |
| 获得数据 | `qrcode_key=2ab18af4...` + 二维码 URL |

```json
{
  "code": 0,
  "data": {
    "url": "https://account.bilibili.com/h5/.../scan-web?...&qrcode_key=2ab18af4...",
    "qrcode_key": "2ab18af472392e5f9059551f3ea25a49"
  }
}
```

**`qrcode_key`** = 本次登录会话的唯一 ID，二维码里就带着它，手机扫到后凭它向服务器声明"我确认这个会话"。

### 第 2 步：poll 轮询（**核心，主站凭证在此种下**）

| 项 | 内容 |
|---|---|
| 请求行 | `GET .../qrcode/poll?qrcode_key=2ab18af4...&source=main-fe-header` |
| 参数 | `qrcode_key`（会话ID） |
| 携带 Cookie | `b_nut`、`buvid3`（**此时还没有 SESSDATA**） |
| 获得 | `ticket` + **Set-Cookie 种下主站 SESSDATA** |

轮询中的 `data.code` 状态：

| code | 含义 |
|---|---|
| `86101` | 未扫码 |
| `86090` | 已扫码未确认 |
| `0` | 登录成功 |
| `86038` | 二维码过期 |

**扫码成功后，poll 响应直接 `Set-Cookie` 种下主站登录凭证**（这是最关键的发现）：

```http
Set-Cookie: SESSDATA=0226dc55%2C...; Domain=bilibili.com; HttpOnly; Secure; SameSite=None
Set-Cookie: bili_jct=07352150...; Domain=bilibili.com
Set-Cookie: DedeUserID=283842907; Domain=bilibili.com
Set-Cookie: DedeUserID__ckMd5=8a8bd870...; Domain=bilibili.com
Set-Cookie: sid=f8y9vapp; Domain=bilibili.com
Set-Cookie: default_sites=...; Domain=bilibili.com
```

同时响应体给出 crossDomain 地址（含 ticket）：

```json
"url": "https://passport.biligame.com/.../crossDomain?ticket=51844c1d...&gourl=https%3A%2F%2Fwww.bilibili.com&first_domain=.bilibili.com"
```

> **这就是 SDK 登录失败的根因**：旧 SDK 逻辑去 `url` 参数里硬找 `SESSDATA=xxx`，但 `SESSDATA` 根本不在 url 里，而是在 **poll 响应的 `Set-Cookie` 头**里。

### 第 3 步：sso/list 获取分域名 ticket

| 项 | 内容 |
|---|---|
| 请求行 | `POST .../web/sso/list` |
| 请求 body | `csrf=07352150c3895b917ac167c196a290d1` |
| 携带 Cookie | **已带全套**：SESSDATA、bili_jct、DedeUserID、buvid3、sid 等 |
| 获得数据 | 3 个 `sso/set` 地址（各带不同 ticket） |

请求头 Cookie（此时已登录主站）：

```http
Cookie: DedeUserID=283842907; DedeUserID__ckMd5=...; SESSDATA=0226dc55%2C...;
        b_nut=1793673201; bili_jct=07352150...; buvid3=...; default_sites=...; sid=...
```

响应：

```json
{
  "data": {
    "sso": [
      "https://passport.biligame.com/.../sso/set?ticket=08e8d20c...",
      "https://passport.bilibili.cn/.../sso/set?ticket=406b9033...",
      "https://passport.huasheng.cn/.../sso/set?ticket=162309b8..."
    ]
  }
}
```

**关键点**：`csrf` 的值 = cookie 里的 `bili_jct` 值（都是 `07352150...`），即 **csrf 直接从 `bili_jct` 取**。

### 第 4 步：sso/set ×3（SSO 扩散）

每个请求用各自的 `ticket`，**请求 body 为空**（`Content-Length: 0`），纯粹用 ticket 换 cookie。

| 请求 | 种 cookie 的 Domain |
|---|---|
| `sso/set` #1 → `passport.biligame.com` | `Domain=biligame.com` |
| `sso/set` #2 → `passport.bilibili.cn` | `Domain=bilibili.cn` |
| `sso/set` #3 → `passport.huasheng.cn` | `Domain=huasheng.cn` |

每个响应都种下同一套 6 个 cookie（`SESSDATA`、`bili_jct`、`DedeUserID`、`DedeUserID__ckMd5`、`sid`、`default_sites`），只是 `Domain` 不同。

**这就是单点登录（SSO）的落地**：同一份登录凭证，通过不同 `Domain` 种到不同域名，让你在这些域名都"已登录"。

### 第 5 步：最终提取

`www.bilibili.com` 下最终拿到 8 个 cookie：

| Cookie | 值（示例，已脱敏） | 作用 |
|---|---|---|
| `SESSDATA` | `0226dc55,1809225209,05a4b*b1` | **登录凭证核心**（随机值,过期时间戳,签名） |
| `bili_jct` | `07352150...`（32位hex） | CSRF token，写操作必需 |
| `DedeUserID` | `283842907` | 用户 ID |
| `DedeUserID__ckMd5` | `8a8bd870...` | 用户 ID 校验 |
| `buvid3` | `4FC93230-...` | 设备指纹 |
| `b_nut` | `1793673201` | 时间戳 |
| `sid` | `f8y9vapp` | 会话 ID |
| `default_sites` | `["CN","OVERSEAS"]` | 站点偏好 |

---

## 四、Cookie 的 Domain 与跨域认证

观察 `Set-Cookie` 里的 `Domain` 字段变化：

```
第0步 预热   → Domain=.bilibili.com   （主域，子域共享）
第2步 poll   → Domain=bilibili.com    （主站登录态）
第4步 sso/set → Domain=biligame.com / bilibili.cn / huasheng.cn
```

- `Domain=.bilibili.com`（带点）= 主域 cookie，`www/api/passport.bilibili.com` 都能读
- `Domain=bilibili.com` 与 `Domain=.bilibili.com` 等价
- 不同**主域**（`biligame.com` vs `bilibili.com`）无法共享 cookie，所以需要 `sso/set` 分别种

其他重要属性：

| 属性 | 含义 |
|---|---|
| `HttpOnly` | 禁止 JS 读取（防 XSS 偷 cookie），只在 `SESSDATA` 上有 |
| `Secure` | 仅 HTTPS 传输 |
| `SameSite=None` | 允许跨站请求携带（配合跨域 SSO） |
| `Expires` | 过期时间（`SESSDATA` 约 1 天，其余较长） |

---

## 五、核心结论

1. **主站 `SESSDATA` 来自 poll 成功响应**（`Set-Cookie` 头，不是 url 参数）—— 这是 SDK 旧逻辑失败的根本原因。

2. **`sso/set` 是 SSO 单点登录扩散**，把同一套凭证种到 `biligame.com`、`bilibili.cn`、`huasheng.cn` 三个域。

3. **`csrf` = `bili_jct`**：`sso/list` 的 csrf 参数直接从 cookie 里的 `bili_jct` 取值。

4. **`ticket` 是一次性授权码**（OAuth 思路），用一次就失效，且分域名各自独立。

5. **`refresh_token` 本次为空**：说明它并非登录必需，`SESSDATA` 才是核心凭证；`refresh_token` 只在"刷新过期 cookie"时才用到。

---

## 六、对 CLI 实现的启示

| 要素 | 实现要点 |
|---|---|
| Cookie 管理 | 必须用**同一个 `CookieJar`** 贯穿全流程（poll 种的 SESSDATA 要能被后续请求自动带上） |
| 主站凭证 | 从 **poll 响应的 Set-Cookie** 提取，不是从 url 解析 |
| csrf | 从 cookie 罐里的 `bili_jct` 取值 |
| 请求头 | 需带浏览器 `User-Agent` + `Referer` + `Origin` |
| 完整流程 | 预热 → generate → poll → sso/list → sso/set，缺一不可 |
