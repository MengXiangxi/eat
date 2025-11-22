# Cloudflare Tunnel 配置指南

本指南汇总了把“吃什么”部署到公网的几种方式：快速试用、持久化隧道、Docker Compose 集成以及仓库自带的 `start_with_tunnel.sh`。主站 (`server.py` / `5000`) 会通过 Cloudflare 暴露到互联网；管理端 (`server_manage.py` / `5001`) 默认只在局域网/Tailscale 内访问，更安全也更易维护。

---

## 应用拓扑速览

- `server.py` (5000)：只读页面 `eat.html`，给任何人使用。
- `server_manage.py` (5001)：`eat_manage.html`，包含增删改和点餐记录，请仅在可信网络中访问。
- `cloudflared`：把 5000 端口映射到 Cloudflare，全局可访问。

---

## 方法一：快速临时隧道（无需账号）

适合分享几分钟/几小时，或临时演示。

1. 安装 `cloudflared`
   - Windows：`winget install --id Cloudflare.cloudflared`
   - macOS：`brew install cloudflared`
   - Linux：下载对应包或参考 <https://pkg.cloudflare.com/>
2. 启动主站
   ```bash
   python server.py
   # 如需在外网管理，可另起终端运行 python server_manage.py 暴露 5001（建议配合 Tailscale）
   ```
3. 运行临时隧道
   ```bash
   cloudflared tunnel --url http://localhost:5000
   ```
4. 终端会打印一个 `https://<随机>.trycloudflare.com`，把它分享出去即可。

> 临时隧道在 `cloudflared` 进程结束后立即失效，每次重启都会获得新的 URL。

---

## 方法二：持久化隧道（Cloudflare 账号）

获得固定域名，适合常驻部署。

1. **登录**：`cloudflared tunnel login`
2. **创建隧道**：`cloudflared tunnel create eat-app`
3. **准备 `config.yml`**（仓库已提供空文件，可直接复用）：
   ```yaml
   tunnel: <隧道ID>
   credentials-file: /path/to/<隧道ID>.json

   ingress:
     - hostname: eat.yourdomain.com  # 或 eat-app.trycloudflare.com
       service: http://localhost:5000
     - service: http_status:404
   ```
4. **配置 DNS**：
   ```bash
   cloudflared tunnel route dns eat-app eat.yourdomain.com
   ```
5. **运行**：
   ```bash
   python server.py
   cloudflared tunnel run eat-app
   ```

> 如果要通过 Cloudflare 暴露管理端，可在 `ingress` 中再添加一条指向 `http://localhost:5001` 的记录，但务必配合 Basic Auth / Cloudflare Access。

---

## 方法三：Docker Compose（推荐）

仓库内的 `docker-compose.yml` 已配置好 Flask + Cloudflare Tunnel，只需替换为你自己的 `tunnel --token <TOKEN>` 即可使用。

```yaml
services:
  app:
    build: .
    ports:
      - "5000:5000"
    volumes:
      - ./db.csv:/app/db.csv
      - ./db_meal.csv:/app/db_meal.csv
    restart: unless-stopped

  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --no-autoupdate run --token <你的TunnelToken>
    depends_on:
      - app
    restart: unless-stopped
```

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
COPY eat.html .
EXPOSE 5000
CMD ["python", "server.py"]
```

运行：

```bash
docker compose up -d
docker compose logs -f cloudflared   # 查看公网 URL
```

管理端仍然通过 `python server_manage.py` 在宿主机或其他机器上启动，结合 Tailscale/VPN 即可安全访问。

---

## 方法四：`start_with_tunnel.sh` 一键脚本

适合 NAS/树莓派等场景，把所有步骤打包起来：

1. **准备**：确保安装 Docker（含 compose 插件）和 Python；如果想在 tmux 中托管，预先安装 `tmux`。
2. **运行脚本**
   ```bash
   ./start_with_tunnel.sh
   ```
3. **脚本做的事**
   - 检查 Docker → `docker compose up -d`（主站 + cloudflared）
   - 选择 Python 解释器（优先 `.venv/bin/python`）运行 `server_manage.py`
   - 优先启动 tmux session `eat-manage`；无 tmux 则使用 `nohup` 并写 `server_manage.log`

主站通过 Cloudflare 提供公网地址；管理端留在 5001（本地或 Tailscale IP）。停止服务时执行 `docker compose down` 并关闭 tmux/后台进程。

---

## 安全建议

1. **至少添加 Basic Auth**  
   在 `server.py` / `server_manage.py` 中加入 Flask 的基本认证，或使用反向代理做认证。
2. **Cloudflare Access / Zero Trust**  
   在 Cloudflare 仪表盘中为 `eat.yourdomain.com` 添加邮箱验证、MFA、OAuth 等策略。
3. **最小暴露面**  
   - 只把 5000（只读界面）暴露出去。  
   - 5001 管理端通过 Tailscale、WireGuard 或内网访问。
4. **config.yml 其他选项**  
   ```yaml
   no-autoupdate: true
   grace-period: 30s
   warp-routing:
     enabled: true
   ```

---

## 故障排查

| 现象 | 排查思路 |
| --- | --- |
| cloudflared 启动后打不开 | `curl http://localhost:5000` 确认服务运行；检查本地防火墙；查看 `cloudflared` 日志 |
| URL 打开慢或 502 | 等待 DNS 生效；确认 `ingress` 配置正确；观察 Cloudflare Zero Trust 仪表盘 |
| 前端请求仍指向 localhost | 确保 `eat.html` / `eat_manage.html` 使用 `const API_URL = window.location.origin + '/api'` |
| 隧道随机掉线 | 切换为持久化隧道；确保没有多实例争夺同一 token；使用 `no-autoupdate` |

---

## 开机自启动（可选）

- **Windows**：`cloudflared service install`
- **Linux / systemd**：`sudo cloudflared service install && sudo systemctl enable --now cloudflared`
- **macOS**：`sudo cloudflared service install`

> Docker 部署可改为 systemd/cron 启动 `docker compose up -d`，或使用容器编排平台。

---

## 进一步阅读

- [Cloudflare Tunnel 官方文档](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)
- [cloudflared GitHub](https://github.com/cloudflare/cloudflared)
- [Cloudflare Zero Trust](https://www.cloudflare.com/zero-trust/)
- [Tailscale](https://tailscale.com/) / [WireGuard](https://www.wireguard.com/)（管理端访问推荐工具）
