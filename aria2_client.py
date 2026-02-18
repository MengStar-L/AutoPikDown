"""Aria2 JSON-RPC 客户端"""

import json
import uuid
from typing import Any, Dict, List, Optional

import httpx


class Aria2Client:
    """通过 JSON-RPC 与 Aria2 通信"""

    def __init__(self, rpc_url: str, rpc_secret: str = "", download_dir: str = ""):
        self.rpc_url = rpc_url
        self.rpc_secret = rpc_secret
        self.download_dir = download_dir

    def _build_request(self, method: str, params: List[Any] = None) -> Dict[str, Any]:
        """构建 JSON-RPC 请求体"""
        req = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
        }

        call_params = []
        if self.rpc_secret:
            call_params.append(f"token:{self.rpc_secret}")
        if params:
            call_params.extend(params)

        req["params"] = call_params
        return req

    async def _call(self, method: str, params: List[Any] = None) -> Any:
        """发送 JSON-RPC 请求"""
        payload = self._build_request(method, params)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.rpc_url,
                json=payload,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()

        if "error" in data:
            err = data["error"]
            raise RuntimeError(f"Aria2 RPC 错误: [{err.get('code')}] {err.get('message')}")

        return data.get("result")

    async def test_connection(self) -> str:
        """
        测试 Aria2 连接，返回版本号

        Raises:
            RuntimeError: 连接失败
        """
        result = await self._call("aria2.getVersion")
        version = result.get("version", "未知")
        print(f"[Aria2] 连接成功，版本: {version}")
        return version

    async def add_uri(
        self,
        url: str,
        filename: Optional[str] = None,
        extra_options: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        添加下载任务

        Args:
            url: 下载链接
            filename: 文件名（可选）
            extra_options: 额外的 Aria2 选项

        Returns:
            Aria2 任务 GID
        """
        options = {}

        if self.download_dir:
            options["dir"] = self.download_dir

        if filename:
            options["out"] = filename

        if extra_options:
            options.update(extra_options)

        params = [[url]]
        if options:
            params.append(options)

        gid = await self._call("aria2.addUri", params)
        print(f"[Aria2] 任务已添加: {filename or url[:80]}  (GID: {gid})")
        return gid

    async def add_uris_batch(
        self,
        tasks: List[Dict[str, str]],
    ) -> List[str]:
        """
        批量添加下载任务

        Args:
            tasks: [{"url": "...", "name": "..."}, ...]

        Returns:
            GID 列表
        """
        gids = []
        for task in tasks:
            try:
                gid = await self.add_uri(url=task["url"], filename=task.get("name"))
                gids.append(gid)
            except Exception as e:
                print(f"[Aria2] ✗ 添加失败: {task.get('name', '未知')} - {e}")
        return gids
