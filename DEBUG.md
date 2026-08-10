# GerOS 调试指南

本文档涵盖 GerOS 开发与运行中的常见问题诊断和解决方法。

---

## 目录

- [环境检查](#环境检查)
- [Node.js 诊断](#nodejs-诊断)
- [插件调试](#插件调试)
  - [架构概览](#架构概览)
  - [第一步：Node.js 子进程可用性](#第一步确认-nodejs-子进程能否被-python-拉起)
  - [第二步：JS 插件加载测试](#第二步确认-js-插件的-moduleexports-能否被-nodejs-正常-require)
  - [第三步：子进程通信排查](#第三步排查-python-子进程通信)
  - [第四步：临时 Mock 数据](#第四步临时-mock-数据绕过后端)
  - [待办清单](#当前待办清单)
- [GUI 问题](#gui-问题)
- [音效问题](#音效问题)
- [打包问题](#打包问题)

---

> 注：本文档以 `node-v24.14.0-win-x64` 为例，请根据项目根目录 `nodejs/` 下的实际文件夹名称替换。

---

## 环境检查

### 快速自检

运行环境诊断脚本：

```bash
python scripts/tests/_test_env.py
```

示例输出：

```
=== 环境检查 ===
tkinter: OK
Pillow:  OK (12.3.0)
psutil:  OK (7.2.2)
Node.js: OK (内建 v24.14.0)
  npm/axios:      OK
  npm/cheerio:    OK
  npm/he:         OK
  npm/crypto-js:  OK
  npm/webdav:     OK
  npm/dayjs:      OK
=== 环境就绪 ===
```

### 手动检查

| 组件 | 检查命令 |
|------|---------|
| Python 版本 | `python --version` |
| tkinter | `python -c "import tkinter; print('OK')"` |
| Pillow | `python -c "from PIL import Image; print(Image.__version__)"` |
| psutil | `python -c "import psutil; print(psutil.__version__)"` |

---

## Node.js 诊断

### 问题：JS 插件不可用 / "Node.js 环境初始化失败"

**步骤 1 — 检查 Node.js 目录结构**

确保以下路径存在：

```
项目根目录/
└── nodejs/
    └── node-v24.14.0-win-x64/
        ├── node.exe
        └── node_modules/
            ├── axios/
            ├── cheerio/
            ├── cheerio-select/
            ├── crypto-js/
            ├── dayjs/
            ├── he/
            └── webdav/
```

**步骤 2 — 解压 Node.js**

如果 `nodejs/` 目录不存在，将 `node.zip` 解压到项目根目录：

```bash
# 使用 PowerShell
Expand-Archive -Path node.zip -DestinationPath .
```

**步骤 3 — 安装 npm 依赖**

```bash
# 进入 Node.js 目录
cd nodejs\node-v24.14.0-win-x64

# 安装所需包
npm install axios cheerio he crypto-js webdav dayjs
```

或运行自动安装脚本：

```bash
python scripts/setup/_install_deps3.py
```

**步骤 4 — 验证 Node.js 可执行**

```bash
# 进入 Node.js 目录执行
cd nodejs\node-v24.14.0-win-x64
node.exe -e "console.log('Node.js OK')"
```

### 问题：NODE_PATH 环境变量不生效

在 Node.js v24.x 中，`NODE_PATH` 仍然有效但请确保路径指向正确的 `node_modules` 目录。

程序代码中通过 `NodeEnv.get_node_modules_path()` 自动获取，无需手动设置。

### 诊断脚本

```bash
# 运行插件诊断
python scripts/diagnostics/_diag_plugins.py
```

---

## 插件调试

> **当前状态**：核心通信桥接尚未闭环，插件调用暂不可用。以下为开发调试清单。

### 架构概览

必须清楚的调用链（任何一个环节断掉，插件就会表现为「没反应」或「闪退」）：

```
GUI (搜索框输入)
   → Python 主线程 (事件绑定)
      → 子进程调用 (subprocess.Popen)
         → node.exe 执行 JS 插件
            → 网络请求 (axios)
               → 返回 JSON 数据
                  → 子进程 stdout 捕获
                     → Python 解析 → 展示到 Listbox
```

### JSON 插件

JSON 插件是最简单的形式，包含以下字段：

```json
{
  "name": "示例音乐源",
  "url": "https://api.example.com",
  "search": "/search?keyword={keyword}",
  "song": "/song?id={id}",
  "lyric": "/lyric?id={id}"
}
```

调试方法：
- 直接在浏览器中访问 API 端点确认返回格式
- 检查 JSON 语法是否正确（使用 jsonlint.com）

### JS 插件

JS 插件遵循 MusicFree 协议，模板结构：

```javascript
module.exports = {
  platform: "示例音源",
  version: "1.0.0",
  
  // 搜索
  search: async function(query, page, type) {
    // 返回 { isEnd: bool, data: [...] }
  },
  
  // 获取歌曲详情
  getMusicInfo: async function(songItem) {
    // 返回 { url: string, ... }
  },
  
  // 获取歌词
  getLyric: async function(songItem) {
    // 返回 { lyric: string, ... }
  }
};
```

### 第一步：确认 Node.js 子进程能否被 Python 拉起

在 `Psystem_GerOS_V0.5.py` 中临时加一个测试函数，确认 Python 能正确找到 `node.exe`：

```python
import subprocess, os, sys

def test_node_availability():
    # 打包后路径处理
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    
    node_path = os.path.join(base_path, "nodejs", "node-v24.14.0-win-x64", "node.exe")
    if not os.path.exists(node_path):
        print(f"[错误] 找不到 node.exe: {node_path}")
        return False
    
    result = subprocess.run([node_path, "-e", "console.log('hello')"], 
                            capture_output=True, text=True, timeout=5)
    print(f"Node.js 测试输出: {result.stdout}")
    return result.returncode == 0
```

如果这里返回 `False`：说明 `build.spec` 的 `datas` 没把 `nodejs` 目录打进去，或者相对路径写错了。

### 第二步：确认 JS 插件的 module.exports 能否被 Node.js 正常 require

在插件目录放一个测试脚本 `_test_loader.js`：

```javascript
// 放在 nodejs/node-v24.14.0-win-x64/ 下运行
const pluginPath = '../../../plugins/mf_plugin_XXXXXXXXXX.js'; // 改成你的插件路径
try {
    const plugin = require(pluginPath);
    console.log('插件加载成功，导出的方法:', Object.keys(plugin));
    // 测试 search 方法是否存在
    if (typeof plugin.search === 'function') {
        console.log('search 方法存在 ✓');
    } else {
        console.log('search 方法缺失 ✗ (插件协议不完整)');
    }
} catch (e) {
    console.error('加载失败:', e.message);
}
```

### 第三步：排查 Python 子进程通信

**最常见卡点**。Python 端调用 JS 插件的模板代码（需封装为工具类）：

```python
def call_plugin(plugin_name, method, params):
    node_path = get_node_path()  # 获取 node.exe 绝对路径
    js_code = f"""
    const plugin = require('{plugin_name}');
    plugin['{method}']({params}).then(result => {{
        console.log(JSON.stringify(result));
    }}).catch(err => {{
        console.error(JSON.stringify({{error: err.message}}));
    }});
    """
    # 注意：必须用 -e 参数执行字符串代码，且通信只能用 stdout
    proc = subprocess.Popen(
        [node_path, "-e", js_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8'
    )
    stdout, stderr = proc.communicate(timeout=10)
    return stdout, stderr
```

**常见报错排查**：

| 现象 | 可能原因 | 解决办法 |
|------|----------|----------|
| `subprocess` 超时无返回 | JS 插件内 axios 网络请求未设置超时 | 在 JS 中加 `timeout: 5000` |
| 返回 `{error: "Cannot find module"}` | Node.js 的 require 路径不是绝对路径 | 在 JS 代码中用 `__dirname` 拼接绝对路径 |
| 返回中文乱码 | Windows 控制台编码问题 | `Popen` 加上 `encoding='utf-8'` |
| Python 界面卡死 | 在 Tkinter 主线程中阻塞调用了子进程 | 必须用 `threading` 或 `after` 异步执行 |

### 第四步：临时 Mock 数据绕过后端

如果暂时不想调通 Node.js，可以先在 Python 端硬编码返回假数据，让 UI 先跑起来：

```python
def mock_search(keyword):
    return [
        {"title": f"测试歌曲_{keyword}_1", "artist": "虚拟歌手", "id": "001"},
        {"title": f"测试歌曲_{keyword}_2", "artist": "虚拟歌手", "id": "002"},
    ]
```

将搜索按钮的回调暂时指向这个 mock 函数，待插件链路稳定后再切换回去。

### 当前待办清单

- [ ] 打通 Python → Node.js 的路径查找逻辑（兼容开发环境与打包 EXE）
- [ ] 实现 Node.js 端统一的插件加载器（加载 `plugins/` 下所有 `.js` 文件）
- [ ] 规范 Python 与 Node.js 的通信协议（JSON 序列化 / 反序列化）
- [ ] 将子进程调用放到单独的 `QThread` 或 `threading.Thread` 中，防止 UI 卡顿
- [ ] 编写至少一个完整的测试插件（包含 `search`、`getMusicInfo`、`getLyric` 三个方法）

---

## GUI 问题

### 启动时闪退

可能是 Python import 错误，在命令行中运行查看具体错误信息：

```bash
python Psystem_GerOS_V0.5.py
```

### 界面显示异常

- 确认 `system.png` 文件存在（系统背景图，约 3MB）
- 检查 `logo.jpg` 是否损坏（程序图标）

### 高 DPI 显示模糊/过小

**原因**：Windows 默认对非 DPI 感知的应用使用位图缩放，导致界面模糊或字体过小。

**解决方案**：在 `Psystem_GerOS_V0.5.py` 的 `if __name__ == "__main__":` 最顶部（`import tkinter` 之前）添加 Windows DPI 感知声明：

```python
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)  # 启用系统 DPI 感知
except Exception:
    pass
```

### 窗口无响应

- 如果音乐播放卡死，可能是网络请求超时
- 切换音源插件或重启程序

---

## 音效问题

### 无声音

1. 确认 `Ring10.wav` 和 `Windows Logoff Sound.wav` 存在
2. 检查系统音量合成器中 Python 是否被静音
3. 打开 GerOS 设置确认音量滑块 > 0

### 指定自定义音效

替换项目根目录下的同名文件即可：
- 启动音效：替换 `Ring10.wav`
- 关机音效：替换 `Windows Logoff Sound.wav`

> 格式需为 **PCM 编码的 WAV**（标准 16bit/44.1kHz）。如果播放有杂音或无声，请用 Audacity 将音频重采样为 `Signed 16-bit PCM` 格式导出。

---

## 打包问题

### PyInstaller 打包

```bash
pip install pyinstaller
pyinstaller build.spec
```

### 常见打包错误

**错误：`ModuleNotFoundError: No module named 'PIL'`**
```
# 在 build.spec 中确保 hiddenimports 包含：
hiddenimports=["PIL", "PIL.Image", "PIL.ImageTk", "PIL.ImageDraw", "psutil"]
```

**错误：打包后图片/音效找不到**
```
# build.spec 的 datas 列表必须包含所有资源文件：
datas=[
    ("Ring10.wav", "."),
    ("Windows Logoff Sound.wav", "."),
    ("logo.jpg", "."),
    ("system.png", "."),
    ("zh.jpg", "."),
    ("Ger壁纸推荐", "Ger壁纸推荐"),
]
```

**错误：打包体积过大 (>100MB)**
```
# 在 excludes 中排除不需要的库：
excludes=["matplotlib", "numpy", "scipy", "pandas", "PyQt5", "PyQt6"]
```

### 错误：打包后 Node.js 无法启动（JS 插件失效）

**原因**：PyInstaller 打包后，`sys.executable` 指向 EXE 的临时解压路径，`./nodejs/` 目录不会被自动复制到解压目录。

**解决方案**：在 `build.spec` 中，必须将 `nodejs` 整个目录作为 **binaries**（或 datas）包含进去，并在代码中动态获取 `sys._MEIPASS` 路径拼接 `node.exe`。

```python
# build.spec 中确保：
binaries=[('nodejs/node-v24.14.0-win-x64/node.exe', 'nodejs/node-v24.14.0-win-x64')]
datas=[('nodejs/node-v24.14.0-win-x64/node_modules', 'nodejs/node-v24.14.0-win-x64/node_modules')]
```

同时在主程序中修改 `app_dir()` 函数或 `NodeEnv._find_extracted_node()`，使其在打包模式下使用 `sys._MEIPASS`：

```python
# 在 app_dir() 或相关位置：
import sys
def resource_path(relative_path):
    """获取资源绝对路径，兼容开发环境和 PyInstaller 打包"""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)
```

> 由于 Node.js 运行时约 34MB，如果不需要 JS 插件支持，可以移除 `nodejs/` 目录后重新打包，EXE 体积会减小约 30%，JSON 插件仍可正常使用。

---

## 其他诊断工具

| 脚本 | 用途 |
|------|------|
| `scripts/tests/_test_env.py` | 环境完整性检查 |
| `scripts/tests/_test_e2e.py` | 端到端功能测试 |
| `scripts/tests/_test_qq.py` | QQ音乐插件专项测试 |
| `scripts/diagnostics/_diag_plugins.py` | 插件系统诊断 |
| `scripts/diagnostics/_fix.py` | 通用修复脚本 |
| `scripts/setup/_install_deps3.py` | 一键安装所有依赖 |

---

## 获取帮助

- 提交 Issue 时请附上 `_test_env.py` 的运行输出
- 附上相关错误日志/截图有助于快速定位问题
