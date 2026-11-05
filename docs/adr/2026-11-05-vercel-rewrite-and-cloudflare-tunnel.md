---
title: 部署拓樸：前端在 Vercel，以同源 rewrite 轉送 /api，backend 在 VM 經 Cloudflare Tunnel 對外
date: 2026-11-05
review-date: 2027-05-05
status: active
supersedes-parts: "2026-10-18-api-and-auth-baseline.md 的 D1（拓樸）、D6（per-IP 限流的 IP 來源）、D8（proxy 部分），以及約束段、Expected Outcome、Revocation Triggers 中提到 Caddy 的部分"
tags: [fullstack-template, decision, deployment, security]
---

# 部署拓樸：前端在 Vercel，以同源 rewrite 轉送 /api，backend 在 VM 經 Cloudflare Tunnel 對外

## Context

Go 版範本（template-go-nextjs@560106d）把前端、backend 與 PostgreSQL 都放在一台 VPS 的 Docker Compose 裡，前面是 compose 內的 Caddy（[API 與認證基準](2026-10-18-api-and-auth-baseline.md) D1、D8）。這個 repo 改成前端放 Vercel（Will 2026-11-03），Caddy 拿掉。Caddy 原本負責的事都要有新的負責者：`/api/*` 同源路由、前端與 backend 的 HTTPS 與 HSTS、backend 不直接對外、給 per-IP 限流的可信 proxy 位址（`TRUSTED_PROXIES`）、覆寫 `X-Request-ID`、backend 重建時暫留請求（`lb_try_duration`）。

**約束**：

- `external`：前端放 Vercel，backend 與 PostgreSQL 放一台 VM（Will 2026-11-03）。
- `external`：Vercel 的出口 IP 不固定，付費的 Static IPs 只涵蓋 Functions，所以 backend 不能用 IP 白名單辨認 Vercel（[Static IPs](https://vercel.com/docs/networking/static-ips)）。external rewrite 每個請求最多 120 秒（[Limits](https://vercel.com/docs/limits)）。
- `inherited`：同源是 `__Host-` cookie、SSR 拿得到 cookie、CSP `connect-src 'self'` 與 Fetch Metadata CSRF 防護的前提（基準 ADR D1、D5）。仍成立：這些機制已實作，前端與契約沿用 Go 版 `560106d`。
- `external`：範本本身不部署（Will 2026-11-03），正式路徑在範本的 CI 裡沒有真的 Vercel 與 Cloudflare 可測。

## Options Considered

- **基準：前後端分子網域加 CORS**（多數 SPA＋API 的做法，[MDN CORS](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS)）：cookie 帶 `Domain`、不再用 `__Host-`，CORS 帶憑證，CSP 列 API 網域。代價是推翻基準 ADR D1，前端至少改 `lib/api/client.ts`、`lib/env.ts`、`proxy.ts`、`next.config.ts` 四個程式檔，兩個範本的前端不再相同。
- **A. Vercel 同源 rewrite**（選用）：`vercel.json` 把 `/api/*` external rewrite 到 backend，同一條 route 用 transform 設 `X-Origin-Secret`（`set` 會覆寫 client 帶的值；Vercel 文件「Restricting your origin to Vercel traffic」）。瀏覽器仍同源，cookie、CSP、CSRF 都不變。
- **B. FastAPI 也放 Vercel**，PostgreSQL 放外部：不用管 backend 的 HTTPS，但要處理冷啟動與連線池，而且違反「backend 放 VM」的約束。
- backend 的 HTTPS 與不對外：**Cloudflare Tunnel**（選用；VM 不開 port、不用續約，代價是網域 DNS 交給 Cloudflare）、uvicorn 加 certbot（port 與續約自己管）、只為 HTTPS 放回 Caddy。

## Decision

- **D1 路由**：A。`frontend/vercel.json` 一條 route 同時做 rewrite（目的地 `$BACKEND_ORIGIN`）與 transform（`X-Origin-Secret` = `$ORIGIN_SECRET`），兩者都從 Vercel 環境變數讀，不進 git；`scripts/check_vercel_config.py` 在 CI 守住這個形狀。dev 用 `next.config.ts` 的 rewrite。SSR 直接連 `API_URL`，伺服器端程式碼自己帶密鑰；Vercel Function 的區域設在靠近 VM 的地方。
- **D2 對外入口**：compose 的 `cloudflared` 服務（`profiles: [tunnel]`、`--token-file`），在 Cloudflare 後台只轉送 `/api/*` 到 `backend:8000`；compose 不發佈任何 port。`/health`、`/health/ready` 只接受 loopback，不依賴 tunnel 的路由規則。
- **D3 可信來源與 client IP**：backend 只信任帶正確 `X-Origin-Secret` 的請求所附的 `X-Real-IP`（Vercel 會覆寫它，client 偽造的值傳不到 backend），取代 `TRUSTED_PROXIES`。沒帶密鑰：`MODE=dev` 放行，其餘拒絕；帶錯一律 403。SSR 不帶 `X-Real-IP`，backend 用連線來源位址；限流只作用在瀏覽器發出的註冊與登入，不受影響。密鑰只存在 Vercel 環境變數與 VM 的 compose secret `.secrets/origin_secret`。
- **D4 其他**：API 回應帶 `Cache-Control: no-store`（2026-04-06 之後建立的 Vercel 專案，external rewrite 預設會照上游的 `Cache-Control` 快取）；`X-Request-ID` 由 backend 自己產生；`lb_try_duration` 沒有替代。

## Rationale

- 選 A 不選基準：基準是更常見的拓樸，但它要改四個前端檔案與 cookie 屬性，還要加 CORS；A 讓前端程式與契約幾乎不變（只有部署設定與伺服器端帶密鑰），同源帶來的 `__Host-` cookie、CSP 與 CSRF 防護照舊成立。代價：多一個共享密鑰要輪換；Vercel 文件沒寫 `Cookie`／`Set-Cookie` 會穿過 external rewrite，這點沒有實測。
- 選 Cloudflare Tunnel：VM 不開任何 port，等於保留 Go 版「backend 不直接對外」的不變量，也不用自己續約憑證。代價：網域 DNS 交給 Cloudflare，Cloudflare 與 Vercel 都終止 TLS，兩者都是新的資料處理者（`docs/privacy/data-inventory.md`）。
- 用密鑰加 `X-Real-IP` 取代 `TRUSTED_PROXIES`：Vercel 沒有固定 IP 可列，而密鑰可以證明請求經過 Vercel，`X-Real-IP` 又是 Vercel 覆寫的，這樣 per-IP 限流的 key 不能由 client 自選（MDN「X-Forwarded-For」：只信任已知 proxy）。
- 接受的代價：dev 走 `next dev` rewrite、正式走 Vercel，不再是同一套 proxy（基準 ADR D1 原本用 Caddy 換到的 dev/prod parity 沒了）；來源密鑰與 `X-Real-IP` 的正式路徑在 CI 沒有自動化覆蓋，靠 backend 單元測試、`vercel.json` 的 policy 檢查與部署文件的第一次部署檢查清單；backend 重建的空檔請求會失敗，與基準 ADR D8 已接受的代價相同；Vercel preview 部署不設 `ORIGIN_SECRET`，所以 preview 呼叫不了 API。

## Expected Outcome

- 瀏覽器只看到一個網域；`__Host-session` cookie、CSP `connect-src 'self'` 與 CSRF 防護和 Go 版 `560106d` 相同，前端程式只有部署設定與伺服器端帶密鑰的差異。
- 沒有帶正確密鑰的請求偽造不了限流用的 client IP。
- VM 沒有發佈任何 port（CI stack job 檢查）。

## Invariants

- backend 只信任帶正確來源密鑰的請求所附的 `X-Real-IP`：`backend/src/app/web/origin.py`、per-IP 限流的 key、access log 的 `client_ip`。
- 來源密鑰不進 git：`scripts/init_env.py`、compose 的 `origin_secret` secret、`vercel.json` 以 `$ORIGIN_SECRET` 引用。
- compose 沒有服務發佈 port：CI stack job 的檢查（用 `--profile tunnel` 的 config）。

## Revocation Triggers

- 衍生專案第一次部署時 cookie 不能穿過 rewrite → 改選基準（分子網域加 CORS）。
- 第一次部署檢查清單發現 `Sec-Fetch-Site` 或 `Origin` 到不了 backend（跨來源 POST 沒被擋）→ 先在 `vercel.json` 的 route 拒絕沒有 `Sec-Fetch-Site` 的非安全方法請求；做不到就改選基準，CSRF 改用 synchronizer token（OWASP CSRF Prevention Cheat Sheet）。
- 需要在 Vercel preview 上操作 API → 加一個 staging backend 與 preview 用的密鑰。
- 要求零停機部署，或 backend 超過一個副本 → 在 VM 上加 blue-green 或放回一層 proxy；per-IP 限流改用 DB 或 proxy 層。
- Vercel 提供涵蓋 rewrite 的固定出口 IP → 重新評估密鑰與 IP 白名單的取捨。

## Review Notes

## Related

<!-- >>> template-only -->
- 來源：FastAPI 版計畫 `docs/plans/2026-11-03-fastapi-template.md` 的 D6（Will 2026-11-03 拍板；Vercel 文件 [Rewrites](https://vercel.com/docs/routing/rewrites)、[Request headers](https://vercel.com/docs/headers/request-headers) 由 subagent 於當天讀取）。
<!-- <<< template-only -->
- 部分取代：[API 與認證基準](2026-10-18-api-and-auth-baseline.md)（各段標有 2026-11-05 修訂）。
- 部署步驟與第一次部署檢查清單：`docs/deploy-vps.md`。
