"""生成样例数据的脚本"""
import pandas as pd
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config


def generate_university_a():
    """生成A大学样例数据（中文拼音命名）"""
    data_dir = config.DATA_SOURCES["university_a"]
    os.makedirs(data_dir, exist_ok=True)

    # 班级表
    banjixx = pd.DataFrame({
        "bj_id": ["C001", "C002", "C003", "C004"],
        "bjmc": ["计算机2301班", "计算机2302班", "数学2301班", "物理2301班"],
        "nj": ["2023", "2023", "2023", "2023"],
        "js_gh": ["T001", "T002", "T003", "T004"]
    })
    banjixx.to_excel(os.path.join(data_dir, "banjixx.xlsx"), index=False)

    # 教师表
    jiaoshi = pd.DataFrame({
        "gh": ["T001", "T002", "T003", "T004", "T005"],
        "xm": ["王教授", "李副教授", "张讲师", "刘教授", "陈副教授"],
        "xb": ["男", "女", "男", "女", "男"],
        "zc": ["教授", "副教授", "讲师", "教授", "副教授"],
        "xy": ["计算机学院", "计算机学院", "数学学院", "物理学院", "数学学院"]
    })
    jiaoshi.to_excel(os.path.join(data_dir, "jiaoshi.xlsx"), index=False)

    # 课程表
    kecheng = pd.DataFrame({
        "kc_id": ["K001", "K002", "K003", "K004", "K005", "K006"],
        "kcmc": ["高等数学", "数据结构", "大学物理", "线性代数", "Python编程", "概率统计"],
        "xf": [4, 3, 4, 3, 3, 3],
        "js_gh": ["T003", "T001", "T004", "T005", "T002", "T003"]
    })
    kecheng.to_excel(os.path.join(data_dir, "kecheng.xlsx"), index=False)

    # 学生表
    xuesheng = pd.DataFrame({
        "xh": ["S001", "S002", "S003", "S004", "S005", "S006", "S007", "S008", "S009", "S010"],
        "xm": ["赵一", "钱二", "孙三", "李四", "周五", "吴六", "郑七", "王八", "冯九", "陈十"],
        "xb": ["男", "女", "男", "女", "男", "女", "男", "女", "男", "女"],
        "csrq": ["2005-03-15", "2004-07-22", "2005-01-10", "2004-11-30", "2005-06-18",
                 "2004-09-05", "2005-02-28", "2004-12-12", "2005-08-08", "2004-05-20"],
        "bj_id": ["C001", "C001", "C002", "C002", "C003", "C003", "C004", "C004", "C001", "C003"]
    })
    xuesheng.to_excel(os.path.join(data_dir, "xuesheng.xlsx"), index=False)

    # 成绩表
    chengji = pd.DataFrame({
        "cj_id": [f"G{str(i).zfill(3)}" for i in range(1, 31)],
        "xh": ["S001","S001","S001","S002","S002","S002","S003","S003","S003","S004",
               "S004","S004","S005","S005","S005","S006","S006","S006","S007","S007",
               "S007","S008","S008","S008","S009","S009","S009","S010","S010","S010"],
        "kc_id": ["K001","K002","K005","K001","K002","K005","K001","K002","K005","K001",
                  "K002","K005","K001","K003","K006","K001","K003","K006","K003","K004",
                  "K006","K003","K004","K006","K001","K002","K005","K001","K003","K006"],
        "fenshu": [85, 90, 78, 92, 88, 95, 76, 82, 70, 88,
                   91, 85, 95, 87, 90, 72, 68, 75, 80, 85,
                   88, 93, 90, 86, 78, 85, 82, 90, 92, 88],
        "xueqi": ["2023-2024-1"]*30
    })
    chengji.to_excel(os.path.join(data_dir, "chengji.xlsx"), index=False)
    print("✅ A大学数据生成完成")


def generate_university_b():
    """生成B大学样例数据（英文命名）"""
    data_dir = config.DATA_SOURCES["university_b"]
    os.makedirs(data_dir, exist_ok=True)

    # 班级表
    class_info = pd.DataFrame({
        "class_id": ["CL01", "CL02", "CL03", "CL04"],
        "class_name": ["软件工程2301", "软件工程2302", "信息安全2301", "人工智能2301"],
        "grade": ["2023", "2023", "2023", "2023"],
        "advisor_id": ["TC01", "TC02", "TC03", "TC04"]
    })
    class_info.to_excel(os.path.join(data_dir, "class_info.xlsx"), index=False)

    # 教师表
    teacher_info = pd.DataFrame({
        "teacher_id": ["TC01", "TC02", "TC03", "TC04", "TC05"],
        "teacher_name": ["黄教授", "林副教授", "杨讲师", "赵教授", "周副教授"],
        "gender": ["男", "女", "男", "女", "男"],
        "title": ["教授", "副教授", "讲师", "教授", "副教授"],
        "college": ["软件学院", "软件学院", "信息学院", "人工智能学院", "信息学院"]
    })
    teacher_info.to_excel(os.path.join(data_dir, "teacher_info.xlsx"), index=False)

    # 课程表
    course_list = pd.DataFrame({
        "course_id": ["CS01", "CS02", "CS03", "CS04", "CS05", "CS06"],
        "course_name": ["高等数学", "算法设计", "网络安全", "机器学习", "Java编程", "概率统计"],
        "credit": [4, 3, 3, 4, 3, 3],
        "teacher_id": ["TC03", "TC01", "TC03", "TC04", "TC02", "TC05"]
    })
    course_list.to_excel(os.path.join(data_dir, "course_list.xlsx"), index=False)

    # 学生表
    stu_info = pd.DataFrame({
        "student_id": ["B001", "B002", "B003", "B004", "B005", "B006", "B007", "B008"],
        "name": ["张伟", "刘洋", "陈静", "杨帆", "黄磊", "林霞", "吴浩", "马丽"],
        "gender": ["男", "女", "女", "男", "男", "女", "男", "女"],
        "birthday": ["2005-04-10", "2004-08-15", "2005-02-20", "2004-10-05",
                     "2005-07-12", "2004-06-28", "2005-03-08", "2004-11-18"],
        "class_id": ["CL01", "CL01", "CL02", "CL02", "CL03", "CL03", "CL04", "CL04"]
    })
    stu_info.to_excel(os.path.join(data_dir, "stu_info.xlsx"), index=False)

    # 成绩表
    score_record = pd.DataFrame({
        "record_id": [f"R{str(i).zfill(3)}" for i in range(1, 25)],
        "student_id": ["B001","B001","B001","B002","B002","B002","B003","B003","B003","B004",
                       "B004","B004","B005","B005","B005","B006","B006","B006","B007","B007",
                       "B007","B008","B008","B008"],
        "course_id": ["CS01","CS02","CS05","CS01","CS02","CS05","CS01","CS02","CS05","CS01",
                      "CS02","CS05","CS01","CS03","CS06","CS01","CS03","CS06","CS04","CS05",
                      "CS06","CS04","CS05","CS06"],
        "score": [88, 92, 85, 78, 85, 90, 95, 88, 92, 82,
                  79, 88, 90, 85, 78, 75, 82, 80, 93, 87,
                  91, 86, 90, 84],
        "semester": ["2023-2024-1"]*24
    })
    score_record.to_excel(os.path.join(data_dir, "score_record.xlsx"), index=False)
    print("✅ B大学数据生成完成")


if __name__ == "__main__":
    generate_university_a()
    generate_university_b()
    print("✅ 全部样例数据生成完成")
