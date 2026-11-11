# -*- coding: utf-8 -*-
"""初始化演示数据：通过真实导入管线灌入虚构客户数据。

场景覆盖：
  1. 共享手机号（已验证+同名）→ 张伟 CRM/电商 自动合并
  2. 同名不同人 → 两个李娜（上海/广州）各自独立，不合并
  3. 手机号/邮箱指向冲突 → 电商 S103 陈强 进入人工确认
  4. 相似姓名+地址 → 两个王芳 生成候选推荐（之后可在页面上手动合并、
     再导入 06_crm_wangfang.json、撤销合并，演示归属冲突）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import MDM

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "mdm.db")

SOURCES = {
    "01_crm.json": ("CRM系统", 30),
    "02_shop.json": ("电商平台", 20),
    "03_support.json": ("客服系统", 10),
    "04_shop_conflict.json": ("电商平台", 20),
}

ACTION_LABELS = {
    "created": "新建客户", "auto_merged": "自动合并", "updated": "更新来源版本",
    "review": "进入人工确认", "candidate": "生成候选推荐", "error": "错误",
}


def main():
    if os.path.exists(DB):
        os.remove(DB)
    mdm = MDM(DB)
    for fname, (src, prio) in SOURCES.items():
        with open(os.path.join(HERE, "demo_imports", fname), encoding="utf-8") as f:
            rows = json.load(f)
        print("== 导入 %s（来源：%s） ==" % (fname, src))
        for r in mdm.import_rows(src, prio, rows):
            print("  %-8s %-14s %s" % (r["external_id"], ACTION_LABELS.get(r["action"], r["action"]),
                                        {k: v for k, v in r.items()
                                         if k not in ("action", "record_id", "external_id")}))
    print("\n客户数：%d，记录数：%d，待确认：%d" % (
        mdm.one("SELECT COUNT(*) c FROM customers WHERE active=1")["c"],
        mdm.one("SELECT COUNT(*) c FROM records")["c"],
        mdm.one("SELECT COUNT(*) c FROM queue WHERE status='pending'")["c"]))
    mdm.close()


if __name__ == "__main__":
    main()
