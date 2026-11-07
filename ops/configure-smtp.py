"""Verify Gmail SMTP credentials and save them for the ordering service."""

from argparse import ArgumentParser
from contextlib import closing
from email.errors import HeaderParseError
from email.headerregistry import Address
import getpass
from pathlib import Path
import re
import shlex
import smtplib
import ssl
import sys
from tempfile import NamedTemporaryFile
from time import sleep
import warnings


def verify_login(host, port, sender, password):
    context = ssl.create_default_context()
    for attempt in range(1, 4):
        phase = "连接"
        try:
            with closing(smtplib.SMTP_SSL(host, port, timeout=12, context=context)) as client:
                phase = "登录"
                client.login(sender, password)
            return
        except smtplib.SMTPResponseException as error:
            detail = f"SMTP {error.smtp_code}"
            if not 400 <= error.smtp_code < 500:
                raise SystemExit(f"Gmail 拒绝{phase}（{detail}）。配置尚未保存。") from None
        except (ssl.SSLCertVerificationError, smtplib.SMTPNotSupportedError) as error:
            raise SystemExit(f"Gmail {phase}检查未通过（{type(error).__name__}）。配置尚未保存。") from None
        except OSError as error:
            detail = type(error).__name__
        if attempt < 3:
            print(f"Gmail {phase}中断（{detail}），正在重试（{attempt + 1}/3）…", flush=True)
            sleep(attempt)
    raise SystemExit(f"Gmail {phase}检查连续失败（{detail}）。配置尚未保存。")


def main():
    parser = ArgumentParser()
    parser.add_argument("--sender", help="发件 Gmail 地址")
    args = parser.parse_args()
    config = Path(__file__).resolve().parents[1] / "preview.env"
    if not config.is_file() or not sys.stdin.isatty():
        raise SystemExit("请在接单服务器的 JupyterLab Terminal 里运行此脚本。")

    sender = (args.sender or input("发件 Gmail 地址：")).strip()
    try:
        address = Address(addr_spec=sender)
    except (HeaderParseError, ValueError):
        raise SystemExit("请填写完整的发件邮箱地址。") from None
    if not address.username or not address.domain:
        raise SystemExit("请填写完整的发件邮箱地址。")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            password = "".join(getpass.getpass("Google 应用专用密码（输入时不显示）：").split())
        except getpass.GetPassWarning:
            raise SystemExit("当前终端无法隐藏密码，请换用 JupyterLab Terminal。") from None
    if not re.fullmatch(r"[A-Za-z0-9]{16}", password):
        raise SystemExit("请输入 Google 生成的 16 位应用专用密码。")

    host, port = "smtp.gmail.com", 465
    verify_login(host, port, sender, password)

    settings = {
        "SMTP_HOST": host,
        "SMTP_PORT": str(port),
        "SMTP_SECURITY": "ssl",
        "SMTP_USERNAME": sender,
        "SMTP_PASSWORD": password,
        "SMTP_FROM": sender,
    }
    existing = config.read_text(encoding="utf-8").splitlines()
    lines = [
        line for line in existing
        if line.strip().removeprefix("export ").split("=", 1)[0].strip() not in settings
    ]
    lines.extend(f"export {key}={shlex.quote(value)}" for key, value in settings.items())
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=config.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            handle.write("\n".join(lines) + "\n")
            handle.close()
            temp.replace(config)
        finally:
            temp.unlink(missing_ok=True)
    print("Gmail 登录检查通过，配置已保存。重启接单服务后生效。")


if __name__ == "__main__":
    main()
