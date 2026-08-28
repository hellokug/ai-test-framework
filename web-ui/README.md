# web-ui

当前交付的前端是**免构建 SPA**，随包直接分发在 `aite/web/static/`（`index.html` +
`app.js` + `style.css`），`aite serve` 打开即用，无 Node 依赖。

- 技术形态：Vue 3（global build）+ ECharts，经 CDN 引入；企业级样式手写（AntD 风格令牌）。
- 页面：Runs 列表 / Run 详情（case 表格 + 指标）/ 趋势（折线 + 雷达）/ 用例管理 /
  套件编辑（YAML）/ 模型管理 / 人工评测 / 任务 / 可选 Bearer 登录。

## 迁移到 Vite + TS + Ant Design Vue 工程的路径

当需要离线部署或深度定制时，把 `aite/web/static` 中的模板与逻辑搬入本目录的标准工程：

1. `npm create vite@latest . -- --template vue-ts`
2. `npm i ant-design-vue@4 echarts vue-echarts vue-router@4`
3. `index.html` 的各 `<section v-if="view==='…'">` 拆为 `src/views/*.vue` 组件，
   `app.js` 的 methods 按 view 拆成 composables（`src/api/*.ts` 放 fetch 封装）
4. `vite.config.ts` 设置 `build.outDir = '../aite/web/static'`，
   `base = '/static/'`，构建产物直接替换免构建版本
5. 服务端零改动：所有数据接口都在 `/api/v1`，SSE 通道 `/api/v1/tasks/{id}/stream`

离线运行免构建版的替代方案：将 Vue/ECharts 下载到 `aite/web/static/vendor/` 并把
`index.html` 的 CDN 引用改为本地路径。
