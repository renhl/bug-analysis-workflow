"""
初始化服务注册表脚本

从现有系统导入服务信息，生成 services.yaml
"""

import argparse
import yaml
from pathlib import Path

# TODO: 实现从 Nacos/K8s 导入
# 这里提供一个模板实现


def create_sample_registry(output_path: str):
    """
    创建示例服务注册表
    """
    services = [
        {
            "name": "order-service",
            "repo_url": "git@github.com:your-org/order-service.git",
            "language": "java",
            "keywords": ["订单", "下单", "取消订单", "order"],
            "dependencies": ["payment-service", "inventory-service"],
            "db_tables": ["orders", "order_items"]
        },
        {
            "name": "payment-service",
            "repo_url": "git@github.com:your-org/payment-service.git",
            "language": "java",
            "keywords": ["支付", "付款", "退款", "payment"],
            "dependencies": ["order-service"],
            "db_tables": ["payments"]
        },
        {
            "name": "inventory-service",
            "repo_url": "git@github.com:your-org/inventory-service.git",
            "language": "go",
            "keywords": ["库存", "inventory"],
            "dependencies": [],
            "db_tables": ["inventory"]
        }
    ]
    
    yaml.dump({"services": services}, Path(output_path).open("w"))
    print(f"✓ 已生成示例配置: {output_path}")
    print("请根据实际情况修改配置文件")


def main():
    parser = argparse.ArgumentParser(description="初始化服务注册表")
    parser.add_argument("--output", default="config/services.yaml", help="输出文件路径")
    parser.add_argument("--sample", action="store_true", help="生成示例配置")
    
    args = parser.parse_args()
    
    if args.sample:
        create_sample_registry(args.output)
    else:
        print("请指定导入来源:")
        print("  --from-nacos http://nacos:8848  从 Nacos 导入")
        print("  --from-k8s namespace            从 Kubernetes 导入")
        print("  --sample                       生成示例配置")


if __name__ == "__main__":
    main()