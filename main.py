"""AutoPikDown - 磁链自动离线下载工具

磁力链接 → PikPak 离线 → 获取直链 → 推送 Aria2
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

from aria2_client import Aria2Client
from pikpak_client import PikPakClient


CONFIG_FILE = "config.yaml"


def load_config() -> Dict[str, Any]:
    """加载配置文件"""
    config_path = Path(__file__).parent / CONFIG_FILE
    if not config_path.exists():
        print(f"✗ 未找到配置文件: {config_path}")
        print(f"  请复制 config.yaml.example 为 config.yaml 并填写配置")
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return config


def create_clients(config: Dict[str, Any]):
    """根据配置创建客户端实例"""
    pikpak_cfg = config.get("pikpak", {})
    aria2_cfg = config.get("aria2", {})

    pikpak = PikPakClient(
        username=pikpak_cfg["username"],
        password=pikpak_cfg["password"],
        save_dir=pikpak_cfg.get("save_dir", "/"),
    )

    aria2 = Aria2Client(
        rpc_url=aria2_cfg.get("rpc_url", "http://localhost:6800/jsonrpc"),
        rpc_secret=aria2_cfg.get("rpc_secret", ""),
        download_dir=aria2_cfg.get("download_dir", ""),
    )

    return pikpak, aria2


async def process_magnet(
    magnet: str,
    pikpak: PikPakClient,
    aria2: Aria2Client,
    config: Dict[str, Any],
) -> bool:
    """
    处理单个磁力链接的完整流程

    Returns:
        是否成功
    """
    task_cfg = config.get("task", {})
    pikpak_cfg = config.get("pikpak", {})
    poll_interval = task_cfg.get("poll_interval", 3)
    max_wait_time = task_cfg.get("max_wait_time", 3600)
    delete_after = pikpak_cfg.get("delete_after_download", False)

    magnet = magnet.strip()
    if not magnet:
        return False

    print(f"\n{'='*60}")
    print(f"处理磁链: {magnet[:80]}...")
    print(f"{'='*60}")

    # 1. 添加离线任务
    try:
        task_info = await pikpak.add_offline_task(magnet)
    except Exception as e:
        print(f"[PikPak] ✗ 添加离线任务失败: {e}")
        return False

    task_id = task_info["task_id"]
    file_id = task_info["file_id"]

    if not task_id:
        print("[PikPak] ✗ 未获取到 task_id")
        return False

    # 2. 等待离线完成
    from pikpakapi.enums import DownloadStatus

    status = await pikpak.wait_for_task(
        task_id, file_id,
        poll_interval=poll_interval,
        max_wait_time=max_wait_time,
    )

    if status != DownloadStatus.done:
        print(f"[PikPak] ✗ 离线任务未成功完成 (状态: {status.value})")
        return False

    # 3. 获取下载链接
    try:
        files = await pikpak.get_download_urls(file_id)
    except Exception as e:
        print(f"[PikPak] ✗ 获取下载链接失败: {e}")
        return False

    if not files:
        print("[PikPak] ✗ 未找到可下载的文件")
        return False

    print(f"[PikPak] 找到 {len(files)} 个文件:")
    for f in files:
        print(f"         📄 {f['name']}")

    # 4. 推送到 Aria2
    tasks = [{"url": f["url"], "name": f["name"]} for f in files]
    try:
        gids = await aria2.add_uris_batch(tasks)
        print(f"\n[Aria2] ✓ 成功推送 {len(gids)}/{len(files)} 个文件到 Aria2")
    except Exception as e:
        print(f"[Aria2] ✗ 推送失败: {e}")
        return False

    # 5. 可选：清理 PikPak 文件
    if delete_after:
        try:
            file_ids_to_delete = [f["file_id"] for f in files]
            # 如果有父文件夹 ID，只删父文件夹即可
            await pikpak.delete_files([file_id])
        except Exception as e:
            print(f"[PikPak] ⚠ 清理文件失败: {e}")

    return True


async def cmd_add(args, config: Dict[str, Any]):
    """处理 add 命令"""
    pikpak, aria2 = create_clients(config)
    await pikpak.login()

    success = await process_magnet(args.magnet, pikpak, aria2, config)
    if success:
        print("\n✓ 处理完成！")
    else:
        print("\n✗ 处理失败")
        sys.exit(1)


async def cmd_batch(args, config: Dict[str, Any]):
    """处理 batch 命令"""
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"✗ 文件不存在: {file_path}")
        sys.exit(1)

    with open(file_path, "r", encoding="utf-8") as f:
        magnets = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not magnets:
        print("✗ 文件中没有找到磁力链接")
        sys.exit(1)

    print(f"找到 {len(magnets)} 个磁力链接")

    pikpak, aria2 = create_clients(config)
    await pikpak.login()

    success_count = 0
    fail_count = 0

    for i, magnet in enumerate(magnets, 1):
        print(f"\n[{i}/{len(magnets)}] 处理中...")
        ok = await process_magnet(magnet, pikpak, aria2, config)
        if ok:
            success_count += 1
        else:
            fail_count += 1

    print(f"\n{'='*60}")
    print(f"批量处理完成: 成功 {success_count}, 失败 {fail_count}, 共 {len(magnets)}")
    print(f"{'='*60}")

    if fail_count > 0:
        sys.exit(1)


async def cmd_status(args, config: Dict[str, Any]):
    """查看 PikPak 离线任务列表"""
    pikpak, _ = create_clients(config)
    await pikpak.login()

    tasks = await pikpak.get_offline_tasks()

    if not tasks:
        print("当前没有离线任务")
        return

    print(f"当前有 {len(tasks)} 个离线任务:\n")
    for t in tasks:
        name = t.get("file_name", "未知")
        phase = t.get("phase", "未知")
        progress = t.get("progress", 0)
        message = t.get("message", "")

        status_icon = "⏳" if "RUNNING" in phase else ("✓" if "COMPLETE" in phase else "✗")
        print(f"  {status_icon} {name}")
        print(f"    状态: {phase}  进度: {progress}%")
        if message:
            print(f"    信息: {message}")
        print()


async def cmd_test(args, config: Dict[str, Any]):
    """测试 PikPak 和 Aria2 连接"""
    pikpak, aria2 = create_clients(config)

    print("测试 PikPak 连接...")
    try:
        await pikpak.login()
        print("  ✓ PikPak 登录成功")
    except Exception as e:
        print(f"  ✗ PikPak 登录失败: {e}")

    print("\n测试 Aria2 连接...")
    try:
        await aria2.test_connection()
        print("  ✓ Aria2 连接成功")
    except Exception as e:
        print(f"  ✗ Aria2 连接失败: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="AutoPikDown - 磁链 → PikPak 离线 → Aria2 下载",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py add "magnet:?xt=urn:btih:..."
  python main.py batch magnets.txt
  python main.py status
  python main.py test
  python main.py web --port 8888
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # add 命令
    add_parser = subparsers.add_parser("add", help="添加单个磁力链接")
    add_parser.add_argument("magnet", help="磁力链接")

    # batch 命令
    batch_parser = subparsers.add_parser("batch", help="批量添加（从文件读取）")
    batch_parser.add_argument("file", help="包含磁力链接的文件（每行一个）")

    # status 命令
    subparsers.add_parser("status", help="查看 PikPak 离线任务状态")

    # test 命令
    subparsers.add_parser("test", help="测试 PikPak 和 Aria2 连接")

    # web 命令
    web_parser = subparsers.add_parser("web", help="启动 Web 管理界面")
    web_parser.add_argument("--port", type=int, default=8888, help="端口号 (默认 8888)")
    web_parser.add_argument("--host", default="0.0.0.0", help="监听地址 (默认 0.0.0.0)")

    args = parser.parse_args()

    config = load_config()

    # 无参数或 web 命令 → 启动 Web 服务器
    if not args.command or args.command == "web":
        from web_server import WebServer
        server = WebServer(config)
        host = getattr(args, "host", "0.0.0.0")
        port = getattr(args, "port", 8888)
        server.run(host=host, port=port)
        return

    cmd_map = {
        "add": cmd_add,
        "batch": cmd_batch,
        "status": cmd_status,
        "test": cmd_test,
    }

    asyncio.run(cmd_map[args.command](args, config))


if __name__ == "__main__":
    main()
