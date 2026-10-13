# 服务器部署（演示版，带每日额度）

适用：一台装有 Docker（含 compose v2）和 nginx 的 Linux 服务器。服务器不需要访问 Docker Hub、PyPI 或 Hugging Face，镜像、向量库和资料都在部署包里。

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

`.env` 最少需要这些内容：

```ini
LLM_PROVIDER=zhipu
LLM_MODEL=glm-5.2
ZAI_API_KEY=你的智谱 Key

APP_PROFILE=demo
PUBLIC_RATE_LIMIT_ENABLED=true
PUBLIC_ANSWER_PER_CLIENT_DAILY=10   # 每个访客每天 AI 回答次数
PUBLIC_ANSWER_DAILY_BUDGET=20       # 全站每天 AI 回答总次数（控制 token 费用的关键）
PUBLIC_ANSWER_PER_MINUTE=3
PUBLIC_SOURCE_LOOKUP_PER_MINUTE=30  # "只找原文"不调用模型，不消耗 token
PUBLIC_DIAGNOSTIC_ROUTES_ENABLED=false
FORWARDED_ALLOW_IPS=*               # 应用端口只绑定 127.0.0.1，只有本机 nginx 能连
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
5. 恢复向量库，并把 `corpus_v2_live` 指过去；
6. 启动应用并等待就绪检查通过。

升级时重新打包上传，再运行同一个脚本即可。

## 4. nginx 和 HTTPS

1. 把 `deploy/nginx-drainage-rag.conf` 复制到 `/etc/nginx/conf.d/`，把 `rag.example.com` 换成你的域名，然后执行 `nginx -t && systemctl reload nginx`。
2. 申请证书：`certbot --nginx -d 你的域名`。
3. 云服务器安全组只开放 80 和 443，8000 与 6333 不对外开放（compose 已经只绑定 127.0.0.1）。

## 5. 额度说明

- AI 回答受两层限制：每个访客每天 10 次，全站每天 20 次。"只找原文"和浏览资料不受每日限制。
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
