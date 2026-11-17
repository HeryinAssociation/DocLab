# Doclab Book Archive

本地归档插件，当前打包目标为 Zotero 10.0.x。安装包位于 dist/doclab-archive.xpi，含本机桥接配置和密钥，不发布或发送给其他人。

## 打包

在 Doclab 根目录运行 `python tools/build_zotero_plugin.py`，仅重新生成 XPI，不重写 DSH 配置。首次准备整个工作区时使用 tools/setup_intake.py。

打包器依据本机 Zotero 的 Extension.sys.mjs 中额外清单要求检查必填字段，并检查 ZIP、包内根目录和 HTTPS 更新地址。缺少 update_url 会被 Zotero 视为无效扩展，即使版本范围看似正确。

## 更新方式

这是手动安装、手动升级的本地插件，目前没有部署自动更新服务器。Zotero 强制要求非空 update_url，同时更新安全检查要求 HTTPS，因此清单使用回环 HTTPS 地址 `https://127.0.0.1/doclab-archive/updates.json` 作为未启用的更新位置；该地址目前不提供更新服务，也不访问第三方服务器。手动检查更新可能显示连接失败，不影响本地安装或归档接口。没有更改 Zotero 的更新安全设置或放宽版本到未测试主版本。

## 0.1.1 修复

0.1.0 漏写 applications.zotero.update_url，导致安装器在启动 bootstrap 前拒收。0.1.1 补齐此字段，使用明确的 10.0.* 兼容范围，并增加打包前验证。

## 0.1.2 运行期修复

已在本机 Zotero 10.0.6 验证启动和带认证的 health 请求。旧版用 getContentsFromURLAsync 读取 jar: 压缩包资源，HTTP 模块访问 nsIURI.username 时抛 NS_ERROR_FAILURE，导致端点未注册；active/enabled 不代表 startup 成功。现改用 Zotero 自身插件加载器同样使用的 getResourceAsync，启动错误写入 Zotero 错误日志，日志不含配置或密钥。

排查时先查看“帮助 → 报告错误”的本地错误内容，不必发送报告。启动失败先修代码并重新构建，同一版本反复禁用、启用或重启不会消除错误。安装后以带认证的 health 返回插件版本作为加载证据。
