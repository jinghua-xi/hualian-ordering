#!/usr/bin/env python3
"""Upload a source-only release to the existing ordering preview server."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".deploy"
CONFIG = STATE / "config.json"
REQUIRED = {"catalog_search.py", "auth_guard.py", "web_lookup.py", "paste_parser.py", "server.py", "order_delivery.py", "catalog.json", "ops/start-preview.sh",
            "ops/deploy_remote.py", "static/index.html", "static/app.js", "static/styles.css",
            "static/history.html", "static/sales.html", "static/sales.js"}


def source_files():
    names = {"catalog_search.py", "auth_guard.py", "web_lookup.py", "paste_parser.py", "server.py", "order_delivery.py", "catalog.json", "deploy"}
    for folder, suffixes in (("static", {".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2"}),
                             ("ops", {".py", ".sh"})):
        directory = ROOT / folder
        if directory.is_symlink():
            raise ValueError("部署目录不能是符号链接：" + folder)
        paths = directory.iterdir() if folder == "ops" else directory.rglob("*")
        for path in paths:
            if path.is_symlink():
                raise ValueError("部署目录中不允许符号链接：" + str(path.relative_to(ROOT)))
            if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts:
                names.add(path.relative_to(ROOT).as_posix())
    if not REQUIRED <= names:
        raise ValueError("缺少部署文件：" + ", ".join(sorted(REQUIRED - names)))
    result = {}
    for name in sorted(names):
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("缺少普通源码文件：" + name)
        data = path.read_bytes()
        if path.suffix == ".py":
            compile(data, name, "exec")
        result[name] = hashlib.sha256(data).hexdigest()
    catalog = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
    if not isinstance(catalog, (list, dict)) or not catalog:
        raise ValueError("商品目录必须是非空 JSON 列表或对象")
    return result


def configuration():
    if not CONFIG.is_file():
        raise ValueError("尚未配置 SSH 部署连接，参见 ops/DEPLOYMENT.md（.deploy/config.json）。")
    value = json.loads(CONFIG.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("部署配置必须是 JSON 对象")
    for key in ("host", "port", "user", "project_root", "public_url"):
        if key not in value:
            raise ValueError("部署配置缺少 " + key)
    for key in ("host", "user", "project_root", "public_url", "identity_file", "known_hosts_file"):
        if key in value and not isinstance(value[key], str):
            raise ValueError("部署配置 " + key + " 必须是字符串")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", value["host"]) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", value["user"]):
        raise ValueError("SSH 主机名或用户名不合法")
    if type(value["port"]) is not int or not 1 <= value["port"] <= 65535:
        raise ValueError("SSH 端口必须是 1–65535 的整数")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", value["project_root"]) or ".." in Path(value["project_root"]).parts:
        raise ValueError("远端项目路径不合法")
    if not value["public_url"].startswith("https://"):
        raise ValueError("公网地址必须使用 HTTPS")
    for key, default in (("identity_file", "id_ed25519"), ("known_hosts_file", "known_hosts")):
        path = Path(value.get(key, default)).expanduser()
        if not path.is_absolute():
            path = STATE / path
        if not path.is_file() or path.is_symlink():
            raise ValueError("缺少本机 SSH 文件：" + str(path))
        value[key] = str(path)
    if Path(value["identity_file"]).stat().st_mode & 0o077:
        raise ValueError("SSH 私钥权限过宽，请将其设为 chmod 600")
    return value


def run(args):
    # Stream progress without echoing complete commands or reading remote secrets.
    result = subprocess.run(args, text=True)
    if result.returncode:
        raise RuntimeError("命令失败（退出码 %s）" % result.returncode)


def connect_master(ssh, diagnostic):
    """Retry only the initial authentication, never replay a deployment command."""
    command = ssh[:-1] + ["-M", "-N", "-f", ssh[-1]]
    for attempt in range(1, 4):
        print(f"建立 SSH 部署连接（{attempt}/3）…", flush=True)
        with diagnostic.open("w") as output:
            result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=output, timeout=30)
        if result.returncode == 0:
            print("SSH 已认证，上传和发布将复用这条连接。", flush=True)
            return
        detail = diagnostic.read_text(errors="replace").strip()
        permanent = any(marker in detail.lower() for marker in (
            "permission denied", "host key verification failed", "remote host identification has changed",
            "bad configuration", "bad permissions",
        ))
        if permanent or attempt == 3:
            raise RuntimeError("SSH 连接失败：" + detail[-800:])
        time.sleep(attempt * 2)


def close_master(ssh):
    try:
        subprocess.run(ssh[:-1] + ["-O", "exit", ssh[-1]], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def public_health(base):
    # Login pages and static assets require no credentials and create no orders.
    for route, marker in (("/", b"<html"), ("/app.js", b""), ("/styles.css", b"")):
        request = urllib.request.Request(base.rstrip("/") + route, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(2 * 1024 * 1024)
            if response.status != 200 or not data or (marker and marker not in data.lower()):
                raise RuntimeError("公网检查未通过：" + route)


def main():
    parser = argparse.ArgumentParser(description="上传源码、备份、重启接单服务并检查；失败时回退源码。")
    parser.add_argument("--dry-run", action="store_true", help="只验证本地源码和上传清单，不连接服务器")
    args = parser.parse_args()
    manifest = source_files()
    print("本地检查通过，准备上传 %d 个源码文件；排除订单库、配置、日志和密钥。" % len(manifest), flush=True)
    if args.dry_run:
        print("\n".join(manifest))
        return
    config = configuration()
    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)
    # OpenSSH parses -o values using its config syntax, including whitespace.
    known_hosts = '"' + config["known_hosts_file"].replace("\\", "\\\\").replace('"', '\\"') + '"'
    ssh_options = ["-i", config["identity_file"], "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                   "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=" + known_hosts,
                   "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4"]
    target = config["user"] + "@" + config["host"]
    ssh = ["ssh", "-p", str(config["port"])] + ssh_options + [target]
    remote_stage = config["project_root"] + "/releases/.incoming-" + time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory(prefix="ordering-deploy-", dir="/tmp") as folder:
        folder = Path(folder)
        control_path = folder / "ssh-control"
        # A short, private Unix socket path also works when the project path contains spaces.
        ssh_options += ["-o", "ControlMaster=auto", "-o", "ControlPersist=60", "-o", "ControlPath=" + str(control_path)]
        ssh = ["ssh", "-p", str(config["port"])] + ssh_options + [target]
        package = folder / "release.tar.gz"
        with tarfile.open(str(package), "w:gz") as archive:
            for name in manifest:
                archive.add(str(ROOT / name), arcname=name, recursive=False)
        manifest_path = folder / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        stage_created = False
        remote_succeeded = False
        try:
            print("连接云服务器并上传…", flush=True)
            connect_master(ssh, folder / "ssh-connect.log")
            run(ssh + ["umask 077; mkdir -p -- " + shlex.quote(remote_stage)])
            stage_created = True
            run(["scp", "-q", "-P", str(config["port"])] + ssh_options +
                [str(package), str(manifest_path), str(ROOT / "ops/deploy_remote.py"), target + ":" + remote_stage + "/"])
            print("云端预检、备份、重启与功能检查…", flush=True)
            run(ssh + ["python3 " + shlex.quote(remote_stage + "/deploy_remote.py") + " " +
                       shlex.quote(config["project_root"]) + " " + shlex.quote(remote_stage)])
            remote_succeeded = True
        finally:
            if stage_created and remote_succeeded:
                cleanup = subprocess.run(ssh + ["rm -rf -- " + shlex.quote(remote_stage)],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if cleanup.returncode:
                    print("暂存目录尚未清理：" + remote_stage, file=sys.stderr)
            elif stage_created:
                print("失败暂存目录已保留，可检查诊断或恢复进度：" + remote_stage, file=sys.stderr)
            close_master(ssh)
    try:
        public_health(config["public_url"])
    except (OSError, urllib.error.URLError, RuntimeError) as exc:
        raise RuntimeError("云端功能检查已通过，但本机到公网入口的检查失败；请检查网络或端口映射：" + str(exc))
    (STATE / "last-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("部署完成，公网入口正常：" + config["public_url"], flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, SyntaxError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        print("部署未完成：" + str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("部署被中断；如已进入云端切换，助手会尝试回退源码，请确认线上服务状态。", file=sys.stderr)
        sys.exit(130)
