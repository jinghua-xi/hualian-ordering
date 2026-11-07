#!/usr/bin/env python3
"""Validate, deploy and health-check a source-only release on the GPU host.

Usage: python3 deploy_remote.py PROJECT_ROOT STAGE_DIR
STAGE_DIR must contain release.tar.gz and manifest.json (path -> SHA-256).
Only application source is replaced. Secrets, orders and model files stay remote.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import time
import uuid
from datetime import datetime, timezone


PYTHON = "/usr/local/miniconda3/envs/py312/bin/python3"
REQUIRED = {
    "catalog_search.py", "auth_guard.py", "web_lookup.py", "server.py", "order_delivery.py", "catalog.json", "static/index.html",
    "static/app.js", "static/history.html", "static/sales.html",
    "static/sales.js", "static/styles.css", "ops/start-preview.sh",
}
MAX_SOURCE_BYTES = 100 * 1024 * 1024
EXISTING_REQUIRED = REQUIRED - {"catalog_search.py", "auth_guard.py", "web_lookup.py"}


class DeploymentError(Exception):
    pass


def progress(message):
    # An SSH disconnect must not interrupt source rollback through a broken pipe.
    try:
        print(message, flush=True)
    except OSError:
        pass


def allowed(path):
    if not isinstance(path, str) or not path or "\\" in path:
        return False
    parts = PurePosixPath(path).parts
    if (path.startswith("/") or any(part in (".", "..", "") for part in path.split("/"))
            or str(PurePosixPath(path)) != path):
        return False
    return (path in {"catalog_search.py", "auth_guard.py", "web_lookup.py", "paste_parser.py", "server.py", "order_delivery.py", "catalog.json", "deploy"}
            or (len(parts) >= 2 and parts[0] == "static")
            or (len(parts) == 2 and parts[0] == "ops" and parts[1].endswith((".py", ".sh"))))


def safe_destination(root, relative):
    if not allowed(relative):
        raise DeploymentError("拒绝非白名单源码路径。")
    target = root / relative
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise DeploymentError("源码路径包含符号链接，停止部署。")
    return target


def source_files(root):
    files = set()
    for name in ("catalog_search.py", "auth_guard.py", "web_lookup.py", "paste_parser.py", "server.py", "order_delivery.py", "catalog.json", "deploy"):
        path = safe_destination(root, name)
        if path.exists():
            if not path.is_file():
                raise DeploymentError("现有源码不是普通文件。")
            files.add(name)
    static_dir = root / "static"
    if static_dir.is_symlink() or (root / "ops").is_symlink():
        raise DeploymentError("源码目录不能是符号链接。")
    if static_dir.exists():
        for directory, dirs, names in os.walk(str(static_dir), followlinks=False):
            for name in dirs + names:
                path = Path(directory) / name
                if path.is_symlink():
                    raise DeploymentError("现有 static 目录包含符号链接。")
            for name in names:
                path = Path(directory) / name
                if not path.is_file():
                    raise DeploymentError("现有 static 包含非普通文件。")
                files.add(path.relative_to(root).as_posix())
    if (root / "ops").exists():
        for path in (root / "ops").iterdir():
            relative = path.relative_to(root).as_posix()
            if allowed(relative):
                safe_destination(root, relative)
                if not path.is_file():
                    raise DeploymentError("现有运维源码不是普通文件。")
                files.add(relative)
    return files


def write_metadata(directory, status, **extra):
    data = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat(), **extra}
    path = directory / "release.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def record_failure(directory, status, **extra):
    # A full disk must not stop recovery or hide its final result.
    try:
        write_metadata(directory, status, **extra)
    except OSError:
        progress("提示：无法写入发布诊断元数据，请检查磁盘空间。")


def unpack(stage):
    manifest_path, archive_path = stage / "manifest.json", stage / "release.tar.gz"
    if (manifest_path.is_symlink() or archive_path.is_symlink()
            or not manifest_path.is_file() or not archive_path.is_file()):
        raise DeploymentError("暂存目录缺少普通文件 manifest.json / release.tar.gz。")
    if manifest_path.stat().st_size > 2 * 1024 * 1024:
        raise DeploymentError("源码清单过大。")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or not manifest or len(manifest) > 10000
            or any(not allowed(name) or not isinstance(digest, str)
                   or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                   for name, digest in manifest.items())):
        raise DeploymentError("源码清单格式或路径不合法。")
    if not (REQUIRED | {"paste_parser.py"}).issubset(manifest):
        raise DeploymentError("发布包缺少必要的页面、后端或启动脚本。")
    destination = stage / "source"
    if destination.exists() or destination.is_symlink():
        raise DeploymentError("暂存 source 已存在，请使用新的部署暂存目录。")
    destination.mkdir(mode=0o700)
    seen, total = set(), 0
    with tarfile.open(str(archive_path), "r:gz") as archive:
        for member in archive:
            name = member.name
            if (not member.isfile() or not allowed(name) or name in seen
                    or name not in manifest or member.size < 0):
                raise DeploymentError("压缩包含重复、链接、目录或非白名单成员。")
            total += member.size
            if total > MAX_SOURCE_BYTES:
                raise DeploymentError("发布源码超过 100 MiB 限额。")
            target = safe_destination(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            reader = archive.extractfile(member)
            if reader is None:
                raise DeploymentError("无法读取压缩包成员。")
            with reader, target.open("xb") as writer:
                while True:
                    chunk = reader.read(1024 * 1024)
                    if not chunk:
                        break
                    writer.write(chunk)
                    digest.update(chunk)
            if digest.hexdigest() != manifest[name]:
                raise DeploymentError("源码 SHA-256 校验失败。")
            target.chmod(0o700 if name.endswith(".sh") or name == "deploy" else 0o600)
            seen.add(name)
    if seen != set(manifest):
        raise DeploymentError("压缩包与源码清单不完全一致。")
    return destination, set(manifest)


def validate_source(source, files):
    for name in sorted(files):
        path = source / name
        if name.endswith(".py"):
            compile(path.read_bytes(), name, "exec")
        elif name.endswith(".sh") or name == "deploy":
            result = subprocess.run(["/bin/bash", "-n", str(path)], capture_output=True)
            if result.returncode:
                raise DeploymentError("Shell 启动或运维脚本语法校验失败。")
    catalog = json.loads((source / "catalog.json").read_text(encoding="utf-8"))
    if not isinstance(catalog, list) or not catalog or len(catalog) > 100000:
        raise DeploymentError("商品目录必须是非空 JSON 数组。")
    seen = set()
    for product in catalog:
        if not isinstance(product, dict):
            raise DeploymentError("商品目录中存在非对象条目。")
        for field in ("sku", "name", "category", "spec", "unit"):
            if not isinstance(product.get(field), str) or not product[field].strip():
                raise DeploymentError("商品目录缺少有效的货号、名称、品类、规格或单位。")
        if not isinstance(product.get("color", ""), str):
            raise DeploymentError("商品颜色必须是文本；未知颜色可留空。")
        if product["sku"] in seen:
            raise DeploymentError("商品目录存在重复 SKU。")
        seen.add(product["sku"])


def database_connection(path, readonly=False):
    if path.is_symlink() or not path.is_file():
        raise DeploymentError("真实订单数据库不存在或不是普通文件。")
    return sqlite3.connect(path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
                           uri=True, timeout=1)


def backup_database(path, destination):
    source = database_connection(path, readonly=True)
    target = sqlite3.connect(str(destination))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    destination.chmod(0o600)


def order_numbers(path):
    database = database_connection(path, readonly=True)
    try:
        if database.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise DeploymentError("订单数据库 quick_check 失败。")
        return {row[0] for row in database.execute("SELECT number FROM orders")}
    finally:
        database.close()


def preflight(source, database, stage):
    copied = stage / "preflight.sqlite3"
    backup_database(database, copied)
    before = order_numbers(copied)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("SMTP_") and key not in {
                       "PREVIEW_PASSWORD", "SALES_PASSWORD", "SESSION_SECRET", "ORDER_RECIPIENT",
                   }}
    environment.update(DEMO_MODE="1", ORDERS_DB=str(copied), PYTHONDONTWRITEBYTECODE="1")
    code = ("import server; "
            "assert server.DEMO_MODE and not server.mail_enabled(); "
            "server.init_db(); "
            "rows=server.sales_orders(); "
            "assert isinstance(rows,list); "
            "assert all(isinstance(row,dict) and row.get('number') for row in rows)")
    result = subprocess.run([PYTHON, "-B", "-c", code], cwd=str(source), env=environment,
                            capture_output=True, timeout=45)
    diagnostic = stage / "preflight.log"
    diagnostic.write_bytes(result.stdout + result.stderr)
    diagnostic.chmod(0o600)
    if result.returncode or not before.issubset(order_numbers(copied)):
        raise DeploymentError("新源码在订单数据库副本上的预检失败；线上服务未停止。")


def app_processes(root):
    matches = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            if Path(os.readlink(str(entry / "cwd"))) != root:
                continue
            args = (entry / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
            if (len(args) != 3 or not os.fsdecode(args[0]).rsplit("/", 1)[-1].startswith("python")
                    or args[1] != b"-B" or os.fsdecode(args[2]) not in {"server.py", str(root / "server.py")}):
                continue
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if fields[0] != "Z":
                matches.append((int(entry.name), fields[19]))
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            continue
    if len(matches) > 1:
        raise DeploymentError("检测到多个接单服务进程，拒绝部署。")
    return matches


def stop_application(root, database, on_stopping):
    """Block new mail claims, wait for existing leases, then stop under the lock."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        matches = app_processes(root)
        if not matches:
            on_stopping()
            return None
        previous = matches[0]
        connection = database_connection(database)
        try:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                    raise
                time.sleep(0.3)
                continue
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            active = ("order_email_outbox" in tables and connection.execute(
                "SELECT 1 FROM order_email_outbox WHERE lease_until > ? LIMIT 1", (int(time.time()),)
            ).fetchone() is not None)
            if active:
                connection.rollback()
                time.sleep(1)
                continue
            if app_processes(root) != [previous]:
                connection.rollback()
                continue
            on_stopping()
            os.kill(previous[0], signal.SIGTERM)
            while time.monotonic() < deadline:
                if previous not in app_processes(root):
                    return previous
                time.sleep(0.1)
            raise DeploymentError("接单服务在 SIGTERM 后未能退出，未强制杀死进程。")
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()
    raise DeploymentError("等待发信任务或数据库锁超过 60 秒，请稍后重新部署。")


def copy_source(source, destination, files):
    for name in sorted(files):
        target = safe_destination(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".deploy-" + uuid.uuid4().hex)
        try:
            shutil.copy2(str(source / name), str(temporary))
            os.replace(str(temporary), str(target))
        finally:
            if temporary.exists():
                temporary.unlink()


def start_application(root, diagnostic):
    with diagnostic.open("ab") as output:
        result = subprocess.run(["/bin/bash", str(root / "ops/start-preview.sh")],
                                cwd=str(root), stdout=output, stderr=output, timeout=80)
    diagnostic.chmod(0o600)
    if result.returncode:
        raise DeploymentError("启动脚本执行失败，详情已保存到发布诊断日志。")


HEALTH_CODE = r'''
import base64, json, os, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(path, role=None):
    headers = {}
    if role:
        username = os.environ.get(role + "_USER", "preview" if role == "PREVIEW" else "sales")
        password = os.environ.get(role + "_PASSWORD", "")
        if not username or not password:
            raise RuntimeError("Missing remote login configuration")
        token = base64.b64encode((username + ":" + password).encode()).decode()
        headers["Authorization"] = "Basic " + token
    url = path if path.startswith("http:") else "http://127.0.0.1:8767" + path
    with opener.open(urllib.request.Request(url, headers=headers), timeout=3) as response:
        data = response.read(16 * 1024 * 1024 + 1)
        assert response.status == 200 and data and len(data) <= 16 * 1024 * 1024
        return data
page = get("/").lower()
assert b"<form" in page and b"password" in page
catalog = json.loads(get("/api/catalog", "SALES"))
assert isinstance(catalog.get("products"), list) and catalog["products"]
assert isinstance(json.loads(get("/api/time", "SALES")), dict)
assert isinstance(json.loads(get("/sales/orders", "SALES")).get("orders"), list)
for path in ("/app.js", "/sales.js", "/styles.css"):
    assert len(get(path, "PREVIEW").strip()) > 20
models = json.loads(get("http://127.0.0.1:11434/api/tags"))
assert any(row.get("name") == "qwen3:8b" or row.get("model") == "qwen3:8b"
           for row in models.get("models", []))
print("HEALTH_OK")
'''


def health_check(root, database, previous, expected_orders):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        matches = app_processes(root)
        if len(matches) == 1 and matches[0] != previous:
            # Only this remote Bash process reads secrets. They never enter arguments or logs.
            command = ["/bin/bash", "-c", 'set -ae; source "$1"; exec "$2" -B -c "$3"',
                       "deploy-health", str(root / "preview.env"), PYTHON, HEALTH_CODE]
            try:
                result = subprocess.run(command, cwd=str(root), capture_output=True, timeout=25)
                if result.returncode == 0 and b"HEALTH_OK" in result.stdout:
                    if not expected_orders.issubset(order_numbers(database)):
                        raise DeploymentError("部署后缺失原订单编号，已触发源码回退。")
                    if app_processes(root) == matches:
                        return matches[0]
            except subprocess.TimeoutExpired:
                pass
        time.sleep(1)
    raise DeploymentError("服务健康检查失败：进程、登录、商品/时间/订单接口、静态资源或 Qwen 检查未通过。")


def prune_releases(releases, current):
    successful = []
    for directory in releases.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            metadata = json.loads((directory / "release.json").read_text())
            if metadata.get("status") == "success":
                successful.append(directory)
        except (OSError, ValueError):
            continue
    for directory in sorted(successful, key=lambda path: path.name, reverse=True)[3:]:
        if directory != current:
            shutil.rmtree(str(directory))


def interrupted(signum, _frame):
    raise DeploymentError("部署被信号 %s 中断。" % signum)


def deploy(root, stage):
    if not root.is_dir() or not stage.is_dir() or root == stage:
        raise DeploymentError("项目目录或暂存目录无效。")
    releases = root / "releases"
    if releases.is_symlink():
        raise DeploymentError("releases 不能是符号链接。")
    releases.mkdir(mode=0o700, exist_ok=True)
    lock_path = releases / "deploy.lock"
    if lock_path.is_symlink():
        raise DeploymentError("部署锁不能是符号链接。")
    with lock_path.open("a+") as lock:
        lock_path.chmod(0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DeploymentError("已有部署正在进行，请等待完成。") from error
        progress("1/6 校验发布包、代码语法和商品目录…")
        source, new_files = unpack(stage)
        validate_source(source, new_files)
        database = root / "orders.sqlite3"
        old_files = source_files(root)
        if not EXISTING_REQUIRED.issubset(old_files):
            raise DeploymentError("线上源码不完整；请先恢复现有服务的必要文件。")
        if not Path(PYTHON).is_file():
            raise DeploymentError("找不到云端 py312 Python 解释器。")
        if (root / "preview.env").is_symlink() or not (root / "preview.env").is_file():
            raise DeploymentError("缺少云端普通文件 preview.env。")
        app_processes(root)
        progress("2/6 使用订单数据库副本检查迁移和历史订单读取（不发信、不运行推理）…")
        preflight(source, database, stage)
        release_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        release = releases / release_id
        release.mkdir(mode=0o700)
        backup = release / "source"
        backup.mkdir(mode=0o700)
        copy_source(root, backup, old_files)
        (release / "source-files.json").write_text(json.dumps(sorted(old_files)) + "\n")
        write_metadata(release, "prepared", release_id=release_id)
        recovery_needed = False
        previous, original_orders = None, None

        def mark_stopping():
            nonlocal recovery_needed
            recovery_needed = True

        try:
            progress("3/6 等待正在发送的邮件完成，安全停止旧服务…")
            previous = stop_application(root, database, mark_stopping)
            backup_database(database, release / "orders.sqlite3")
            original_orders = order_numbers(database)
            progress("4/6 保存源码与订单备份，切换新源码…")
            copy_source(source, root, new_files)
            progress("5/6 启动服务并检查登录、订单、商品、时间及 Qwen…")
            start_application(root, release / "start.log")
            health_check(root, database, previous, original_orders)
            write_metadata(release, "success", release_id=release_id)
            progress("6/6 部署成功；订单与发信配置已保留。")
            try:
                prune_releases(releases, release)
            except OSError:
                progress("提示：旧发布备份清理失败，本次部署已成功，备份均已保留。")
            progress("DEPLOY_OK " + release_id)
            return 0
        except BaseException as error:
            detail = str(error) if isinstance(error, DeploymentError) else type(error).__name__
            progress("部署失败：" + detail)
            if not recovery_needed:
                record_failure(release, "failed_before_stop", release_id=release_id, error=detail)
                raise
            # Complete recovery even when the original failure was SIGINT/SIGTERM.
            for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, signal.SIG_IGN)
            try:
                progress("正在恢复旧源码；保留当前真实订单库和邮件状态…")
                stopped_new = stop_application(root, database, lambda: None)
                copy_source(backup, root, old_files)
                for name in sorted(new_files - old_files):
                    target = safe_destination(root, name)
                    if target.is_file():
                        target.unlink()
                start_application(root, release / "rollback.log")
                health_check(root, database, stopped_new, original_orders or set())
                record_failure(release, "rolled_back", release_id=release_id, error=detail)
                progress("ROLLBACK_OK " + release_id + "（旧源码已恢复，真实订单库未回滚）")
            except BaseException as rollback_error:
                rollback_detail = (str(rollback_error) if isinstance(rollback_error, DeploymentError)
                                   else type(rollback_error).__name__)
                record_failure(release, "rollback_failed", release_id=release_id,
                               error=detail, rollback_error=rollback_detail)
                progress("ROLLBACK_FAILED " + release_id + "：" + rollback_detail)
            return 1


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 deploy_remote.py PROJECT_ROOT STAGE_DIR", file=sys.stderr)
        return 2
    os.umask(0o077)
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    try:
        return deploy(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    except BaseException as error:
        detail = str(error) if isinstance(error, DeploymentError) else type(error).__name__
        print("DEPLOY_FAILED（线上源码未切换）：" + detail, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
