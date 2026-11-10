# 故事小岛：轻量 GitHub Pages 发布包（备用方案）

这是公开、非商业学习使用的静态英语绘本阅读站，包含 362 本绘本、40 本真人朗读、77 本中英对照。学习记录留在访问者浏览器中，发布文件不含学习记录、录音或远程凭据。

## 发布文件

将本目录的 5 个文件上传到独立仓库根目录，并将配套 `github-pages-fetch.yml` 保存为 `.github/workflows/deploy.yml`。Pages 发布来源选择 GitHub Actions。

- `site-frontend.zip`：页面、样式、字体、图标及绘本数据。
- `site-manifest.json`：前端包及每个运行文件的大小与 SHA256 清单。
- `restore-site.py`：仅用 Python 标准库恢复并核验站点。
- `README.md`、`LICENSE`：说明和代码许可。

工作流从 GitHub 图片仓库 `global-asp/asp-imagebank` 的固定提交 `a56e6dbe7aeeb8ea3e53d9941664416e2e7356ad` 下载一次 ZIP，仅读取所需的 4,207 张 `medium` 图片；420 段 MP3 来自 `https://storybookscanada.ca/audio/en/<id>/mp3/<NN.mp3>`。音频使用 12 个并发下载，超时 45 秒，最多尝试 3 次，间隔 1、2 秒。每个文件均核对大小与 SHA256，任何缺失或不一致都会阻止发布，不会用未知资源替换。

## 手动更新

1. 在本地完成页面改动，并将 `app/` 的成品同步到 `site/`。
2. 重新生成 `site-frontend.zip`：ZIP 根级须保持 `index.html`、`assets/`、`data/`，排除 `books/`、使用说明和系统隐藏文件。
3. 重新核对前端包 CRC；用当前 `site/` 为所有前端文件及 `books/` 媒体生成 `path`、`bytes`、`sha256`。更新清单中的前端 ZIP 大小、SHA256、前端文件列表和站点总文件数/字节数。媒体变动时也必须更新媒体清单，并确认指定上游版本仍包含对应图片。
4. 在空目录运行 `python3 restore-site.py --out /tmp/story-island-check --media-from /本地/site/books`，逐文件核对与本地 `site/` 一致，再提交更新后的 ZIP 和清单。

本方案需要构建服务器可访问指定图片与音频源。完整本地媒体分包仍可作为不依赖远程恢复的备用发布资源。

代码基于 [english-bookshelf](https://github.com/vickysy/english-bookshelf)，界面采用 [Animal Island UI](https://github.com/guokaigdg/animal-island-ui) 设计和资源。UI、图标、字体许可随站点保留；绘本文字、插图、朗读保留各自署名。含 CC BY-NC 绘本，仅供非商业学习，各书来源与许可见阅读页。
