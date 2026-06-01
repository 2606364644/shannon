#!/usr/bin/env python3
"""
LLM-Proxy 模型列表探测脚本

常见的 Provider 列表:
    - openai: OpenAI 官方模型（GPT-4, GPT-4o, O系列等）
    - azure: Azure OpenAI 服务（GPT-4, GPT-4o-jp等）
    - aws: AWS Bedrock 上的 Anthropic 模型（Claude-3.5-Sonnet等）
    - google: Google AI 模型（Gemini系列）
    - volcengine: 火山引擎（豆包系列、DeepSeek等）
    - dashscope: 阿里云 DashScope（Qwen/通义千问系列）
    - private: 私有模型（DeepSeek等）

用法:
    # 使用 API Key 获取所有模型
    python scripts/fetch_models.py --api-key YOUR_API_KEY

    # 获取指定 provider 的模型
    python scripts/fetch_models.py --api-key YOUR_API_KEY --provider openai

    # 保存到自定义路径
    python scripts/fetch_models.py --api-key YOUR_API_KEY --output models.json
"""

import argparse
import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests


class LLMProxyClient:
    """LLM-Proxy API 客户端"""

    def __init__(self, base_url: str = "https://llm-proxy.futuoa.com", api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        if api_key:
            self.session.headers.update({"Authorization": f"Bearer {api_key}"})

    def get_service_info(self) -> Dict[str, Any]:
        """获取服务信息"""
        response = self.session.get(f"{self.base_url}/")
        response.raise_for_status()
        return response.json()

    def get_all_models(self) -> Dict[str, Any]:
        """获取所有可用模型列表"""
        response = self.session.get(f"{self.base_url}/v1/models")
        response.raise_for_status()
        return response.json()

    def get_provider_models(self, provider: str) -> Dict[str, Any]:
        """获取指定 provider 的模型列表"""
        response = self.session.get(f"{self.base_url}/{provider}/v1/models")
        response.raise_for_status()
        return response.json()

    def get_available_providers(self) -> List[str]:
        """从服务信息中提取可用的 provider 列表"""
        info = self.get_service_info()
        return info.get("providers", [])


def fetch_all_models(client: LLMProxyClient) -> Dict[str, Any]:
    """获取所有模型，按 provider 分组"""
    result = {
        "timestamp": datetime.now().isoformat(),
        "service_info": {},
        "all_models": [],
        "by_provider": {},
    }

    # 获取服务信息
    try:
        result["service_info"] = client.get_service_info()
    except Exception as e:
        print(f"⚠️  获取服务信息失败: {e}", file=sys.stderr)

    # 获取所有模型
    try:
        all_models = client.get_all_models()
        result["all_models"] = all_models
    except Exception as e:
        print(f"⚠️  获取所有模型失败: {e}", file=sys.stderr)
        result["all_models"] = {}

    # 按 provider 分组获取
    providers = result["service_info"].get("providers", [])
    if not providers:
        # 如果没有 provider 列表，从模型中提取
        model_data = result.get("all_models", {}).get("data", [])
        if isinstance(model_data, list):
            providers = set(m.get("provider", "unknown") for m in model_data)
        else:
            providers = []

    for provider in providers:
        try:
            provider_models = client.get_provider_models(provider)
            result["by_provider"][provider] = provider_models
            print(f"✓ {provider}: {len(provider_models.get('data', []))} 个模型")
        except Exception as e:
            print(f"⚠️  获取 {provider} 模型失败: {e}", file=sys.stderr)
            result["by_provider"][provider] = {"error": str(e)}

    return result


def print_summary(data: Dict[str, Any]) -> None:
    """打印摘要信息"""
    print("\n" + "=" * 50)
    print("📊 模型列表摘要")
    print("=" * 50)

    service_info = data.get("service_info", {})
    if service_info:
        print(f"服务: {service_info.get('service', 'N/A')}")
        print(f"版本: {service_info.get('version', 'N/A')}")
        print(f"状态: {service_info.get('status', 'N/A')}")

    all_models = data.get("all_models", {}).get("data", [])
    print(f"\n总模型数: {len(all_models)}")

    # 按 provider 统计
    provider_count = {}
    for model in all_models:
        provider = model.get("provider", "unknown")
        provider_count[provider] = provider_count.get(provider, 0) + 1

    print("\n按 Provider 统计:")
    for provider, count in sorted(provider_count.items(), key=lambda x: x[1], reverse=True):
        print(f"  {provider}: {count}")

    print("=" * 50)


def main():
    parser = argparse.ArgumentParser(description="探测 LLM-Proxy 模型列表")
    parser.add_argument("--api-key", "-k", help="API Key (如不提供则只获取服务信息)")
    parser.add_argument("--provider", "-p", help="指定 provider 名称")
    parser.add_argument("--output", "-o", default="models.json", help="输出文件路径 (默认: models.json)")
    parser.add_argument("--base-url", default="https://llm-proxy.futuoa.com", help="API 基础 URL")
    parser.add_argument("--no-save", action="store_true", help="不保存到文件，只打印")
    parser.add_argument("--compact", action="store_true", help="紧凑格式输出 JSON")

    args = parser.parse_args()

    client = LLMProxyClient(base_url=args.base_url, api_key=args.api_key)

    try:
        if args.provider:
            # 获取指定 provider 的模型
            print(f"🔍 获取 {args.provider} 的模型列表...")
            data = client.get_provider_models(args.provider)
            data["timestamp"] = datetime.now().isoformat()
        else:
            # 获取所有模型
            print("🔍 获取所有模型列表...")
            data = fetch_all_models(client)

        print_summary(data)

        if not args.no_save:
            with open(args.output, "w", encoding="utf-8") as f:
                if args.compact:
                    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
                else:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"\n💾 已保存到: {args.output}")
        else:
            # 即使不保存也打印 JSON
            print("\n📄 JSON 输出:")
            print(json.dumps(data, ensure_ascii=False, indent=2))

    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP 错误: {e}", file=sys.stderr)
        if e.response.status_code == 401:
            print("💡 提示: 请检查 API Key 是否正确", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ 错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
