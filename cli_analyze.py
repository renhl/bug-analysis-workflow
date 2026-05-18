#!/usr/bin/env python3
"""
Bug Analysis Workflow - CLI 入口

接收报错、traceId、业务异常描述或预期/实际行为，输出结构化分析结果。
默认输出保持稳定；使用 --json 时仍返回 problem_type/confidence/root_cause/
code_locations/fix_suggestion 这些核心字段，便于上层工具继续消费。
"""

import sys
import os
import argparse
import json
from pathlib import Path
from datetime import datetime

# 确保能导入项目模块
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

from core.workflow import BugAnalysisWorkflow, load_config_from_yaml
from core.models import BugAnalysisConfig, AnalysisRequest
from core.domain_config import DomainConfigLoader


def main():
    parser = argparse.ArgumentParser(
        description="Evidence-first bug analysis workflow",
        epilog=(
            "Examples:\n"
            "  python3 cli_analyze.py \"panic: nil pointer ... user.go:42\" --repo /path/to/repo\n"
            "  python3 cli_analyze.py \"开通会员后没有调起支付\" --domain go_member --repo /path/to/go_member "
            "--expected \"应进入支付前检查\" --actual \"直接跳过支付\"\n"
            "  python3 cli_analyze.py \"订单状态未更新\" --trace abc123 --time-range "
            "2026-05-18T10:00:00,2026-05-18T10:10:00 --json"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("error_desc", nargs="?", default=None, help="报错信息 / 问题描述")
    parser.add_argument("--domain", default=None, help="业务域名称（如 go_member）")
    parser.add_argument("--all-domains", action="store_true", help="跨所有已配置业务域分析")
    parser.add_argument("--list-domains", action="store_true", help="列出可用业务域并退出")
    parser.add_argument("--repo", default=None, help="代码仓库路径")
    parser.add_argument("--repos", nargs="+", help="跨服务分析的关联仓库路径")
    parser.add_argument("--trace", default=None, help="traceId")
    parser.add_argument("--expected", default=None, help="预期行为描述")
    parser.add_argument("--actual", default=None, help="实际行为描述")
    parser.add_argument(
        "--time-range",
        default=None,
        help="日志查询时间范围，格式：start,end；例如 2026-05-18T10:00:00,2026-05-18T10:10:00",
    )
    parser.add_argument("--changed-file", action="append", default=None, help="本次变更文件，可重复传入")
    parser.add_argument("--base-branch", default=None, help="用于自动获取 git diff 的基准分支")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--config", default=None, help="配置文件路径（默认 config/config.yaml）")
    parser.add_argument("--ai", action="store_true", help="启用 AI 分析（需要 ANTHROPIC_API_KEY 或 config 配置）")
    args = parser.parse_args()

    # 处理 --list-domains 和 --all-domains（不需要 error_desc）
    if args.list_domains:
        config_path = args.config or str(project_root / "config" / "domains.yaml")
        if Path(config_path).exists():
            loader = DomainConfigLoader(config_path)
            domains = loader.list_domains()
            print(f"Available domains ({len(domains)}):")
            for d in domains:
                cfg = loader.get_domain(d)
                repos = [r.path for r in cfg.repos] if cfg else []
                print(f"  {d}: {cfg.display if cfg else ''} | repos: {repos}")
        else:
            # 回退到旧的 domains/ 目录方式
            from domains.loader import DomainLoader
            loader = DomainLoader(str(project_root / "domains"))
            result = loader.load_all()
            print(f"Available domains ({len(result.list_domains())}):")
            for d in result.list_domains():
                domain = result.get_domain(d)
                print(f"  {d}: {domain.description if domain else ''}")
        return

    if args.all_domains and not args.domain:
        # --all-domains: 自动从 domains.yaml 加载所有域
        config_path = str(project_root / "config" / "domains.yaml")
        if Path(config_path).exists():
            loader = DomainConfigLoader(config_path)
            all_repos = loader.get_all_repos()
            if all_repos:
                args.repo = all_repos[0]
                args.repos = all_repos

    if not args.error_desc and not args.list_domains:
        parser.error("error_desc is required for analysis")

    time_range = None
    if args.time_range:
        try:
            start_raw, end_raw = [p.strip() for p in args.time_range.split(",", 1)]
            time_range = (datetime.fromisoformat(start_raw), datetime.fromisoformat(end_raw))
        except ValueError:
            parser.error("--time-range must use format: start,end")

    # 加载配置
    config_path = args.config or str(project_root / "config" / "config.yaml")
    try:
        config = load_config_from_yaml(config_path)
    except FileNotFoundError:
        config = BugAnalysisConfig()

    # 域模式
    domains_dir = project_root / "domains"
    if domains_dir.exists():
        config.domains_dir = str(domains_dir)

    # AI Analysis
    if args.ai:
        config.ai_enabled = True
        config.ai_api_key = config.ai_api_key or os.environ.get("ANTHROPIC_API_KEY")

    # Load multi-domain configuration
    domains_yaml = project_root / "config" / "domains.yaml"
    domain_loader = None
    if domains_yaml.exists():
        domain_loader = DomainConfigLoader(str(domains_yaml))

    # --all-domains: add all repos from all domains as related_repos
    related_repos = args.repos or []
    if args.all_domains and domain_loader:
        for path in domain_loader.get_all_repos():
            if path not in related_repos and path != args.repo:
                related_repos.append(path)

    workflow = BugAnalysisWorkflow(config)

    request = AnalysisRequest(
        error_desc=args.error_desc,
        domain=args.domain,
        repo_path=args.repo,
        related_repos=related_repos,
        trace_id=args.trace,
        time_range=time_range,
        expected_behavior=args.expected,
        actual_behavior=args.actual,
        changed_files=args.changed_file,
        base_branch=args.base_branch,
    )

    result = workflow.analyze(request)

    if args.json:
        output = {
            "problem_type": result.problem_type.value,
            "confidence": result.confidence,
            "root_cause": result.root_cause,
            "code_locations": [
                {
                    "file": loc.file,
                    "line": loc.line,
                    "function": loc.function,
                    "verified": loc.verified,
                }
                for loc in result.code_locations
            ],
            "fix_suggestion": result.fix_suggestion,
        }
        if result.thinking:
            output["thinking"] = result.thinking
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        # 人类可读格式
        type_labels = {
            "stack_trace": "A1-堆栈分析",
            "error_log": "A2-错误日志",
            "data_anomaly": "B1-数据异常",
            "business_anomaly": "B2-业务异常",
            "logic_error": "C1-逻辑偏差",
        }
        type_label = type_labels.get(result.problem_type.value, result.problem_type.value)
        conf_bar = "█" * int(result.confidence * 10) + "░" * (10 - int(result.confidence * 10))

        print(f"问题类型: {type_label}   置信度: {conf_bar} {result.confidence:.2f}")
        print()
        print("根因:")
        for line in result.root_cause.strip().split("\n"):
            if line.strip():
                print(f"  {line}")
        print()
        if result.code_locations:
            print("代码位置:")
            seen = set()
            for loc in result.code_locations[:6]:
                key = f"{loc.file}:{loc.line}"
                if key in seen:
                    continue
                seen.add(key)
                verified = "✓" if loc.verified else "~"
                func_info = f" → {loc.function}()" if loc.function else ""
                print(f"  [{verified}] {loc.file}:{loc.line}{func_info}")
            print()
        print("修复建议:")
        for line in result.fix_suggestion.strip().split("\n"):
            if line.strip():
                print(f"  {line}")


if __name__ == "__main__":
    main()
