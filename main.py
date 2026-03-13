"""CLI entry for running the ontology NL2SQL pipeline."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def cmd_query(args):
    """Execute one natural-language query."""
    from pipeline.orchestrator import Orchestrator

    orch = Orchestrator()
    orch.run(args.question)


def cmd_interactive(args):
    """Run interactive query mode."""
    from pipeline.orchestrator import Orchestrator

    print("=" * 60)
    print("本体问数系统 - 交互模式")
    print("  输入自然语言问题进行查询")
    print("  输入 'quit' 或 'exit' 退出")
    print("=" * 60)

    while True:
        try:
            question = input("\n请输入问题: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            print("再见")
            break

        orch = Orchestrator()
        orch.run(question)


def cmd_ontology(args):
    """Show/search ontology."""
    import config
    from ontology.ontology_manager import OntologyManager

    om = OntologyManager(os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json"))

    if args.action == "show":
        print(om.to_description())
    elif args.action == "search":
        results = om.search_concepts(args.keyword)
        if results:
            for item in results:
                print(f"  [{item['type']}] {item.get('entity', '')}.{item['name']} - {item['label']}")
        else:
            print("未找到匹配概念")


def cmd_mapping(args):
    """Show ontology/table mappings."""
    import config
    from mapping.mapping_manager import MappingManager

    mm = MappingManager(config.MAPPING_DIR)
    print(mm.to_description())


def main():
    parser = argparse.ArgumentParser(
        description="本体问数系统 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python main.py query "江苏省有哪些关联的系统未上云的项目"
  python main.py query "查询我省目录开放率是多少"
  python main.py ontology show
  python main.py ontology search 学生
  python main.py mapping show
  python main.py interactive
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    p_query = subparsers.add_parser("query", help="执行自然语言查询")
    p_query.add_argument("question", type=str, help="自然语言问题")
    p_query.set_defaults(func=cmd_query)

    p_interactive = subparsers.add_parser("interactive", help="交互模式")
    p_interactive.set_defaults(func=cmd_interactive)

    p_onto = subparsers.add_parser("ontology", help="查看本体定义")
    p_onto.add_argument("action", choices=["show", "search"], help="操作类型")
    p_onto.add_argument("keyword", nargs="?", default="", help="搜索关键词")
    p_onto.set_defaults(func=cmd_ontology)

    p_map = subparsers.add_parser("mapping", help="查看映射配置")
    p_map.add_argument("action", choices=["show"], help="操作类型")
    p_map.set_defaults(func=cmd_mapping)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
