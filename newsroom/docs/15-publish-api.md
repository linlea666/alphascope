# 15 发布接口（RPA / 浏览器插件）

浏览器插件、RPA 或其他程序可以通过这组接口拿到“待发布”的稿件，发完把结果报回来，系统据此记录发布、算渠道统计、回填表现数据。人工发布走网页的“发布中心”，两者共用同一套记录。

## 0. 地址

插件和 RPA 访问网页的同一个地址，例如 `http://194.163.135.37:3300/api/feed/...`。后端 API 只监听本机，网页服务把带令牌的 `/api/feed/*` 请求原样转给它（不读也不带登录 cookie，允许跨域调用）。

## 1. 令牌

系统管理 → 接口令牌 → 新建：起个名字（例如“我的 Chrome 插件”），选用途（浏览器插件 / RPA / 其他程序），可以只勾选允许的账号。令牌只显示一次；服务器只保存它的哈希。每次请求带：

```
Authorization: Bearer nrt_xxxxxxxx
```

停用后立刻失效。所有调用写入审计日志；令牌记录的发布会标明渠道和令牌名。

## 2. 接口

### 列出可发的稿件

`GET /api/feed/ready?language=zh&account=alphascope_cn&limit=20`

只返回：已通过（AI 审稿或人工）、没过期、没被别的程序领走、账号启用中的稿件，按到期时间排序。

```json
{"as_of": "2026-11-24T08:00:00Z",
 "posts": [{"article_id": 123,
            "account": {"id": 1, "handle": "alphascope_cn", "platform": "x"},
            "language": "zh",
            "posts": ["🚨 鲸鱼警报\n\n0x048…cd87d 过去几小时把 $BTC 空单加到 3691 万美元 …"],
            "reply": "钱包地址：0x04889bbfa46fcf754ba06b1ca6d3e83265dcd87d",
            "image_url": "/api/feed/assets/456",
            "expires_at": "2026-11-24T10:12:00Z",
            "style": "zh_whale_alert",
            "event": {"class": "whale_add", "coin": "BTC"}}]}
```

- `posts`：第一条是正文，后面是串推（如果有）。
- `reply`：发完正文后，作为第一条评论发出去（完整钱包地址；账号开启时附浏览器链接）。
- `image_url`：用同一个令牌下载配图，附在正文上。

### 领取

`POST /api/feed/{article_id}/claim` → `{"ok": true, "post": {...}, "claim_expires_at": "..."}`

领取后 15 分钟内别的令牌拿不到这篇；超时没回报自动释放。已被别人领取返回 409。

### 下载配图

`GET /api/feed/assets/{asset_id}` → PNG（1200×675）

### 回报结果

`POST /api/feed/{article_id}/result`

成功：

```json
{"ok": true, "x_url": "https://x.com/alphascope_cn/status/1860000000000000001",
 "reply_url": "https://x.com/alphascope_cn/status/1860000000000000002",
 "published_at": "2026-11-24T08:03:00Z", "final_text": null}
```

`x_url`、`reply_url`、`published_at` 都可以不填（不填时间就是现在）。`final_text` 只在实际发出的文字和稿件不同时填，系统会重新核对事实。返回 `{"ok": true, "publication_id": 789}`。

失败（例如登录失效）：

```json
{"ok": false, "error": "login wall"}
```

稿件回到待发布，原因写入记录。

### 回填表现数据

`POST /api/feed/publications/{publication_id}/metrics`

```json
{"impressions": 1200, "likes": 31, "reposts": 4, "replies": 2, "bookmarks": 6, "captured_at": "2026-11-25T08:00:00Z"}
```

字段都可选。数据进入发布记录的表现栏和每周质量报告。

## 3. 最小示例（伪代码）

```js
const H = { Authorization: `Bearer ${TOKEN}` };
const { posts } = await (await fetch(`${BASE}/api/feed/ready?language=zh`, { headers: H })).json();
for (const p of posts) {
  const claim = await fetch(`${BASE}/api/feed/${p.article_id}/claim`, { method: "POST", headers: H });
  if (!claim.ok) continue;                       // 别的程序已经在发
  const image = p.image_url && await (await fetch(BASE + p.image_url, { headers: H })).blob();
  try {
    const url = await postToX(p.posts, image);   // 你的发布逻辑
    const replyUrl = p.reply && await replyOnX(url, p.reply);
    await fetch(`${BASE}/api/feed/${p.article_id}/result`, { method: "POST", headers: { ...H, "content-type": "application/json" },
      body: JSON.stringify({ ok: true, x_url: url, reply_url: replyUrl }) });
  } catch (e) {
    await fetch(`${BASE}/api/feed/${p.article_id}/result`, { method: "POST", headers: { ...H, "content-type": "application/json" },
      body: JSON.stringify({ ok: false, error: String(e) }) });
  }
}
```

## 4. 常见错误

| 状态码 | 原因 |
|---|---|
| 401 | 没带令牌、令牌写错或已停用 |
| 403 | 这个令牌没被允许用于这个账号 |
| 409 | 稿件已被别的程序领取、已过期、已作废，或不是“已通过”状态 |
| 423 | 账号暂停、授权被撤销/过期、紧急停止中，或今日发布已达上限 |

## 5. 旧接口

`GET /api/rpa/next` 和 `POST /api/rpa/plans/{id}/result`（按排期发布的单令牌接口）继续可用，现在也会返回评论区内容 `reply`，配图只返回这篇稿件对应的那张。
