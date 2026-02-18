"""PikPak API 客户端封装"""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pikpakapi import PikPakApi
from pikpakapi.enums import DownloadStatus

TOKEN_FILE = Path(__file__).parent / "pikpak_token.json"


class PikPakClient:
    """封装 PikPakApi，提供离线下载 → 获取直链的完整流程"""

    def __init__(self, username: str, password: str, save_dir: str = "/"):
        self.username = username
        self.password = password
        self.save_dir = save_dir
        self._save_dir_id: Optional[str] = None

        # 尝试加载已有 token
        saved_token = self._load_token()
        if saved_token:
            self.client = PikPakApi(
                username=username,
                password=password,
                encoded_token=saved_token,
                token_refresh_callback=PikPakClient._on_token_refresh,
            )
        else:
            self.client = PikPakApi(
                username=username,
                password=password,
                token_refresh_callback=PikPakClient._on_token_refresh,
            )

    def _load_token(self) -> Optional[str]:
        """从本地文件加载 token"""
        try:
            if TOKEN_FILE.exists():
                data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
                # 只使用同一账号的 token
                if data.get("username") == self.username:
                    print("[PikPak] 已加载本地 token")
                    return data.get("encoded_token")
        except Exception:
            pass
        return None

    def _save_token(self):
        """保存 token 到本地文件"""
        try:
            if self.client.encoded_token:
                TOKEN_FILE.write_text(
                    json.dumps({
                        "username": self.username,
                        "encoded_token": self.client.encoded_token,
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
        except Exception as e:
            print(f"[PikPak] ⚠ 保存 token 失败: {e}")

    @staticmethod
    async def _on_token_refresh(client: PikPakApi, **kwargs):
        """token 刷新回调 — 自动保存新 token"""
        try:
            if client.encoded_token:
                TOKEN_FILE.write_text(
                    json.dumps({
                        "username": client.username,
                        "encoded_token": client.encoded_token,
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
                print("[PikPak] token 已自动刷新并保存")
        except Exception:
            pass

    async def login(self):
        """登录 PikPak：优先使用 token，失败回退密码登录"""
        # 如果已有 token，尝试 refresh 验证有效性
        if self.client.refresh_token:
            try:
                await self.client.refresh_access_token()
                self._save_token()
                print("[PikPak] token 登录成功")
                return
            except Exception as e:
                print(f"[PikPak] token 登录失败 ({e})，尝试密码登录...")

        # 密码登录
        try:
            await self.client.login()
            self._save_token()
            print("[PikPak] 密码登录成功，token 已保存")
        except Exception as e:
            error_msg = str(e)
            if "Invalid username or password" in error_msg:
                print(f"[PikPak] ✗ 登录失败: 用户名或密码错误")
                print(f"         请检查 config.yaml 中的 pikpak.username 和 pikpak.password")
                print(f"         当前用户名: {self.client.username}")
            else:
                print(f"[PikPak] ✗ 登录失败: {error_msg}")
            raise

    async def _get_save_dir_id(self) -> Optional[str]:
        """获取保存目录的 ID（缓存）"""
        if self._save_dir_id is not None:
            return self._save_dir_id

        if self.save_dir in ("/", ""):
            # 根目录不需要 ID
            return None

        result = await self.client.path_to_id(self.save_dir, create=True)
        if result:
            self._save_dir_id = result[-1]["id"]
            return self._save_dir_id
        return None

    async def add_offline_task(self, magnet_url: str, name: Optional[str] = None) -> Dict[str, Any]:
        """
        添加离线下载任务

        Returns:
            包含 task_id 和 file_id 的字典
        """
        parent_id = await self._get_save_dir_id()
        result = await self.client.offline_download(
            file_url=magnet_url,
            parent_id=parent_id,
            name=name,
        )

        task = result.get("task", {})
        task_id = task.get("id", "")
        file_id = task.get("file_id", "")
        file_name = task.get("file_name", "未知")

        print(f"[PikPak] 离线任务已添加: {file_name}")
        print(f"         Task ID: {task_id}")

        return {
            "task_id": task_id,
            "file_id": file_id,
            "file_name": file_name,
            "raw": result,
        }

    async def wait_for_task(
        self,
        task_id: str,
        file_id: str,
        poll_interval: float = 3.0,
        max_wait_time: float = 3600.0,
    ) -> DownloadStatus:
        """
        轮询等待离线任务完成

        Returns:
            最终的下载状态
        """
        start_time = time.time()
        last_status = None

        while True:
            elapsed = time.time() - start_time
            if elapsed > max_wait_time:
                print(f"\n[PikPak] ⚠ 等待超时 ({max_wait_time}s)")
                return DownloadStatus.error

            try:
                status = await self.client.get_task_status(task_id, file_id)
            except Exception as e:
                print(f"\n[PikPak] ⚠ 查询状态出错: {e}")
                await asyncio.sleep(poll_interval)
                continue

            if status != last_status:
                print(f"[PikPak] 任务状态: {status.value}")
                last_status = status

            if status == DownloadStatus.done:
                print("[PikPak] ✓ 离线下载完成")
                return status
            elif status == DownloadStatus.error:
                print("[PikPak] ✗ 离线下载失败")
                return status
            elif status == DownloadStatus.not_found:
                print("[PikPak] ✗ 任务不存在")
                return status

            await asyncio.sleep(poll_interval)

    async def get_download_urls(self, file_id: str) -> List[Dict[str, str]]:
        """
        获取文件的下载链接。
        如果 file_id 对应的是文件夹，会递归获取内部所有文件的链接。

        Returns:
            [{"name": "文件名", "url": "下载链接", "file_id": "文件ID"}, ...]
        """
        try:
            info = await self.client.get_download_url(file_id)
        except Exception as e:
            print(f"[PikPak] 获取下载链接失败: {e}")
            raise

        # 如果是文件夹，递归获取子文件
        kind = info.get("kind", "")
        if kind == "drive#folder":
            return await self._list_folder_files(file_id)

        # 单个文件
        url = info.get("web_content_link", "")
        name = info.get("name", "未知文件")

        if not url:
            # 尝试 medias 链接
            medias = info.get("medias", [])
            if medias:
                link = medias[0].get("link", {})
                url = link.get("url", "")

        if url:
            return [{"name": name, "url": url, "file_id": file_id}]
        else:
            print(f"[PikPak] ⚠ 无法获取下载链接: {name}")
            return []

    async def _list_folder_files(self, folder_id: str) -> List[Dict[str, str]]:
        """递归列出文件夹内所有文件的下载链接"""
        results = []
        next_page_token = None

        while True:
            resp = await self.client.file_list(
                parent_id=folder_id,
                next_page_token=next_page_token,
            )

            files = resp.get("files", [])
            for f in files:
                kind = f.get("kind", "")
                fid = f.get("id", "")

                if kind == "drive#folder":
                    # 递归进入子文件夹
                    sub_files = await self._list_folder_files(fid)
                    results.extend(sub_files)
                else:
                    url = f.get("web_content_link", "")
                    name = f.get("name", "未知文件")
                    if url:
                        results.append({"name": name, "url": url, "file_id": fid})
                    else:
                        # 需要单独请求下载链接
                        urls = await self.get_download_urls(fid)
                        results.extend(urls)

            next_page_token = resp.get("next_page_token")
            if not next_page_token:
                break

        return results

    async def delete_files(self, file_ids: List[str]):
        """永久删除文件（不经回收站）"""
        if file_ids:
            await self.client.delete_forever(file_ids)
            print(f"[PikPak] 已永久删除 {len(file_ids)} 个文件")

    async def get_offline_tasks(self) -> List[Dict[str, Any]]:
        """获取当前离线任务列表"""
        result = await self.client.offline_list()
        tasks = result.get("tasks", [])
        return tasks

    # ── 分享链接相关 ──

    async def get_share_file_list(
        self, share_link: str, pass_code: str = ""
    ) -> Dict[str, Any]:
        """
        解析分享链接，获取文件列表。

        Returns:
            {
                "share_id": "xxx",
                "pass_code_token": "xxx",
                "files": [{"id", "name", "kind", "size", "file_type"}, ...]
            }
        """
        # 提取 share_id
        match = re.search(r"/s/([^/?#]+)", share_link)
        if not match:
            raise ValueError("无效的分享链接格式")
        share_id = match.group(1)

        # 获取分享信息 — API 返回 {files: [...], pass_code_token, ...}
        result = await self.client.get_share_info(share_link, pass_code or None)
        if isinstance(result, ValueError):
            raise result

        pass_code_token = result.get("pass_code_token", "")

        # 收集所有文件（递归展开文件夹）
        files: List[Dict] = []
        for item in result.get("files", []):
            await self._collect_share_files(
                share_id, pass_code_token, item, files
            )

        return {
            "share_id": share_id,
            "pass_code_token": pass_code_token,
            "files": files,
        }

    async def _collect_share_files(
        self,
        share_id: str,
        pass_code_token: str,
        file_info: Dict,
        files: List[Dict],
        prefix: str = "",
    ):
        """递归收集分享链接中的所有文件"""
        kind = file_info.get("kind", "")
        file_id = file_info.get("id", "")
        name = file_info.get("name", "")
        full_path = f"{prefix}/{name}" if prefix else name

        if kind == "drive#folder":
            # 文件夹：列出子内容
            resp = await self.client.get_share_folder(
                share_id, pass_code_token, parent_id=file_id
            )
            for f in resp.get("files", []):
                await self._collect_share_files(
                    share_id, pass_code_token, f, files, full_path
                )
        elif kind == "drive#file":
            size = int(file_info.get("size", 0))
            file_type = file_info.get("mime_type", "")
            icon_link = file_info.get("icon_link", "")
            files.append({
                "id": file_id,
                "name": name,
                "path": full_path,
                "size": size,
                "file_type": file_type,
                "icon_link": icon_link,
            })

    async def save_share_files(
        self, share_id: str, file_ids: List[str], pass_code_token: str
    ) -> List[str]:
        """
        将分享文件转存到自己的网盘（使用库内置 restore 方法）。

        Returns:
            保存后在自己网盘中的文件 ID 列表
        """
        result = await self.client.restore(share_id, pass_code_token, file_ids)
        print(f"[PikPak] restore 响应: {json.dumps(result, ensure_ascii=False, indent=2)}")

        # 从响应中提取文件 ID — 兼容多种可能的返回格式
        saved_ids = []

        # 格式1: 顶层 file_id（实际观察到的格式）
        if result.get("file_id"):
            saved_ids.append(result["file_id"])

        # 格式2: {"task_info": [{"file_id": "xxx"}, ...]}
        if not saved_ids:
            for task_info in result.get("task_info", []):
                fid = task_info.get("file_id", "")
                if fid:
                    saved_ids.append(fid)

        print(f"[PikPak] 已保存 {len(saved_ids)} 个分享文件到网盘, IDs: {saved_ids}")
        return saved_ids
