# -*- coding: utf-8 -*-
"""端到端流程测试：跑完即删库，不影响演示数据。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import MDM

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "test_e2e.db")


def load(name):
    with open(os.path.join(HERE, "demo_imports", name), encoding="utf-8") as f:
        return json.load(f)


def check(cond, msg):
    assert cond, "FAIL: " + msg
    print("ok -", msg)


if os.path.exists(DB):
    os.remove(DB)
m = MDM(DB)
for f, (s, p) in {"01_crm.json": ("CRM系统", 30), "02_shop.json": ("电商平台", 20),
                  "03_support.json": ("客服系统", 10), "04_shop_conflict.json": ("电商平台", 20)}.items():
    m.import_rows(s, p, load(f))

# 1. 自动合并 + 血缘
g = m.golden_fields(1)
check(g["phone"] == "13800001234" and g["email"] == "zhangwei@example.com", "张伟自动合并，手机号/邮箱已规范化")
f = m.one("SELECT * FROM customer_fields WHERE customer_id=1 AND field='phone'")
check(f["source_value"] == "138-0000-1234" and f["source_name"] == "CRM系统",
      "字段血缘保留来源系统与原值（CRM 原始格式）")

# 2. 同名不同人：两个李娜互不干扰
names = [c["id"] for c in m.q("SELECT id FROM customers WHERE active=1")]
check(len(names) == 7, "同名不同人的两个李娜各自独立（共7客户）")

# 3. 同一来源重复导入 → 更新版本，不新建客户
r = m.import_rows("CRM系统", 30, load("05_crm_update.json"))[0]
check(r["action"] == "updated" and r["customer_id"] == 1, "CRM重复导入C001：更新版本不新建客户")
check(m.one("SELECT version FROM records WHERE id=1")["version"] == 2, "来源版本升到 v2")
check(m.golden_fields(1)["address"] == "北京市朝阳区建国路88号A座1201", "高优先级来源更新地址")

# 4. 候选推荐 → 人工合并（王芳），逐项选字段
q = m.one("SELECT * FROM queue WHERE kind='candidate' AND status='pending'")
rec_t202 = m.one("SELECT * FROM records WHERE external_id='T202'")
mid, cid = m.merge_records([rec_t202["id"]], target_id=4,
                           choices={"address": rec_t202["id"], "phone": rec_t202["id"]},
                           kind="merge", note="测试合并王芳", queue_id=q["id"])
g = m.golden_fields(cid)
check(g["address"] == "杭州市西湖区文三路100号2幢302室", "人工选择的地址生效")
check(g["phone"] == "13588889999", "人工选择的手机号生效")
check(g["email"] == "wangfang@example.com", "未选字段按优先级保留电商邮箱")
check(m.one("SELECT locked FROM customer_fields WHERE customer_id=? AND field='address'", (cid,))["locked"] == 1,
      "人工确认字段已锁定")

# 5. 锁定字段不被低优先级来源覆盖
m.import_rows("客服系统", 10, load("07_support_update.json"))
check(m.golden_fields(cid)["address"] == "杭州市西湖区文三路100号2幢302室", "客服系统更新无法覆盖锁定地址")
check(m.one("SELECT version FROM records WHERE external_id='T202'")["version"] == 2, "客服记录版本同样升级")

# 6. 合并后新增资料（CRM 王芳自动并入）
r = m.import_rows("CRM系统", 30, load("06_crm_wangfang.json"))[0]
check(r["action"] == "auto_merged" and r["customer_id"] == cid, "合并后新资料自动并入王芳")

# 7. 撤销合并 → 新资料无法拆回 → 归属冲突
conflicts = m.undo_merge(mid)
check(len(conflicts) == 1, "撤销合并产生 1 条归属冲突")
check(m.golden_fields(4)["phone"] == "13566667777", "电商王芳已还原")
check(m.golden_fields(6)["phone"] == "13588889999", "客服王芳已还原")
crm_rec = m.one("SELECT * FROM records WHERE external_id='C004'")
check(crm_rec["customer_id"] is None, "CRM王芳记录悬空待归属（未丢失）")

# 8. 处理归属冲突
m.resolve_attr_conflict(conflicts[0], 6)
check(m.one("SELECT customer_id FROM records WHERE id=?", (crm_rec["id"],))["customer_id"] == 6,
      "归属冲突人工指定到客服王芳")

# 9. 手机号/邮箱冲突的人工确认 → 拒绝后独立成客户
q2 = m.one("SELECT * FROM queue WHERE reason='phone_email_conflict' AND status='pending'")
m.reject_queue(q2["id"])
s103 = m.one("SELECT * FROM records WHERE external_id='S103'")
check(s103["customer_id"] is not None, "拒绝合并后S103独立成客户")

# 10. 撤销自动并入（attach）
m2 = m.one("SELECT id FROM merges WHERE kind='attach' AND undone=0 ORDER BY id LIMIT 1")
m.undo_merge(m2["id"])
check(m.one("SELECT undone FROM merges WHERE id=?", (m2["id"],))["undone"] == 1, "自动并入可撤销")

m.close()
os.remove(DB)
print("\n全部通过")
