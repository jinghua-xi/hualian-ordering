# SSH 连接修复记录（2026-11-07）

## 已查明的断开机制

公网 SSH 入口为 `cpod-1xdwqzrhaexo.podtcp.compshare.cn:29134`，认证成功后的 `SSH_CONNECTION` 确认实例内部服务端口为 **23**。此前把 22 端口当成这条通道的推断不正确；临时的 22 端口诊断进程已移除。

诊断时，23 端口的 SSH 主进程显示 `21 of 10-100 startups`，存在大量尚未认证的连接。默认 `MaxStartups 10:30:100` 会从 10 个未认证连接开始随机拒绝新连接。这与本机记录的“TCP 已连接、尚未进入密钥认证就被关闭”一致。参数含义见 [Ubuntu OpenSSH 8.9 配置手册](https://manpages.ubuntu.com/manpages/jammy/man5/sshd_config.5.html)。

这些未认证连接的具体来源未确认，不能将其全部认定为攻击。实例内大部分连接显示为平台转发地址，因此没有按该地址封禁，以免误封正常用户。

## 服务端修复

在原 `/etc/ssh/sshd_config` 顶部加入带标记的小段配置：

```text
# BEGIN ORDERING CONNECTION LIMITS
LoginGraceTime 10
MaxAuthTries 3
# END ORDERING CONNECTION LIMITS
```

只收紧未认证连接占用时间和尝试次数。原端口 23、密码登录、密钥登录、主机密钥、授权公钥和 `MaxStartups` 上限均保留。先保存原配置到实例的 `/workspace/ordering-preview/backups/`，通过 `sshd -t` 校验后热加载；网站与已认证连接无需重启。以后实例启动时沿用原服务配置，没有新增 SSH 开机监听或公网端口。

## 本地部署工具修复

`./deploy` 先建立一条经过固定主机公钥校验的 SSH 主连接，SCP 上传、远端发布和清理复用同一条已认证连接。控制套接字位于本机私有短路径临时目录，结束后主动关闭连接。

只对首次握手做最多三次重试。权限错误、主机公钥不匹配立即停止；远端部署命令不会自动重放，防止连接异常后重复执行修改。

账号、订单库、SMTP 配置和 Qwen 模型继续留在服务器，上传范围没有扩大。

## 验证

离线测试覆盖：首次临时断开后重试、主机身份错误不重试、远端命令不重放、上传/执行/清理共用一个套接字并最终关闭。已完成一次真实 `./deploy`：首次认证即成功，上传、数据库副本预检、备份、重启、远端功能检查与公网检查全部通过。发布编号为 `20261107T151459Z-6d7ff61e`。关闭部署主连接后，再做5次不带自动重试的独立SSH登录，全部成功，返回的实例内部端口均为23。

没有为这次验证创建线上订单或发送测试邮件。未做整机断电重启测试；已验证原SSH服务热加载和独立重连，配置已写入原服务文件。

## 配置回退方法

原始配置备份为实例内 `/workspace/ordering-preview/backups/sshd-config-20261107T150830Z.conf`。如需回退，使用仍可访问的 Jupyter 终端：先将备份复制为候选文件，用 `/usr/sbin/sshd -t -f 候选文件` 校验，再替换 `/etc/ssh/sshd_config` 并向 `/run/sshd.pid` 中的原主进程发送 SIGHUP。保留现有 Jupyter 会话，核实新SSH连接成功后再结束维护。回退会恢复较长的未认证等待时间，可能重新出现原来的积压问题。

最后一次只读核对：SSH有效参数为端口23、LoginGraceTime 10、MaxAuthTries 3、MaxStartups 10:30:100；密码和公钥登录均保持启用。发布备份中的3笔订单编号和2条历史明细均仍在当前数据库，SQLite quick_check通过。
