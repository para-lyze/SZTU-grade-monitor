# SZTU Grade Monitor

自动登录深圳技术大学教务系统，检测成绩汇总变化，并通过 QQ 邮箱发送提醒。

> [!IMPORTANT]
> 本项目与深圳技术大学无隶属关系。部署仓库必须设为 **Private**，以避免隐私泄露。

## 功能说明

- 首次成功运行后发送“成绩监控启动成功”邮件，方便确认部署有效。
- 后续检测到课程数、总学分、累计 GPA 或专业排名变化时发送邮件。
- 没有变化时不会发邮件，可在 GitHub Actions 页面查看每次运行记录。
- 新增课程绩点根据四舍五入后的累计数据推算，仅供参考，不代表教务系统最终结果。

## 一、下载并上传文件

1. 在 [Releases](https://github.com/para-lyze/SZTU-grade-monitor/releases) 下载最新的“一键部署包”。
2. 解压 ZIP，在 GitHub 创建一个新的 **Private** 仓库。
3. 将解压后的全部文件上传到这个私有仓库。
4. 确认仓库中至少包含：

   ```text
   get_grades.py
   requirements.txt
   .github/workflows/grade_monitor.yml
   ```

一键部署包已经包含正确的 `.github/workflows/grade_monitor.yml`，请保留原有目录结构，不要把
`grade_monitor.yml` 单独移动到仓库根目录。上传完成后，可以在私有仓库的 `Code` 页面逐层打开
`.github → workflows → grade_monitor.yml`，确认文件确实存在。

> [!WARNING]
> GitHub 只会识别 `.github/workflows/` 目录中的工作流。以下路径均不会生效：
> `grade_monitor.yml`、`.github/grade_monitor.yml`、`.github/workflow/grade_monitor.yml`。
> 注意正确目录是复数形式的 `workflows`。

`.github` 是以点开头的目录，部分系统会将它隐藏。如果解压后看不到该目录，请先在文件管理器中开启
“显示隐藏文件”，不要只上传看到的 `get_grades.py` 和 `requirements.txt`。

如果上传后发现工作流文件缺失，请在 GitHub 网页选择 `Add file → Create new file`，将完整路径
`.github/workflows/grade_monitor.yml` 填入文件名，再复制一键部署包中同名文件的全部内容并提交。

## 二、获取 QQ 邮箱 SMTP 授权码

`MAIL_PASS` 需要填写 QQ 邮箱生成的 **SMTP 授权码**，不能填写 QQ 密码或邮箱登录密码。

1. 使用电脑浏览器登录 [QQ 邮箱](https://mail.qq.com/)。
2. 打开 `设置 → 账户`。新版页面的名称可能略有不同，请查找“账户与安全”或“邮箱绑定/服务”相关设置。
3. 找到 `POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV 服务`。
4. 开启 `POP3/SMTP 服务` 或 `IMAP/SMTP 服务`；只要包含 SMTP 即可。
5. 按页面提示完成扫码、短信或其他安全验证。
6. 复制页面生成的授权码，并妥善保存。授权码可能只完整显示一次。

如果页面中找不到相关服务，建议使用电脑网页版 QQ 邮箱，并检查账号是否完成安全验证。也可以参考
[Microsoft 的 QQMail 配置说明](https://support.microsoft.com/zh-cn/office/%E5%B0%86-qqmail-%E5%B8%90%E6%88%B7%E6%B7%BB%E5%8A%A0%E5%88%B0-outlook-34ef1254-0d07-405a-856f-0409c7c905eb)
或[华为官方说明](https://consumer.huawei.com/cn/support/content/zh-cn15872097/)。QQ 邮箱界面更新后，按钮名称可能发生变化。

## 三、配置 GitHub Secrets

进入私有部署仓库，依次打开：

```text
Settings → Secrets and variables → Actions → New repository secret
```

逐个添加以下 5 个 Secret，名称区分大小写：

| Name | Value | 说明 |
| --- | --- | --- |
| `STU_ID` | 你的学号 | 教务系统账号 |
| `STU_PWD` | 你的教务系统密码 | 教务系统密码 |
| `MAIL_USER` | `你的QQ号@qq.com` | 发信邮箱 |
| `MAIL_PASS` | QQ 邮箱生成的授权码 | 不是 QQ 密码 |
| `MAIL_RECEIVER` | 接收提醒的邮箱 | 可以与 `MAIL_USER` 相同 |

Secrets 保存后不能再次查看明文，这是正常现象。若填错，请编辑并重新填写。

## 四、启用并测试自动任务

1. 打开私有仓库顶部的 `Actions`。
2. 在左侧选择 `Private GPA Monitor`。如果出现禁用提示，点击 `Enable workflow`。
3. 点击 `Run workflow → Run workflow` 手动运行一次。
4. 等待运行完成。绿色对勾表示成功，红色叉号表示失败，可点击该次运行查看日志。
5. 首次查询和邮件发送成功后，你会收到“成绩监控启动成功”邮件，仓库中也会生成私有状态文件 `grade_history.json`。

之后没有成绩变化时不会重复发邮件。判断自动任务是否正常，应查看 Actions 中是否出现新的绿色运行记录，而不是只看邮箱。

## 定时运行时间

默认在北京时间每天 **08:17–23:17** 之间每小时运行一次，即 08:17、09:17、……、23:17。

> [!NOTE]
> 上述时间是 GitHub Actions 的计划触发时间，并非严格准点保证。平台繁忙时任务可能延迟几十分钟，
> 极少数计划可能被跳过；这通常不代表部署或脚本配置失败。需要严格准点运行时，应使用外部定时器调用
> `workflow_dispatch`。

## 从源码部署

公开源码仓库不会直接启用个人成绩监控，因此源码中的工作流模板存放在 `deploy` 目录。若不使用
Release 一键部署包，请按照下表复制到自己的私有仓库：

| 公开源码中的文件 | 私有部署仓库中的位置 |
| --- | --- |
| `get_grades.py` | `get_grades.py` |
| `requirements.txt` | `requirements.txt` |
| `.gitignore` | `.gitignore` |
| [`deploy/grade_monitor.yml`](https://github.com/para-lyze/SZTU-grade-monitor/blob/main/deploy/grade_monitor.yml) | `.github/workflows/grade_monitor.yml` |

最后一项不是原样放进 `deploy` 目录，而是必须保存到私有仓库的
`.github/workflows/grade_monitor.yml`。提交后打开私有仓库的 `Actions` 页面，左侧出现
`Private GPA Monitor`，才说明 GitHub 已正确识别该文件。



## 本地运行

需要 Python 3.11 或更高版本，以及可用的 Chrome：

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python get_grades.py
```

运行前设置 `.env.example` 中列出的环境变量。凭据只从环境变量读取，不要写入代码。

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
