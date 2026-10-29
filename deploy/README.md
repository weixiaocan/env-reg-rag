# 服务器部署（演示版，带每日额度）

适用：一台装有 Docker（含 compose v2）和 HTTPS 反向代理的 Linux 服务器。服务器不需要访问 Docker Hub、PyPI 或 Hugging Face，镜像、向量库和资料都在部署包里。

腾讯云演示实例使用现有 Caddy，公网入口为 `https://weixiaocan.com/rag/`；语料为 `corpus-b6e4850ef830`（4263 points）。2026-10-29 应用镜像更新到提交 `78e8bb3` 的检索代码，Compose 实际运行镜像为 `env-reg-rag-app:b6e4850ef830-v8-78e8bb3`，前一应用镜像 `env-reg-rag-app:b6e4850ef830-v7-55df929` 保留供回退。公网 ready 五项检查和 G004 真实回答已验证；先前 G001/G009 在线烟测也已通过。Caddy 把 `/rag/*`（去掉 `/rag` 前缀）、`/api/v1/*` 和 `/documents` 转发到 RAG 容器；个人站的其他路径仍走原服务。RAG 容器通过单独的 Compose override 加入现有 Caddy Docker 网络，应用和 Qdrant 的宿主机端口仍只绑定 `127.0.0.1`。调整代理容器后应重新核对其内网 IP 与 `FORWARDED_ALLOW_IPS` 的精确配置。

## 1. 在开发机上打包

```bash
bash deploy/make_bundle.sh
```

生成 `dist/deploy-<语料版本>/`，包括：

- `images.tar.gz`：应用镜像（含嵌入模型）和 Qdrant 镜像
- `collection.snapshot`：当前正式语料的向量库快照
- `data.tar.gz`：`data/registry` 和 `data/raw`（原文 PDF，仅用于逐页显示原文页）
- `code.tar.gz`：当前提交的代码和 compose 文件
- `server_install.sh`、`SHA256SUMS`

部署包里不含 `.env`，密钥只在服务器上创建。

## 2. 上传并在服务器上配置 `.env`

```bash
scp -r dist/deploy-<语料版本> user@server:/tmp/
ssh user@server
sudo mkdir -p /opt/drainage-rag && cd /opt/drainage-rag
sudo nano .env        # 内容见下
sudo chmod 600 .env
```

启用外部模型回答时，`.env` 需要提供方配置和 Key。未配置模型时，提问会返回找到的原文并明确提示模型不可用。公网演示实例目前配置智谱 GLM-5.2；模型可用性须以在线烟测为准。`answer_and_citation` 证据可直接支持结论；`source_locator_only`（机器识别、未人工核对）也可支持结论，但对应结论标注"未核对"。

模型回答部署的示例配置：

```ini
LLM_PROVIDER=zhipu
LLM_MODEL=glm-5.2
ZAI_API_KEY=你的智谱 Key

APP_PROFILE=demo
PUBLIC_RATE_LIMIT_ENABLED=true
PUBLIC_ANSWER_PER_CLIENT_DAILY=10   # 每个访客每天 AI 回答次数
PUBLIC_ANSWER_DAILY_BUDGET=20       # 全站每天 AI 回答总次数（控制 token 费用的关键）
PUBLIC_ANSWER_PER_MINUTE=3
PUBLIC_SOURCE_LOOKUP_PER_MINUTE=30  # 只返回原文（额度用完后的兜底）不调用模型，不消耗 token
FORWARDED_ALLOW_IPS=172.19.0.5      # 示例：当前腾讯实例的 Caddy 容器内网 IP；其他主机需实测后替换
```

## 3. 安装

```bash
sudo bash /tmp/deploy-<语料版本>/server_install.sh /opt/drainage-rag
```

脚本会依次完成以下步骤，最后打印 `"status":"ready"`：

1. 校验文件；
2. 加载镜像；
3. 解压代码和资料；
4. 启动 Qdrant；
5. 恢复向量库，核对 point 数与语料指针一致，再以单次 Qdrant action 批次切换 `corpus_v2_live`；
6. 启动应用并等待就绪检查通过。

升级时重新打包上传，再运行同一个脚本即可。

## 4. HTTPS 反向代理

1. 独立 nginx 主机可把 `deploy/nginx-drainage-rag.conf` 复制到 `/etc/nginx/conf.d/`，把 `rag.example.com` 换成你的域名，再执行 `nginx -t && systemctl reload nginx`，并申请 HTTPS 证书。
2. 已有 Caddy 的共享主机应在现有站点配置中加专用路由，先验证 Caddy 到应用的私有网络连通性，再热加载配置。不要用 nginx 配置覆盖现有 80/443 服务。
3. 云服务器安全组只开放 80 和 443，8000 与 6333 不对外开放（compose 已经只绑定 127.0.0.1）。

## 5. 额度说明

- AI 回答受两层限制：每个访客每天 10 次，全站每天 20 次。额度用完后，提问自动改为只返回相关原文；这一路径和浏览资料不受每日限制。
- 计数在应用内存中，按 UTC 日期重置（北京时间 8:00）。重启容器也会清零。
- 应用内的计数不是严格的费用上限。请同时在智谱控制台设置余额提醒或消费上限。

## 6. 常用操作

```bash
cd /opt/drainage-rag
docker compose ps
docker compose logs --tail 100 app
curl -s http://127.0.0.1:8000/api/v1/ready
docker compose restart app     # 修改 .env 里的额度后执行
```
