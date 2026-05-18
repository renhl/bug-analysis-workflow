"""
WeKnora 知识库连接器
"""

import httpx
import json
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from core.models import DEFAULT_AGENT_ID


@dataclass
class WeKnoraConfig:
    """WeKnora 配置"""
    base_url: str       # http://your-weknora:8080/api/v1
    api_key: str        # sk-xxxxx
    default_agent_id: str = DEFAULT_AGENT_ID


class WeKnoraConnector:
    """
    WeKnora 知识库连接器
    
    用于:
    1. 知识检索（搜索历史案例、业务规则）
    2. Agent 问答（多步推理）
    3. 上传文档（保存新案例）
    """
    
    def __init__(self, config: WeKnoraConfig):
        self.config = config
        self.client = httpx.Client(
            base_url=config.base_url,
            headers={
                "X-API-Key": config.api_key,
                "Content-Type": "application/json"
            },
            timeout=60.0
        )
    
    # ========== 知识库管理 ==========
    
    def list_knowledge_bases(self) -> List[Dict]:
        """
        获取知识库列表
        """
        response = self.client.get("/knowledge-bases")
        return response.json().get("data", [])
    
    def create_knowledge_base(self, 
                              name: str, 
                              description: str,
                              kb_type: str = "document") -> Dict:
        """
        创建知识库
        """
        response = self.client.post("/knowledge-bases", json={
            "name": name,
            "description": description,
            "type": kb_type
        })
        return response.json()
    
    # ========== 文档上传 ==========
    
    def upload_document(self,
                        kb_id: str,
                        file_path: str,
                        metadata: Dict = None) -> Dict:
        """
        上传文档到知识库
        
        Args:
            kb_id: 知识库 ID
            file_path: 本地文件路径
            metadata: 元数据（可选）
        """
        with open(file_path, 'rb') as f:
            files = {'file': f}
            data = {'metadata': json.dumps(metadata)} if metadata else {}
            
            response = self.client.post(
                f"/knowledge-bases/{kb_id}/knowledge/file",
                files=files,
                data=data
            )
        
        return response.json()
    
    def upload_text(self,
                    kb_id: str,
                    content: str,
                    title: str,
                    metadata: Dict = None) -> Dict:
        """
        上传文本内容（直接发布，不停留在草稿）

        用于保存 Bug 案例文档
        """
        body: Dict = {
            "title": title,
            "content": content,
            "status": "publish",
        }
        if metadata:
            body["metadata"] = metadata
        response = self.client.post(
            f"/knowledge-bases/{kb_id}/knowledge/manual",
            json=body,
        )
        return response.json()
    
    def upload_from_url(self,
                        kb_id: str,
                        url: str,
                        title: str = None) -> Dict:
        """
        从 URL 导入文档
        """
        response = self.client.post(
            f"/knowledge-bases/{kb_id}/knowledge/url",
            json={
                "url": url,
                "title": title
            }
        )
        return response.json()
    
    # ========== 知识搜索 ==========
    
    def list_documents(self, kb_id: str, page_size: int = 100) -> List[str]:
        """返回知识库中所有文档的 ID 列表"""
        resp = self.client.get(
            f"/knowledge-bases/{kb_id}/knowledge",
            params={"page": 1, "page_size": page_size},
        )
        items = resp.json().get("data", [])
        return [item["id"] for item in items if isinstance(item, dict) and item.get("id")]

    def search_knowledge(self,
                         query: str,
                         kb_ids: List[str] = None,
                         knowledge_ids: List[str] = None,
                         top_k: int = 5) -> List[Dict]:
        """
        在知识库中语义搜索

        WeKnora API 实测：/knowledge-search 用 knowledge_ids（文档 ID）可返回结果，
        用 knowledge_base_ids 会返回空。因此当只传 kb_ids 时，先把 KB 内所有文档 ID
        列出来再搜索。

        Args:
            query: 查询文本
            kb_ids: 知识库 ID 列表（自动展开成 knowledge_ids）
            knowledge_ids: 直接指定文档 ID 列表（优先）
            top_k: 返回结果数量
        """
        # 把 kb_ids 展开成 knowledge_ids
        all_doc_ids: List[str] = list(knowledge_ids or [])
        if kb_ids and not knowledge_ids:
            for kb_id in kb_ids:
                all_doc_ids.extend(self.list_documents(kb_id))

        if not all_doc_ids:
            return []

        payload: Dict = {
            "query": query,
            "knowledge_ids": all_doc_ids,
            "top_k": top_k,
        }

        response = self.client.post("/knowledge-search", json=payload)
        raw = response.json()
        data = raw.get("data", [])
        # 兼容两种格式：直接列表 or {"results": [...]}
        if isinstance(data, dict):
            return data.get("results", [])
        return data if isinstance(data, list) else []

    def search_by_keyword(self,
                          keyword: str,
                          kb_id: str = None,
                          limit: int = 10,
                          offset: int = 0) -> List[Dict]:
        """
        按关键词检索文档列表（标题 / 内容文本匹配）

        GET /knowledge/search?keyword=...&limit=...&offset=...
        可选 kb_id 筛选特定知识库
        """
        params = {"keyword": keyword, "limit": str(limit), "offset": str(offset)}
        if kb_id:
            params["knowledge_base_id"] = kb_id
        from urllib.parse import urlencode
        response = self.client.get(f"/knowledge/search?{urlencode(params)}")
        raw = response.json()
        data = raw.get("data", [])
        return data if isinstance(data, list) else []
    
    # ========== Agent 问答 ==========
    
    def create_session(self, title: str = "Bug Analysis") -> str:
        """
        创建会话
        """
        response = self.client.post("/sessions", json={"title": title})
        return response.json().get("data", {}).get("id")
    
    def agent_chat(self,
                   session_id: str,
                   query: str,
                   kb_ids: List[str] = None,
                   agent_id: str = None,
                   enable_memory: bool = True) -> Dict[str, Any]:
        """
        Agent 问答
        
        支持多步推理和工具调用
        
        Args:
            session_id: 会话 ID
            query: 查询文本
            kb_ids: 知识库 ID 列表
            agent_id: Agent ID（默认 builtin-smart-reasoning）
            enable_memory: 是否启用记忆
        
        Returns:
            {
                "thinking": "...",      # Agent 思考过程
                "tool_calls": [...],    # 工具调用记录
                "references": [...],    # 知识库引用
                "answer": "...",        # 最终答案
            }
        """
        agent_id = agent_id or self.config.default_agent_id
        
        payload = {
            "query": query,
            "agent_id": agent_id,
            "agent_enabled": True,
            "enable_memory": enable_memory
        }
        
        if kb_ids:
            payload["knowledge_base_ids"] = kb_ids
        
        # SSE 流式响应处理
        result = self._process_sse_response(f"/agent-chat/{session_id}", payload)
        
        return result
    
    def knowledge_chat(self,
                       session_id: str,
                       query: str,
                       kb_ids: List[str]) -> Dict:
        """
        知识库问答（不使用 Agent）
        """
        payload = {
            "query": query,
            "knowledge_base_ids": kb_ids
        }
        
        return self._process_sse_response(f"/knowledge-chat/{session_id}", payload)
    
    def _process_sse_response(self, endpoint: str, payload: dict) -> Dict:
        """
        处理 SSE 流式响应
        """
        collected = {
            "thinking": [],
            "tool_calls": [],
            "tool_results": [],
            "references": [],
            "answer": [],
            "errors": []
        }
        
        try:
            with self.client.stream("POST", endpoint, json=payload) as response:
                for line in response.iter_lines():
                    if line.startswith("data:"):
                        try:
                            data = json.loads(line[5:])
                            response_type = data.get("response_type")
                            
                            if response_type == "thinking":
                                collected["thinking"].append(data.get("content"))
                            elif response_type == "tool_call":
                                collected["tool_calls"].append(data.get("data"))
                            elif response_type == "tool_result":
                                collected["tool_results"].append(data.get("content"))
                            elif response_type == "references":
                                collected["references"].append(data.get("data"))
                            elif response_type == "answer":
                                collected["answer"].append(data.get("content"))
                            elif response_type == "error":
                                collected["errors"].append(data.get("content"))
                        except json.JSONDecodeError:
                            pass
        except Exception as e:
            collected["errors"].append(str(e))
        
        return {
            "thinking": "\n".join(collected["thinking"]),
            "tool_calls": collected["tool_calls"],
            "tool_results": collected["tool_results"],
            "references": collected["references"],
            "answer": "\n".join(collected["answer"]),
            "errors": collected["errors"]
        }
    
    # ========== Session 管理 ==========
    
    def get_session(self, session_id: str) -> Dict:
        """
        获取会话详情
        """
        response = self.client.get(f"/sessions/{session_id}")
        return response.json().get("data", {})
    
    def clear_session(self, session_id: str):
        """
        清空会话消息
        """
        self.client.delete(f"/sessions/{session_id}/messages")
    
    def delete_session(self, session_id: str):
        """
        删除会话
        """
        self.client.delete(f"/sessions/{session_id}")