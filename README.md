# 吃什么 - 随机餐厅 & 点餐记录

一个基于 Flask + HTML/JS 的轻量 Web 应用，用来随机挑选餐厅，并记录实际点餐情况。适合部署在本地、NAS、树莓派或 Cloudflare Tunnel。

## 功能亮点

- 🎲 **按权重随机挑选**：支持中文排序与本地缓存排序偏好。
- 📋 **可视化商家管理**：在管理端直接增删改餐厅和权重。
- 🍱 **点餐记录**：记录日期/内容/价格/评分，方便回顾。
- 🌐 **公网访问选项**：内置 Cloudflare Tunnel 指南与 Docker Compose，一条命令就能共享到互联网。
- 🧰 **脚本与自动化**：`start_with_tunnel.sh` 同时拉起 Docker (主站) + 管理端，支持 tmux/nohup 持续运行。

## 项目结构

- `server.py`：主站后端（5000 端口）+ 静态页 `eat.html`，只读展示和随机抽取。
- `server_manage.py`：管理端后端（5001 端口）+ `eat_manage.html`，支持 CRUD 和点餐记录维护。
- `db.csv` / `db_meal.csv`：商家 & 点餐 CSV 数据文件。
- `start_with_tunnel.sh`：一键启动脚本（Docker + 管理端 + Cloudflare Tunnel）。
- `docker-compose.yml` / `Dockerfile`：容器化部署（含 cloudflared 服务）。
- `CLOUDFLARE_TUNNEL.md`：详细隧道配置教程。

## 本地快速开始

```bash
python -m venv .venv && source .venv/bin/activate  # 可选
pip install -r requirements.txt
python server.py                                   # 5000 端口
```

浏览器访问 `http://localhost:5000`，即可使用只读版页面（随机按钮 + 列表 + 点餐记录时间线）。

### 启动管理端

```bash
python server_manage.py   # 默认 5001 端口
```

打开 `http://localhost:5001`：
- 增删改餐厅与权重（实时写入 `db.csv`）
- 管理点餐记录 `db_meal.csv`
- 同样可以在页面里随机抽餐

> 需要远程管理可结合 Tailscale/VPN，把 `5001` 暴露在局域网或虚拟网络上。

## 数据存储

- 默认使用单文件 SQLite 数据库 `eat.db`，两张表：
  - `vendors(id, vendor, weight)`
  - `meals(id, date, order_text, price, rate, image)`
- 首次启动时如果表为空，会自动从旧版 `db.csv` / `db_meal.csv` 迁移一次数据。

## Docker & Cloudflare 部署

在启动前先准备 `.env`（已在 `.gitignore` 中，避免泄漏）并写入你的隧道 Token：

```bash
echo "CLOUDFLARE_TUNNEL_TOKEN=你的token" > .env
```

```bash
docker compose up -d
docker compose logs -f cloudflared   # 查公网 URL
```

栈内包含：
- `app`：基于 `Dockerfile` 构建的 Flask 服务（映射本地 `db*.csv` 到容器内）
- `cloudflared`：从环境变量 `CLOUDFLARE_TUNNEL_TOKEN` 读取隧道 Token 自动上线

如需自定义 `cloudflared` 行为，可将凭证、`config.yml` 映射进去或参照 [Cloudflare Tunnel 指南](CLOUDFLARE_TUNNEL.md)。

## 一键脚本：`start_with_tunnel.sh`

该脚本用于“主站走 Docker + Tunnel、公网访问，管理端跑在本机/局域网”这一常见需求：

1. 检查 Docker / docker compose
2. `docker compose up -d` 启动主站 + cloudflared
3. 选择本地 Python（优先 `.venv/bin/python`）运行 `server_manage.py`
4. 优先在 tmux session `eat-manage` 中运行，若无 tmux 则使用 nohup 写日志 `server_manage.log`

停止服务时记得执行 `docker compose down` 并关闭 tmux/后台进程。

## Cloudflare Tunnel

- 不想自己搜？一份完整、分场景的教程在 [`CLOUDFLARE_TUNNEL.md`](CLOUDFLARE_TUNNEL.md)。
- 包含快速临时隧道、持久化隧道、与 Docker Compose 集成、常见故障排查以及安全建议（Basic Auth、Access、Zero Trust）。

## 技术栈 & API

- 后端：Flask + flask-cors
- 前端：原生 HTML / CSS / JS
- 数据：CSV（便于备份与同步）
- 核心 API：
  - `GET /api/vendors` / `POST` / `PUT /<index>` / `DELETE /<index>`
  - `GET /api/meals` / `POST` / `PUT /<index>` / `DELETE /<index>`

欢迎根据自己的需求继续扩展，比如加 SQLite、鉴权、或更多统计页面。
