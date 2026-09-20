import httpx
from mcp.types import CallToolResult, ListToolsResult

class MCPClient:
    def __init__(self, merchant_url: str):
        self.merchant_url = merchant_url.rstrip('/')
        
    async def list_tools(self) -> list[dict]:
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": 1,
            "params": {}
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.merchant_url}/mcp", json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("result", {}).get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": name,
                "arguments": arguments
            }
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{self.merchant_url}/mcp", json=payload)
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                return str(data["error"]), True
            
            result = data.get("result", {})
            is_error = result.get("isError", False)
            content = result.get("content", [])
            text_result = "\n".join([c.get("text", "") for c in content if c.get("type") == "text"])
            return text_result, is_error

    async def search_catalog(self, query: str, budget: float) -> tuple[str, bool]:
        return await self.call_tool("merchant_search_catalog", {"query": query, "max_budget_inr": budget})

    async def evaluate_upsell(self, product_id: str, mandate_limit: float) -> tuple[str, bool]:
        return await self.call_tool("merchant_evaluate_upsell", {"product_id": product_id, "current_mandate_limit": mandate_limit})


