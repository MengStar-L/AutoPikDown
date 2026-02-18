# AutoPikDown

PikPak 磁力/分享链接自动下载工具 —— 通过 Web UI 添加磁力链接或 PikPak 分享链接，自动离线下载并推送到 Aria2。

## 功能特性

- 🔗 **磁力链接下载**：批量添加磁力链接，自动通过 PikPak 离线下载并推送到 Aria2
- 📦 **分享链接支持**：解析 PikPak 分享链接，选择文件下载
- 📡 **RSS 订阅下载**：解析 RSS 订阅链接，提取磁力/种子链接，选择性批量下载
- 📊 **实时进度**：WebSocket 实时推送处理进度
- 📋 **离线任务管理**：查看和管理 PikPak 离线下载任务
- 🔌 **连接测试**：一键测试 PikPak 和 Aria2 连接状态
- 👑 **会员状态**：显示 PikPak 会员状态和存储空间
- 🗑️ **自动清理**：下载完成后可自动删除 PikPak 中的文件
- ⚙️ **Web 配置**：所有配置通过 Web 界面管理

## 部署步骤

### 1. 下载项目

```bash
git clone https://github.com/MengStar-L/AutoPikDown.git /opt/AutoPikDown
```

### 2. 创建虚拟环境并安装依赖

```bash
cd /opt/AutoPikDown
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 创建配置文件

```bash
cp /opt/AutoPikDown/config.example.yaml /opt/AutoPikDown/config.yaml
```

编辑配置文件：

```bash
nano /opt/AutoPikDown/config.yaml
```

填入你的信息：

```yaml
pikpak:
  username: "your_email@example.com"
  password: "your_password"
  save_dir: "/"                        # PikPak 离线保存目录
  delete_after_download: true          # 推送 Aria2 后是否删除 PikPak 文件

aria2:
  rpc_url: "http://localhost:6800/jsonrpc"
  rpc_secret: "your_aria2_secret"
  download_dir: ""                     # Aria2 下载目录 (留空使用 Aria2 默认)

task:
  poll_interval: 3                     # 离线任务轮询间隔 (秒)
  max_wait_time: 3600                  # 离线任务最大等待时间 (秒)
```

### 4. 首次运行

```bash
source /opt/AutoPikDown/venv/bin/activate
cd /opt/AutoPikDown
python main.py
```

访问 `http://localhost:8888` 打开 Web 界面。

### 5. 注册为系统服务（开机自启）

复制服务文件：

```bash
cp /opt/AutoPikDown/autopikdown.service /etc/systemd/system/
```

启用并启动服务：

```bash
systemctl daemon-reload
systemctl enable --now autopikdown
```

### 6. 确认运行状态

```bash
systemctl status autopikdown
```

看到 `active (running)` 即表示部署成功 ✅

## 常用命令

```bash
# 查看实时日志
journalctl -u autopikdown -f

# 重启服务
systemctl restart autopikdown

# 停止服务
systemctl stop autopikdown
```

## License

MIT
