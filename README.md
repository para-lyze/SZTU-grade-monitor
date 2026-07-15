# SZTU Grade Monitor

自动登录深圳技术大学教务系统，检测成绩汇总变化，并通过 QQ 邮箱发送通知。

> 本项目与深圳技术大学无隶属关系。请遵守学校系统的使用规则，并将部署仓库保持为 Private。

## 快速部署

1. 在 [Releases](https://github.com/para-lyze/SZTU-grade-monitor/releases) 下载最新的“一键部署包”。
2. 解压 ZIP，在 GitHub 创建一个新的 **Private** 仓库。
3. 上传解压后的全部文件，确认以下工作流路径存在：

   ```text
   .github/workflows/grade_monitor.yml
   ```

4. 打开私有仓库的 `Settings → Secrets and variables → Actions`，添加：

   | Secret | 用途 |
   | --- | --- |
   | `STU_ID` | 学号 |
   | `STU_PWD` | 教务系统密码 |
   | `MAIL_USER` | QQ 邮箱地址 |
   | `MAIL_PASS` | QQ 邮箱 SMTP 授权码 |
   | `MAIL_RECEIVER` | 接收通知的邮箱 |

5. 打开 `Actions → Private GPA Monitor → Run workflow`，手动运行一次。
6. 首次查询和邮件发送成功后，会收到“成绩监控启动成功”邮件；之后只在成绩汇总变化时通知。

## 从源码部署

公开源码仓库不会启用包含个人 Secrets 的定时工作流。请把
[`deploy/grade_monitor.yml`](deploy/grade_monitor.yml) 复制到你的私有部署仓库：

```text
.github/workflows/grade_monitor.yml
```

同时复制 `get_grades.py`、`requirements.txt` 和 `.gitignore`。不要把学号、密码、
邮箱授权码、`grade_history.json`、失败截图或教务页面 HTML 提交到公开仓库。

## 多账号

推荐每个教务账号使用一个独立的 Private 仓库。这样 Secrets、成绩历史和启动标记
完全隔离，也不会发生多个工作流同时提交状态的冲突。

## 本地运行

需要 Python 3.11 或更高版本，以及可用的 Chrome：

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python get_grades.py
```

运行前需要设置 `.env.example` 中列出的环境变量。凭据只从环境变量读取，不要写入代码。

## 开发与测试

```bash
python -m unittest discover -s tests -v
```

## 已知限制

- 当前版本检测课程数、总学分、累计 GPA 和专业排名变化，不提供逐门课程成绩。
- 新增课程加权绩点由四舍五入后的累计数据推算，仅供参考。
- 登录页面出现验证码、多因素认证或页面结构调整时，自动查询可能需要更新。

## License

[MIT](LICENSE)
