"""AutoPikDown Web 服务器"""

import asyncio
import json
import os

import yaml
from pathlib import Path
from typing import Any, Dict, List, Set

from aiohttp import web

from aria2_client import Aria2Client
from pikpak_client import PikPakClient


class WebServer:
    """Web 管理界面"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.app = web.Application()
        self.ws_clients: Set[web.WebSocketResponse] = set()
        self._setup_routes()
        self._pikpak: PikPakClient | None = None
        self._aria2: Aria2Client | None = None

    def _setup_routes(self):
        static_dir = Path(__file__).parent / "static"
        self.app.router.add_get("/", self._index)
        self.app.router.add_post("/api/add", self._api_add)
        self.app.router.add_get("/api/status", self._api_status)
        self.app.router.add_post("/api/test", self._api_test)
        self.app.router.add_get("/api/config", self._api_get_config)
        self.app.router.add_post("/api/config", self._api_save_config)
        self.app.router.add_post("/api/share/list", self._api_share_list)
        self.app.router.add_post("/api/share/download", self._api_share_download)
        self.app.router.add_get("/api/vip", self._api_vip_info)

        self.app.router.add_get("/ws", self._ws_handler)
        self.app.router.add_static("/static/", path=str(static_dir), name="static")

    async def _api_vip_info(self, request: web.Request) -> web.Response:
        """获取 PikPak VIP 会员状态和存储空间信息"""
        try:
            await self._ensure_clients()

            # 并发获取 VIP 和配额信息
            import asyncio, json as _json
            vip_result, quota_result = await asyncio.gather(
                self._pikpak.client.vip_info(),
                self._pikpak.client.get_quota_info(),
                return_exceptions=True,
            )

            # 解析 VIP
            is_vip = False
            vip_type = "unknown"
            expire = ""
            if isinstance(vip_result, dict):
                print(f"[PikPak] vip_info: {_json.dumps(vip_result, ensure_ascii=False)}")
                data = vip_result.get("data", vip_result)
                vip_type = data.get("type", "") or data.get("vip_type", "")
                status = data.get("status", "")
                expire = data.get("expire", "") or data.get("expire_time", "")
                is_vip = bool(vip_type and vip_type.lower() not in ("novip", "none", ""))
                if not is_vip and status:
                    is_vip = status.lower() in ("ok", "active", "valid")

            # 解析配额
            quota_limit = 0
            quota_usage = 0
            if isinstance(quota_result, dict):
                print(f"[PikPak] quota: {_json.dumps(quota_result, ensure_ascii=False)}")
                q = quota_result.get("quota", {})
                quota_limit = int(q.get("limit", 0))
                quota_usage = int(q.get("usage", 0))

            return web.json_response({
                "is_vip": is_vip, "type": vip_type, "expire": expire,
                "quota_limit": quota_limit, "quota_usage": quota_usage,
            })
        except Exception as e:
            return web.json_response({"is_vip": False, "type": "unknown", "error": str(e)})

    def _create_clients(self):
        """创建 PikPak 和 Aria2 客户端"""
        pikpak_cfg = self.config.get("pikpak", {})
        aria2_cfg = self.config.get("aria2", {})

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

    async def _ensure_clients(self):
        """确保客户端已初始化并登录"""
        if self._pikpak is None:
            self._pikpak, self._aria2 = self._create_clients()
            await self._pikpak.login()
        return self._pikpak, self._aria2

    async def _broadcast(self, msg: dict):
        """向所有 WebSocket 客户端广播消息"""
        dead = set()
        data = json.dumps(msg, ensure_ascii=False)
        for ws in self.ws_clients:
            try:
                await ws.send_str(data)
            except Exception:
                dead.add(ws)
        self.ws_clients -= dead

    # ── 路由处理 ──

    async def _index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(Path(__file__).parent / "static" / "index.html")

    async def _api_add(self, request: web.Request) -> web.Response:
        """添加磁链"""
        data = await request.json()
        magnets_text = data.get("magnets", "")
        magnets = [m.strip() for m in magnets_text.strip().splitlines() if m.strip() and not m.strip().startswith("#")]

        if not magnets:
            return web.json_response({"error": "没有有效的磁力链接"}, status=400)

        # 后台异步处理
        asyncio.create_task(self._process_magnets(magnets))

        return web.json_response({
            "message": f"已提交 {len(magnets)} 个磁链，处理中...",
            "count": len(magnets),
        })

    async def _api_status(self, request: web.Request) -> web.Response:
        """获取离线任务状态"""
        try:
            pikpak, _ = await self._ensure_clients()
            tasks = await pikpak.get_offline_tasks()

            task_list = []
            for t in tasks:
                task_list.append({
                    "name": t.get("file_name", "未知"),
                    "phase": t.get("phase", "未知"),
                    "progress": t.get("progress", 0),
                    "message": t.get("message", ""),
                    "created_time": t.get("created_time", ""),
                })

            return web.json_response({"tasks": task_list})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _api_test(self, request: web.Request) -> web.Response:
        """测试连接"""
        results = {}

        # 测试 PikPak
        try:
            pikpak, aria2 = self._create_clients()
            await pikpak.login()
            results["pikpak"] = {"ok": True, "message": "登录成功"}
            self._pikpak = pikpak
            self._aria2 = aria2
        except Exception as e:
            results["pikpak"] = {"ok": False, "message": str(e)}

        # 测试 Aria2
        try:
            _, aria2 = self._create_clients()
            version = await aria2.test_connection()
            results["aria2"] = {"ok": True, "message": f"版本 {version}"}
        except Exception as e:
            results["aria2"] = {"ok": False, "message": str(e)}

        return web.json_response(results)

    async def _api_get_config(self, request: web.Request) -> web.Response:
        """获取配置（密码脱敏）"""
        cfg = {
            "pikpak": {
                "username": self.config.get("pikpak", {}).get("username", ""),
                "password": self.config.get("pikpak", {}).get("password", ""),
                "save_dir": self.config.get("pikpak", {}).get("save_dir", "/"),
                "delete_after_download": self.config.get("pikpak", {}).get("delete_after_download", False),
            },
            "aria2": {
                "rpc_url": self.config.get("aria2", {}).get("rpc_url", "http://localhost:6800/jsonrpc"),
                "rpc_secret": self.config.get("aria2", {}).get("rpc_secret", ""),
                "download_dir": self.config.get("aria2", {}).get("download_dir", ""),
            },
            "task": {
                "poll_interval": self.config.get("task", {}).get("poll_interval", 3),
                "max_wait_time": self.config.get("task", {}).get("max_wait_time", 3600),
            },
        }
        return web.json_response(cfg)

    async def _api_save_config(self, request: web.Request) -> web.Response:
        """保存配置到 config.yaml"""
        try:
            data = await request.json()

            # 更新内存中的配置
            for section in ("pikpak", "aria2", "task"):
                if section in data:
                    if section not in self.config:
                        self.config[section] = {}
                    self.config[section].update(data[section])

            # 写入文件
            config_path = Path(__file__).parent / "config.yaml"
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True)

            # 重置客户端（下次操作时重新登录）
            self._pikpak = None
            self._aria2 = None

            return web.json_response({"message": "配置已保存"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)



    # ── 分享链接 API ──

    async def _api_share_list(self, request: web.Request):
        """解析分享链接，返回文件列表"""
        try:
            body = await request.json()
            share_link = body.get("share_link", "").strip()
            pass_code = body.get("pass_code", "").strip()

            if not share_link:
                return web.json_response({"error": "请输入分享链接"}, status=400)

            await self._ensure_clients()
            result = await self._pikpak.get_share_file_list(share_link, pass_code)

            # 格式化文件大小
            for f in result.get("files", []):
                size = f.get("size", 0)
                if size >= 1073741824:
                    f["size_str"] = f"{size / 1073741824:.1f} GB"
                elif size >= 1048576:
                    f["size_str"] = f"{size / 1048576:.1f} MB"
                elif size >= 1024:
                    f["size_str"] = f"{size / 1024:.1f} KB"
                else:
                    f["size_str"] = f"{size} B"

            return web.json_response(result)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _api_share_download(self, request: web.Request):
        """下载选中的分享文件"""
        try:
            body = await request.json()
            share_id = body.get("share_id", "")
            file_ids = body.get("file_ids", [])
            pass_code_token = body.get("pass_code_token", "")

            if not share_id or not file_ids:
                return web.json_response({"error": "缺少参数"}, status=400)

            asyncio.create_task(self._process_share_download(
                share_id, file_ids, pass_code_token
            ))

            return web.json_response({
                "message": f"开始处理 {len(file_ids)} 个文件"
            })
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _process_share_download(
        self, share_id: str, file_ids: List[str], pass_code_token: str
    ):
        """后台处理分享文件下载"""
        try:
            await self._ensure_clients()
            total = len(file_ids)

            await self._broadcast({
                "type": "task_start",
                "index": 1,
                "total": total,
                "magnet": f"分享文件 ({total} 个)"
            })

            # 1. 保存到自己网盘
            await self._broadcast({"type": "task_status", "index": 1, "status": "正在保存到网盘..."})
            saved_ids = await self._pikpak.save_share_files(share_id, file_ids, pass_code_token)

            if not saved_ids:
                await self._broadcast({"type": "task_error", "index": 1, "message": "保存失败，未获取到文件"})
                return

            # 2. 获取下载链接并推送到 Aria2
            success_count = 0
            all_file_ids = []

            print(f"[PikPak] 开始处理 {len(saved_ids)} 个文件")
            for i, fid in enumerate(saved_ids, 1):
                # 更新状态
                await self._broadcast({"type": "task_status", "index": i, "status": f"获取下载链接 [{i}/{len(saved_ids)}]"})

                # 重试机制 (最多3次)
                max_retries = 3
                urls = []
                for attempt in range(max_retries):
                    try:
                        import asyncio
                        # 设置 30秒 超时获取下载链接
                        urls = await asyncio.wait_for(self._pikpak.get_download_urls(fid), timeout=30.0)
                        if urls:
                            break # 成功获取，跳出重试循环
                    except asyncio.TimeoutError:
                        print(f"[PikPak] 文件 {i} 获取超时，第 {attempt+1}/{max_retries} 次尝试")
                        if attempt < max_retries - 1:
                            await self._broadcast({"type": "task_status", "index": i, "status": f"获取超时，第 {attempt+2} 次重试..."})
                            await asyncio.sleep(2)
                    except Exception as e:
                        print(f"[PikPak] 文件 {i} 获取出错: {e}")
                        if attempt < max_retries - 1:
                             await asyncio.sleep(2)

                if not urls:
                    print(f"[PikPak] 文件 {i} 获取下载链接失败，已重试 {max_retries} 次")
                    await self._broadcast({"type": "task_error", "index": i, "message": f"获取链接失败 (重试{max_retries}次)"})
                    continue

                try:
                    for url_info in urls:
                        all_file_ids.append(url_info["file_id"])
                        # 设置 10秒 超时推送 Aria2
                        gid = await asyncio.wait_for(self._aria2.add_uri(url_info["url"], url_info["name"]), timeout=10.0)
                        if gid:
                            success_count += 1
                            await self._broadcast({
                                "type": "task_added",
                                "index": i,
                                "file_name": url_info["name"],
                            })
                except asyncio.TimeoutError:
                    print(f"[PikPak] Aria2 推送超时: 文件 {i}")
                    await self._broadcast({"type": "task_error", "index": i, "message": "Aria2 请求超时"})
                except Exception as e:
                    print(f"[PikPak] 处理文件出错: {e}")
                    await self._broadcast({"type": "task_error", "index": i, "message": str(e)})

            await self._broadcast({
                "type": "aria2_done",
                "index": 1,
                "success_count": success_count,
                "total_count": len(saved_ids),
            })

            # 3. 删除网盘中的临时文件（分享文件始终删除）
            if saved_ids:
                await self._broadcast({"type": "task_status", "index": 1, "status": "正在删除网盘文件..."})
                await self._pikpak.delete_files(saved_ids)

            await self._broadcast({
                "type": "all_done",
                "total": total,
            })

        except Exception as e:
            await self._broadcast({"type": "error", "message": f"分享下载失败: {e}"})

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket 连接"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws_clients.add(ws)

        try:
            async for msg in ws:
                pass  # 只用于推送，不处理客户端消息
        finally:
            self.ws_clients.discard(ws)

        return ws

    # ── 后台任务处理 ──

    async def _process_magnets(self, magnets: List[str]):
        """后台处理磁链列表"""
        from pikpakapi.enums import DownloadStatus

        task_cfg = self.config.get("task", {})
        pikpak_cfg = self.config.get("pikpak", {})
        poll_interval = task_cfg.get("poll_interval", 3)
        max_wait_time = task_cfg.get("max_wait_time", 3600)
        delete_after = pikpak_cfg.get("delete_after_download", False)

        try:
            pikpak, aria2 = await self._ensure_clients()
        except Exception as e:
            await self._broadcast({"type": "error", "message": f"登录失败: {e}"})
            return

        total = len(magnets)

        for i, magnet in enumerate(magnets, 1):
            task_label = f"[{i}/{total}]"

            await self._broadcast({
                "type": "task_start",
                "index": i,
                "total": total,
                "magnet": magnet[:80] + ("..." if len(magnet) > 80 else ""),
            })

            # 1. 添加离线任务
            try:
                task_info = await pikpak.add_offline_task(magnet)
            except Exception as e:
                await self._broadcast({
                    "type": "task_error",
                    "index": i,
                    "message": f"添加离线任务失败: {e}",
                })
                continue

            task_id = task_info["task_id"]
            file_id = task_info["file_id"]
            file_name = task_info["file_name"]

            await self._broadcast({
                "type": "task_added",
                "index": i,
                "file_name": file_name,
                "task_id": task_id,
            })

            if not task_id:
                await self._broadcast({
                    "type": "task_error",
                    "index": i,
                    "message": "未获取到 task_id",
                })
                continue

            # 2. 等待离线完成
            import time
            start_time = time.time()
            last_status = None

            while True:
                elapsed = time.time() - start_time
                if elapsed > max_wait_time:
                    await self._broadcast({
                        "type": "task_error",
                        "index": i,
                        "message": f"等待超时 ({max_wait_time}s)",
                    })
                    break

                try:
                    status = await pikpak.client.get_task_status(task_id, file_id)
                except Exception:
                    await asyncio.sleep(poll_interval)
                    continue

                if status != last_status:
                    await self._broadcast({
                        "type": "task_status",
                        "index": i,
                        "status": status.value,
                    })
                    last_status = status

                if status == DownloadStatus.done:
                    break
                elif status in (DownloadStatus.error, DownloadStatus.not_found):
                    await self._broadcast({
                        "type": "task_error",
                        "index": i,
                        "message": f"离线失败 ({status.value})",
                    })
                    break

                await asyncio.sleep(poll_interval)
            else:
                continue

            if last_status != DownloadStatus.done:
                continue

            # 3. 获取下载链接
            try:
                files = await pikpak.get_download_urls(file_id)
            except Exception as e:
                await self._broadcast({
                    "type": "task_error",
                    "index": i,
                    "message": f"获取下载链接失败: {e}",
                })
                continue

            if not files:
                await self._broadcast({
                    "type": "task_error",
                    "index": i,
                    "message": "未找到可下载的文件",
                })
                continue

            await self._broadcast({
                "type": "files_found",
                "index": i,
                "files": [f["name"] for f in files],
            })

            # 4. 推送到 Aria2
            try:
                tasks_to_add = [{"url": f["url"], "name": f["name"]} for f in files]
                gids = await aria2.add_uris_batch(tasks_to_add)

                await self._broadcast({
                    "type": "aria2_done",
                    "index": i,
                    "success_count": len(gids),
                    "total_count": len(files),
                })
            except Exception as e:
                await self._broadcast({
                    "type": "task_error",
                    "index": i,
                    "message": f"推送 Aria2 失败: {e}",
                })
                continue

            # 5. 可选清理
            if delete_after:
                try:
                    await pikpak.delete_files([file_id])
                except Exception:
                    pass

            await self._broadcast({
                "type": "task_done",
                "index": i,
                "file_name": file_name,
            })

        await self._broadcast({"type": "all_done", "total": total})

    def run(self, host: str = "0.0.0.0", port: int = 8888):
        """启动 Web 服务器"""
        print(f"AutoPikDown Web 服务器启动: http://localhost:{port}")
        web.run_app(self.app, host=host, port=port, print=None)
