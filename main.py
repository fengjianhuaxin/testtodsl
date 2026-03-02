"""CLI 命令入口 - 通过命令行调度多智能体流水线"""
import argparse
import sys
import os

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def cmd_query(args):
    """执行自然语言查询"""
    from pipeline.orchestrator import Orchestrator
    orch = Orchestrator()
    orch.run(args.question, source=args.source)


def cmd_interactive(args):
    """交互模式"""
    from pipeline.orchestrator import Orchestrator
    print("=" * 60)
    print("🎓 本体论多智能体数据协作系统 - 交互模式")
    print("  输入自然语言问题进行查询")
    print("  输入 'quit' 或 'exit' 退出")
    print("  输入 'source:xxx' 切换数据源 (university_a / university_b / all)")
    print("=" * 60)

    source = None
    while True:
        try:
            question = input("\n💬 请输入问题: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            print("再见！")
            break
        if question.lower().startswith("source:"):
            source = question.split(":", 1)[1].strip()
            print(f"✅ 数据源已切换为: {source}")
            continue

        orch = Orchestrator()
        orch.run(question, source=source)


def cmd_ontology(args):
    """查看本体"""
    from ontology.ontology_manager import OntologyManager
    import config
    om = OntologyManager(os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json"))

    if args.action == "show":
        print(om.to_description())
    elif args.action == "search":
        results = om.search_concepts(args.keyword)
        if results:
            for r in results:
                print(f"  [{r['type']}] {r.get('entity', '')}.{r['name']} - {r['label']}")
        else:
            print("未找到匹配的概念")


def cmd_mapping(args):
    """查看映射"""
    from mapping.mapping_manager import MappingManager
    import config
    mm = MappingManager(config.MAPPING_DIR)
    print(mm.to_description(source_id=args.source))


def cmd_generate_data(args):
    """生成样例数据"""
    from generate_data import generate_university_a, generate_university_b
    generate_university_a()
    generate_university_b()


def main():
    parser = argparse.ArgumentParser(
        description="🎓 本体论多智能体数据协作系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python main.py generate-data                                # 生成样例数据
  python main.py query "列出所有学生的姓名和班级"              # 查询
  python main.py query "统计男生和女生的人数" --source university_a
  python main.py query "查询高等数学的平均成绩" --source university_b
  python main.py query "对比两所大学的学生人数" --source all
  python main.py ontology show                                # 查看本体
  python main.py ontology search 学生                         # 搜索概念
  python main.py mapping show                                 # 查看映射
  python main.py interactive                                   # 交互模式
        """)

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # query 命令
    p_query = subparsers.add_parser("query", help="执行自然语言查询")
    p_query.add_argument("question", type=str, help="自然语言问题")
    p_query.add_argument("--source", type=str, default=None,
                         help="数据源: university_a / university_b / all")
    p_query.set_defaults(func=cmd_query)

    # interactive 命令
    p_interactive = subparsers.add_parser("interactive", help="交互模式")
    p_interactive.set_defaults(func=cmd_interactive)

    # ontology 命令
    p_onto = subparsers.add_parser("ontology", help="查看本体定义")
    p_onto.add_argument("action", choices=["show", "search"], help="操作类型")
    p_onto.add_argument("keyword", nargs="?", default="", help="搜索关键词")
    p_onto.set_defaults(func=cmd_ontology)

    # mapping 命令
    p_map = subparsers.add_parser("mapping", help="查看映射配置")
    p_map.add_argument("action", choices=["show"], help="操作类型")
    p_map.add_argument("--source", type=str, default=None, help="指定数据源")
    p_map.set_defaults(func=cmd_mapping)

    # generate-data 命令
    p_gen = subparsers.add_parser("generate-data", help="生成样例数据")
    p_gen.set_defaults(func=cmd_generate_data)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
