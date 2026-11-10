# Vue 前端设计与面试讲解

## 项目定位

这是对已有法务初审系统的**前端迁移**——后端业务接口、权限、任务执行和模型调用逻辑全部不变。

**版本**：Vite 7.3.8、Vue 3.5.45、Vue Router 4.6.4、Pinia 4.1.0。`package.json` 固定直接版本，`package-lock.json` 锁定完整依赖。验证环境 Node 22.12.0 / npm 10.9.0。

**零依赖边界**：没有 UI 组件库、没有 CDN、没有外部字体、没有运行时 Markdown 依赖。

**兼容**：原生版本继续保留在 `app/static`，旧 `.html` 地址仍可使用，避免破坏旧链接和离线 `/docs`。

---

## 为什么用 Composition API

**做法**：所有组件用 `<script setup>`，没有 Options API。

**理由**：以详情页为例，合同数据、分页、任务轮询的状态和函数在同一作用域内——`ref` 声明响应式状态，`onMounted` 加载，`onUnmounted` 清除计时器并阻止已离页请求回写。这比按 `data` / `methods` / `mounted` 分散逻辑更适合组织异步任务。

**代价**：它并不天然减少所有代码，`ref` 的 `.value` 和闭包生命周期需要额外理解。

**扩展点**：若后续多页共享轮询，再抽 composable——当前不为抽象而抽象。

---

## 组件拆分原则

**按什么拆**：稳定的业务展示边界，不按每个标签拆。

| 组件 | 职责 |
|---|---|
| `AppShell` | 导航和布局 |
| `App.vue` | 只渲染 `router-view` |
| `RiskCard` | 风险标题、等级、建议、原文、依据展开（**不负责请求**） |
| `StatusBadge` | 统一解析/任务/风险等级的中文与样式，用于列表、详情、结果、RiskCard |
| `StatBar` | 只接收结果 stats，不反向请求 |
| `Toast` | 三秒展示与卸载清理 |
| `EmptyState` | 空列表引导 |

**边界**：页面负责取数、操作和错误处理，通过 props 把数据传给组件。

**收益**：可以独立改变卡片样式，而不复制四套风险展示逻辑。

---

## 为什么 Pinia 只放用户状态

**跨路由真正共享的**：token、role、是否登录。Pinia 负责读写 `sessionStorage`，视图不各自调用 `sessionStorage.getItem`。

**业务数据为什么不放**：列表数据只属于列表页，详情和结果来自各自资源接口，放在页面 `ref` 里即可。退出时无需清理多份业务缓存，也避免刷新后显示其他账号的旧列表。

**边界**：token 存在仅表示客户端有会话凭据，不代表服务端认证一定有效——过期和 `auth_version` 变化由后端 401 判定。

**存储选择**：`sessionStorage` 延续刷新、按浏览器会话保存；不承诺浏览器恢复旧会话时一定清空。

---

## 路由守卫与登录回跳

**做法**：`router.beforeEach` 读 Pinia 的 `isLoggedIn`。只有登录页 `meta.public = true`，其余（包括前端 404 页）都需先登录。

**回跳**：未登录时跳 `/workspace/login?redirect=<to.fullPath>`。登录成功后 `safeRedirect` **只允许本站 workspace 下的合同、结果、报告路径**，防止外部跳转。

**刷新不丢登录**：store 从 `sessionStorage` 恢复，不依赖内存。

**关键认知**：守卫只提供交互体验，**资源安全仍由后端 Bearer 和 owner/reviewer 检查保证**——不能把前端守卫当权限系统。

---

## fetch 和统一 401

**为什么用原生 fetch**：现有接口只涉及 JSON、multipart、文本报告，浏览器已具备这些能力，再引 axios 收益有限。

**唯一入口**：`api/index.js` 集中处理同源校验、Authorization、JSON 序列化、超时、错误信息、401。

**401 处理**：先 `store.logout()` 清空状态及持久化，再 `router.replace` 到登录页并保存原路径。登录失败时不再次跳登录，保留统一的「账号或密码错误」。

**上传细节**：`FormData` 不手动设置 `Content-Type`，让浏览器自己写 boundary。

**封装**：所有端点封装成 `api.contracts`、`api.review` 等方法，组件不拼 token、不直接 fetch。

**易错点**：必须手工检查 `response.ok`——**fetch 不会因 HTTP 4xx 自动 reject**。

---

## history 模式与后端 fallback

**选择**：用 `createWebHistory`，保留干净 URL，没退回 hash 模式。

**路径设计**：页面统一放 `/workspace/...`。因为 `/contracts` 等路径已是 API，不能混用同一路径去猜 `Accept` 头。

**开发环境**：Vite dev server 处理页面 fallback，API 通过 proxy 转发 `127.0.0.1:8000`。

**生产环境**：
- FastAPI 只对 `GET/HEAD` 的 `/workspace` 页面和 `/assets` 资源放行静态壳
- `/workspace` 子路径返回 `dist/index.html`
- 业务接口继续走原认证
- 未知前端路径由 Vue 显示「页面不存在」
- **缺失的 `/assets/*.js` 仍返回 404**——不能把不存在的 JS 也回成 HTML

**降级**：未构建时 `/workspace` 返回 503 并提示构建。根地址有构建时打开 Vue，没有构建时保持旧入口。

---

## 数据和生命周期

**列表页**：只用 `/contracts` 响应计算合同数和最新任务已完成数。**不遍历请求每份合同的审查结果**，也不展示拿不到的风险总数。

**发起审查**：按钮有本地提交锁，服务端仍负责活动任务去重。

**详情页**：从服务器最新任务恢复轮询，2 秒一次，终态停止。离页清计时器，通过 `alive` 阻止迟到响应继续调度。再次进入时重新读服务端，**避免把浏览器内存当任务真相**。

**结果页**：统计直接使用后端 `stats`，`merged` 不重复计入风险。

**错误处理**：页面保留可重试提示，同时 Toast 三秒消失——避免提示消失后用户无从处理。

---

## 安全 Markdown 与零 UI 库

**不使用 `v-html`**。

**解析器**：极简实现，只生成固定的 `h1/h2/h3`、`p`、列表、代码块和加粗的结构化数据。Vue 插值负责转义，不支持的 HTML 按文本显示，下载保留原始 Markdown。

**动态标签**：来自解析器内部固定集合，不来自合同指令。

**样式组织**：变量在 `tokens.css`，基础与布局在 `base.css`，375px 改单列。

**已知不足**：手写样式容易保持产品一致，但需要自己维护弹窗键盘可达性等细节。当前弹窗支持关闭和遮罩关闭，**尚未实现完整 focus trap**——这是后续可改进点。

---

## 和原生 JS 版本的比较

**收益**：

- 状态到 DOM 由 Vue 声明式更新，不再散落 `getElementById`、`replaceChildren` 和手工拼 DOM
- 组件、路由、用户状态、请求职责分明
- 多个页面共用状态标签，退出行为一致
- 异步状态更容易定位

**代价**：

- 新增 Node 和构建步骤
- 依赖升级成本
- 首次 JS 下载及框架概念
- 生产必须处理 history fallback
- 调试响应式和组件生命周期比单页原生脚本复杂

**诚实结论**：五个页面并不证明必须用 Vue。这次迁移的价值是**让后续人工复核、筛选、编辑等交互增长时保持结构**，而不是把原生代码机械包进组件。

---

## 本地运行和构建

```powershell
npm ci
npm run build
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 浏览器：http://127.0.0.1:8000/workspace/contracts

# 开发另开终端：
npm run dev   # 默认 5173，API 代理到 8000
```

**注意**：`dist` 必须通过 HTTP 提供，不能直接双击 `index.html`。Node 只用于开发与构建，生产 API 进程不需要 Node。

**Docker 部署**：Dockerfile 已补 Node 多阶段构建，最终 Python 镜像仅复制 `dist`，不含 `node_modules`。

**已实测**：`docker compose build` 成功（`Built 16.1s`，含前端构建步骤），三容器 healthy，`/workspace` 返回 200 并正常渲染。

---

## 验证边界

**已验证**：

- `npm run build` 生产构建成功
- `python -m tests.vue_browser_acceptance` 在正式库副本上跑真实 API，覆盖五页、真实登录、history 刷新、回跳、真实上传、建 queued 任务、分页、报告下载、HTML 转义、统一 401、越权 404、375px 布局
- Docker 多阶段构建成功，容器内 `/workspace` 可访问

**未验证 / 不代表**：

- 轮询 `running → completed` 及停止使用浏览器响应夹具，**不是实际模型执行**
- 未启动 worker，模型调用 0 次
- 原生版留作对照，**不把它的浏览器验收当成 Vue 已验证**

---

## 面试可直接讲的链路

> 路由守卫读取 Pinia 登录状态，未登录记录目标路径；登录接口成功后 Pinia 保存会话，再回目标页。
>
> 页面通过统一 `api` 取数，`api` 从 Pinia 加 Bearer，401 集中清理并跳登录。
>
> 详情页只负责看任务表的进度，**不在浏览器执行审查**。
>
> 结果页把数据传给 `RiskCard` 和 `StatBar`，报告把 Markdown 解析成安全节点。
>
> 共享状态只放用户，业务资源仍归各页面，后端保持最终权限与任务真相。
