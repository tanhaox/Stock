# Stock Analyst 浏览器扩展 — 安装指南

## 适用浏览器

- Microsoft Edge (推荐)
- Google Chrome
- 任何基于 Chromium 的浏览器 (Brave, Arc, Opera 等)

## 安装步骤

### 1. 打开扩展管理页面

- **Edge**: 地址栏输入 `edge://extensions/`
- **Chrome**: 地址栏输入 `chrome://extensions/`

### 2. 开启开发者模式

页面左下角/右上角找到「开发人员模式」(Developer mode) 开关，**打开**。

### 3. 加载扩展

1. 点击「加载解压缩的扩展」(Load unpacked)
2. 选择文件夹: `C:\AI-Agent-Local\Stock\browser-extension\`
3. 确认后，扩展图标会出现在工具栏

### 4. 固定到工具栏（可选）

点击工具栏拼图图标 → 找到「Stock Analyst 反哺」→ 点击图钉固定。

## 使用方法

1. 打开 `https://chat.deepseek.com/`
2. 正常与 DeepSeek 对话（如"分析 000001.SZ 这只股票"）
3. 每条 AI 回复下方会自动出现 **「📤 发送到Stock」** 按钮
4. 点击按钮 → 弹出股票代码输入框（自动检测消息中的代码）
5. 确认代码后点击「发送」
6. 右下角显示绿色 Toast = 成功反哺到本地系统

## 前提条件

- Stock Analyst 后端必须已启动 (`StockAnalyst.bat`)
- 后端监听 `http://localhost:8000`

## 验证安装

点击工具栏扩展图标 → 弹窗显示后端连接状态：
- 🟢 后端在线 (v0.1.0) = 正常
- 🔴 后端离线 = 请先运行 StockAnalyst.bat

## 故障排查

| 症状 | 解决 |
|------|------|
| 按钮不出现 | 刷新 DeepSeek 页面，等待几秒 |
| 发送失败 | 确认后端已启动，检查 `http://localhost:8000/api/health` |
| 扩展图标不显示 | 检查扩展管理页面是否有错误提示 |
