# -*- coding: utf-8 -*-
"""客户主数据管理核心逻辑：导入、规范化、匹配、合并、撤销、字段级血缘。"""
import json
import re
import sqlite3
import threading
from datetime import datetime

FIELDS = ["name", "phone", "email", "address"]
FIELD_LABELS = {"name": "姓名", "phone": "手机号", "email": "邮箱", "address": "地址"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  active INTEGER NOT NULL DEFAULT 1,
  merged_into INTEGER,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS records(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL REFERENCES sources(id),
  external_id TEXT NOT NULL,
  name TEXT, phone_raw TEXT, phone_norm TEXT,
  email_raw TEXT, email_norm TEXT, address TEXT,
  phone_verified INTEGER DEFAULT 0,
  email_verified INTEGER DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 1,
  customer_id INTEGER REFERENCES customers(id),
  imported_at TEXT, updated_at TEXT,
  UNIQUE(source_id, external_id)
);
CREATE TABLE IF NOT EXISTS customer_fields(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  field TEXT NOT NULL,
  value TEXT,
  record_id INTEGER,
  source_name TEXT,
  source_value TEXT,
  origin TEXT,              -- import | auto | manual
  locked INTEGER DEFAULT 0, -- 人工确认过的字段，低优先级来源不能覆盖
  priority INTEGER DEFAULT 0,
  updated_at TEXT,
  UNIQUE(customer_id, field)
);
CREATE TABLE IF NOT EXISTS merges(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,                -- attach(记录并入已有客户) | merge(客户合并)
  customer_id INTEGER,      -- 合并后存活的客户
  record_ids TEXT,          -- json
  snapshot TEXT,            -- json，撤销用
  note TEXT,
  undone INTEGER DEFAULT 0,
  created_at TEXT, undone_at TEXT
);
CREATE TABLE IF NOT EXISTS queue(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,                -- review(需人工确认) | candidate(仅推荐候选)
  record_id INTEGER,
  customer_id INTEGER,      -- 建议匹配的客户
  options TEXT,             -- json，冲突时的候选客户 id 列表
  reason TEXT, detail TEXT,
  status TEXT DEFAULT 'pending',  -- pending | confirmed | rejected
  created_at TEXT, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS attr_conflicts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  record_id INTEGER,
  merge_id INTEGER,
  options TEXT,             -- json，可归属的客户 id 列表
  status TEXT DEFAULT 'pending',
  resolved_customer_id INTEGER,
  created_at TEXT, resolved_at TEXT
);
"""

REASON_LABELS = {
    "name_mismatch": "手机号/邮箱一致，但姓名不同",
    "unverified_match": "手机号/邮箱一致但未验证",
    "phone_email_conflict": "手机号与邮箱分别指向不同客户",
    "multi_match": "同时匹配到多个客户",
    "similar_name_address": "姓名相同且地址相似",
}


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _locked(fn):
    """串行化写操作（Flask 多线程共用同一连接）。"""
    def wrapper(self, *a, **kw):
        with self.lock:
            return fn(self, *a, **kw)
    wrapper.__name__ = fn.__name__
    return wrapper


def norm_phone(p):
    """手机号规范化：去非数字字符，去 86/0086 国家码。"""
    if not p:
        return ""
    d = re.sub(r"\D", "", p)
    if d.startswith("0086"):
        d = d[4:]
    elif d.startswith("86") and len(d) > 11:
        d = d[2:]
    return d


def norm_email(e):
    return (e or "").strip().lower()


def norm_name(n):
    return re.sub(r"\s+", "", n or "").lower()


def norm_addr(a):
    return re.sub(r"[\s,，.。;；#-]", "", a or "")


def addr_similar(a, b):
    a, b = norm_addr(a), norm_addr(b)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 6 and shorter in longer


class MDM:
    def __init__(self, path="mdm.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- 基础查询 ----------
    def q(self, sql, args=()):
        return self.conn.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        return self.conn.execute(sql, args).fetchone()

    def get_record(self, rid):
        return self.one(
            """SELECT r.*, s.name AS source_name, s.priority AS source_priority
               FROM records r JOIN sources s ON s.id=r.source_id WHERE r.id=?""", (rid,))

    def get_customer(self, cid):
        return self.one("SELECT * FROM customers WHERE id=?", (cid,))

    def source_id(self, name, priority=0):
        row = self.one("SELECT * FROM sources WHERE name=?", (name,))
        if row:
            if row["priority"] != priority:
                self.conn.execute("UPDATE sources SET priority=? WHERE id=?", (priority, row["id"]))
                self.conn.commit()
            return row["id"]
        cur = self.conn.execute("INSERT INTO sources(name, priority) VALUES(?,?)", (name, priority))
        self.conn.commit()
        return cur.lastrowid

    # ---------- 导入 ----------
    def import_rows(self, source_name, priority, rows):
        results = []
        for row in rows:
            results.append(self.import_one(source_name, priority, row))
        return results

    @_locked
    def import_one(self, source_name, priority, row):
        sid = self.source_id(source_name, priority)
        ext = str(row.get("external_id") or "").strip()
        if not ext:
            return {"action": "error", "msg": "缺少 external_id", "row": row}
        data = dict(
            name=(row.get("name") or "").strip(),
            phone_raw=(row.get("phone") or "").strip(),
            phone_norm=norm_phone(row.get("phone")),
            email_raw=(row.get("email") or "").strip(),
            email_norm=norm_email(row.get("email")),
            address=(row.get("address") or "").strip(),
            phone_verified=1 if row.get("phone_verified") else 0,
            email_verified=1 if row.get("email_verified") else 0,
        )
        existing = self.one("SELECT * FROM records WHERE source_id=? AND external_id=?", (sid, ext))
        if existing:
            # 同一来源同一外部ID：更新来源版本，不新建客户
            self.conn.execute(
                """UPDATE records SET name=?, phone_raw=?, phone_norm=?, email_raw=?, email_norm=?,
                   address=?, phone_verified=?, email_verified=?, version=version+1, updated_at=?
                   WHERE id=?""",
                (data["name"], data["phone_raw"], data["phone_norm"], data["email_raw"],
                 data["email_norm"], data["address"], data["phone_verified"], data["email_verified"],
                 now(), existing["id"]))
            self.conn.commit()
            if existing["customer_id"]:
                self.recompute_customer(existing["customer_id"])
            return {"action": "updated", "record_id": existing["id"],
                    "customer_id": existing["customer_id"], "external_id": ext,
                    "version": existing["version"] + 1}
        cur = self.conn.execute(
            """INSERT INTO records(source_id, external_id, name, phone_raw, phone_norm,
               email_raw, email_norm, address, phone_verified, email_verified,
               version, imported_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?)""",
            (sid, ext, data["name"], data["phone_raw"], data["phone_norm"], data["email_raw"],
             data["email_norm"], data["address"], data["phone_verified"], data["email_verified"],
             now(), now()))
        self.conn.commit()
        rid = cur.lastrowid
        result = self.match_new_record(rid)
        result["record_id"] = rid
        result["external_id"] = ext
        return result

    # ---------- 匹配 ----------
    def match_new_record(self, rid):
        r = self.get_record(rid)
        phone_custs, email_custs = {}, {}
        if r["phone_norm"]:
            for row in self.q(
                    """SELECT r.id AS rid, r.phone_verified AS pv, r.customer_id AS cid
                       FROM records r JOIN customers c ON c.id=r.customer_id
                       WHERE c.active=1 AND r.phone_norm=? AND r.id!=?""", (r["phone_norm"], rid)):
                phone_custs.setdefault(row["cid"], []).append(row)
        if r["email_norm"]:
            for row in self.q(
                    """SELECT r.id AS rid, r.email_verified AS ev, r.customer_id AS cid
                       FROM records r JOIN customers c ON c.id=r.customer_id
                       WHERE c.active=1 AND r.email_norm=? AND r.id!=?""", (r["email_norm"], rid)):
                email_custs.setdefault(row["cid"], []).append(row)

        pc, ec = set(phone_custs), set(email_custs)
        if pc and ec and pc.isdisjoint(ec):
            # 手机号指向一个客户、邮箱指向另一个客户 → 人工确认
            opts = sorted(pc | ec)
            self.add_queue("review", rid, None, opts, "phone_email_conflict",
                           "手机号匹配客户 %s，邮箱匹配客户 %s" % (sorted(pc), sorted(ec)))
            return {"action": "review", "reason": "phone_email_conflict"}
        all_c = pc | ec
        if len(all_c) > 1:
            self.add_queue("review", rid, None, sorted(all_c), "multi_match",
                           "匹配到多个客户 %s" % sorted(all_c))
            return {"action": "review", "reason": "multi_match"}
        if len(all_c) == 1:
            cid = all_c.pop()
            via_phone = cid in pc
            matched = phone_custs[cid] if via_phone else email_custs[cid]
            verified = (r["phone_verified"] or any(m["pv"] for m in matched)) if via_phone \
                else (r["email_verified"] or any(m["ev"] for m in matched))
            golden_name = self.golden_fields(cid).get("name", "")
            if norm_name(golden_name) == norm_name(r["name"]):
                if verified:
                    # 已验证手机号/邮箱完全一致 + 姓名一致 → 自动合并
                    mid, _ = self.merge_records(
                        [rid], target_id=cid, kind="attach",
                        note="自动合并：已验证%s一致且姓名一致" % ("手机号" if via_phone else "邮箱"))
                    return {"action": "auto_merged", "customer_id": cid, "merge_id": mid}
                self.add_queue("review", rid, cid, [cid], "unverified_match",
                               "手机号/邮箱一致但来源未验证，需人工确认")
                return {"action": "review", "reason": "unverified_match"}
            self.add_queue("review", rid, cid, [cid], "name_mismatch",
                           "联系方式一致，但姓名「%s」与客户「%s」不同" % (r["name"], golden_name))
            return {"action": "review", "reason": "name_mismatch"}
        # 无联系方式匹配：仅凭相似姓名+地址 → 推荐候选，不自动合并
        cid = self.create_customer_for_record(rid)
        for c in self.q("SELECT id FROM customers WHERE active=1 AND id!=?", (cid,)):
            g = self.golden_fields(c["id"])
            if norm_name(g.get("name")) == norm_name(r["name"]) and \
                    addr_similar(g.get("address", ""), r["address"]):
                self.add_queue("candidate", rid, c["id"], [c["id"]], "similar_name_address",
                               "与客户 #%d 姓名相同、地址相似（%s）" % (c["id"], g.get("address")))
                return {"action": "candidate", "customer_id": cid}
        return {"action": "created", "customer_id": cid}

    def add_queue(self, kind, rid, cid, options, reason, detail):
        self.conn.execute(
            """INSERT INTO queue(kind, record_id, customer_id, options, reason, detail, status, created_at)
               VALUES(?,?,?,?,?,?,'pending',?)""",
            (kind, rid, cid, json.dumps(options or []), reason, detail, now()))
        self.conn.commit()

    # ---------- 客户与字段血缘 ----------
    def rec_value(self, rec, field):
        if field == "phone":
            return rec["phone_norm"]
        if field == "email":
            return rec["email_norm"]
        return rec[field]

    def rec_raw(self, rec, field):
        if field == "phone":
            return rec["phone_raw"]
        if field == "email":
            return rec["email_raw"]
        return rec[field]

    def upsert_field(self, cid, field, value, record_id, source_name, source_value,
                     origin, locked, priority):
        self.conn.execute(
            """INSERT INTO customer_fields(customer_id, field, value, record_id, source_name,
                 source_value, origin, locked, priority, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(customer_id, field) DO UPDATE SET
                 value=excluded.value, record_id=excluded.record_id,
                 source_name=excluded.source_name, source_value=excluded.source_value,
                 origin=excluded.origin, locked=excluded.locked,
                 priority=excluded.priority, updated_at=excluded.updated_at""",
            (cid, field, value, record_id, source_name, source_value, origin, locked, priority, now()))

    def create_customer_for_record(self, rid, origin="import"):
        rec = self.get_record(rid)
        cur = self.conn.execute("INSERT INTO customers(active, created_at) VALUES(1,?)", (now(),))
        cid = cur.lastrowid
        self.conn.execute("UPDATE records SET customer_id=? WHERE id=?", (cid, rid))
        for f in FIELDS:
            v = self.rec_value(rec, f)
            if v:
                self.upsert_field(cid, f, v, rid, rec["source_name"], self.rec_raw(rec, f),
                                  origin, 0, rec["source_priority"])
        self.conn.commit()
        return cid

    def recompute_customer(self, cid):
        """按来源优先级重算未锁定字段；人工确认(locked)的字段不动。"""
        locked = {r["field"] for r in self.q(
            "SELECT field FROM customer_fields WHERE customer_id=? AND locked=1", (cid,))}
        recs = self.q(
            """SELECT r.*, s.name AS source_name, s.priority AS source_priority
               FROM records r JOIN sources s ON s.id=r.source_id
               WHERE r.customer_id=? ORDER BY s.priority DESC, r.updated_at DESC, r.id DESC""", (cid,))
        for f in FIELDS:
            if f in locked:
                continue
            for rec in recs:
                v = self.rec_value(rec, f)
                if v:
                    self.upsert_field(cid, f, v, rec["id"], rec["source_name"],
                                      self.rec_raw(rec, f), "import", 0, rec["source_priority"])
                    break
        self.conn.commit()

    def golden_fields(self, cid):
        return {r["field"]: r["value"] for r in self.q(
            "SELECT field, value FROM customer_fields WHERE customer_id=?", (cid,))}

    # ---------- 合并 ----------
    def snapshot(self, cids, record_ids):
        marks_c = ",".join("?" * len(cids)) or "NULL"
        customers = [dict(r) for r in self.q(
            "SELECT * FROM customers WHERE id IN (%s)" % marks_c, cids)]
        fields = [dict(r) for r in self.q(
            "SELECT * FROM customer_fields WHERE customer_id IN (%s)" % marks_c, cids)]
        recs = {r["id"]: r for r in self.q(
            "SELECT id, customer_id FROM records WHERE customer_id IN (%s)" % marks_c, cids)}
        for rid in record_ids:
            row = self.one("SELECT id, customer_id FROM records WHERE id=?", (rid,))
            if row:
                recs[row["id"]] = row
        qrows = [dict(r) for r in self.q(
            """SELECT id, status FROM queue WHERE status='pending'
               AND (customer_id IN (%s) OR record_id IN (%s))"""
            % (marks_c, ",".join("?" * len(record_ids)) or "NULL"),
            cids + list(record_ids))] if cids else []
        return {"customers": customers, "fields": fields,
                "records": [dict(r) for r in recs.values()], "queue": qrows}

    @_locked
    def merge_records(self, record_ids, target_id=None, choices=None, kind="merge",
                      note="", queue_id=None):
        """把 record_ids 并入 target 客户；这些记录原来所属的客户被合并停用。choices: {field: record_id}"""
        choices = choices or {}
        record_ids = [int(i) for i in record_ids]
        recs = [self.get_record(i) for i in record_ids]
        involved = {r["customer_id"] for r in recs if r["customer_id"]}
        involved.discard(None)
        if target_id is None:
            active = sorted(c for c in involved if self.get_customer(c)["active"])
            if active:
                target_id = active[0]
            else:
                cur = self.conn.execute("INSERT INTO customers(active, created_at) VALUES(1,?)", (now(),))
                target_id = cur.lastrowid
        involved.add(target_id)
        snap = self.snapshot(sorted(involved), record_ids)

        marks = ",".join("?" * len(involved))
        self.conn.execute(
            "UPDATE records SET customer_id=? WHERE customer_id IN (%s)" % marks,
            (target_id, *sorted(involved)))
        for rid in record_ids:
            self.conn.execute("UPDATE records SET customer_id=? WHERE id=?", (target_id, rid))
        for cid in involved - {target_id}:
            self.conn.execute("UPDATE customers SET active=0, merged_into=? WHERE id=?",
                              (target_id, cid))
        self.conn.commit()
        self.recompute_customer(target_id)
        for field, rid in choices.items():
            rid = int(rid)
            rec = self.get_record(rid)
            v = self.rec_value(rec, field)
            if v:
                self.upsert_field(target_id, field, v, rid, rec["source_name"],
                                  self.rec_raw(rec, field), "manual", 1, 999)
        self.conn.commit()
        cur = self.conn.execute(
            """INSERT INTO merges(kind, customer_id, record_ids, snapshot, note, created_at)
               VALUES(?,?,?,?,?,?)""",
            (kind, target_id, json.dumps(record_ids), json.dumps(snap, ensure_ascii=False),
             note, now()))
        mid = cur.lastrowid
        qmarks = ",".join("?" * len(record_ids)) or "NULL"
        self.conn.execute(
            """UPDATE queue SET status='confirmed', resolved_at=? WHERE status='pending'
               AND (record_id IN (%s) OR customer_id IN (%s) OR id=?)"""
            % (qmarks, ",".join("?" * len(involved))),
            (now(), *record_ids, *sorted(involved), queue_id or -1))
        self.conn.commit()
        return mid, target_id

    # ---------- 撤销 ----------
    @_locked
    def undo_merge(self, mid):
        m = self.one("SELECT * FROM merges WHERE id=?", (mid,))
        if not m or m["undone"]:
            raise ValueError("合并记录不存在或已撤销")
        snap = json.loads(m["snapshot"])
        cids = [c["id"] for c in snap["customers"]]
        for later in self.q("SELECT id, kind, customer_id FROM merges WHERE id>? AND undone=0", (mid,)):
            if later["kind"] == "merge" and later["customer_id"] in cids:
                raise ValueError("请先撤销更新的合并 #%d" % later["id"])

        snap_rec_ids = {r["id"] for r in snap["records"]}
        target = m["customer_id"]
        # 合并之后才挂到该客户的新记录 → 无法明确拆回 → 归属冲突
        post = [r["id"] for r in self.q("SELECT id FROM records WHERE customer_id=?", (target,))
                if r["id"] not in snap_rec_ids]

        # 还原记录归属
        for r in snap["records"]:
            self.conn.execute("UPDATE records SET customer_id=? WHERE id=?",
                              (r["customer_id"], r["id"]))
        # 还原客户状态
        for c in snap["customers"]:
            self.conn.execute("UPDATE customers SET active=?, merged_into=? WHERE id=?",
                              (c["active"], c["merged_into"], c["id"]))
        # 还原字段血缘
        marks = ",".join("?" * len(cids)) or "NULL"
        self.conn.execute("DELETE FROM customer_fields WHERE customer_id IN (%s)" % marks, cids)
        for f in snap["fields"]:
            self.conn.execute(
                """INSERT INTO customer_fields(customer_id, field, value, record_id, source_name,
                     source_value, origin, locked, priority, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (f["customer_id"], f["field"], f["value"], f["record_id"], f["source_name"],
                 f["source_value"], f["origin"], f["locked"], f["priority"], f["updated_at"]))
        # 还原待确认队列
        for qrow in snap["queue"]:
            self.conn.execute("UPDATE queue SET status=?, resolved_at=NULL WHERE id=?",
                              (qrow["status"], qrow["id"]))
        self.conn.commit()

        active_after = [c["id"] for c in self.q(
            "SELECT id FROM customers WHERE active=1 AND id IN (%s)" % marks, cids)]
        # attach 撤销后悬空的记录（原本无客户、无队列）→ 恢复为独立客户
        for r in snap["records"]:
            if r["customer_id"] is None:
                pending = self.one(
                    "SELECT id FROM queue WHERE record_id=? AND status='pending'", (r["id"],))
                cur = self.one("SELECT customer_id FROM records WHERE id=?", (r["id"],))
                if not pending and cur and cur["customer_id"] is None:
                    self.create_customer_for_record(r["id"])
        # 合并后新增、无法拆回的记录 → 归属冲突，绝不丢弃
        conflicts = []
        for rid in post:
            self.conn.execute("UPDATE records SET customer_id=NULL WHERE id=?", (rid,))
            cur = self.conn.execute(
                """INSERT INTO attr_conflicts(record_id, merge_id, options, status, created_at)
                   VALUES(?,?,?,'pending',?)""",
                (rid, mid, json.dumps(active_after), now()))
            conflicts.append(cur.lastrowid)
        self.conn.execute("UPDATE merges SET undone=1, undone_at=? WHERE id=?", (now(), mid))
        self.conn.commit()
        return conflicts

    # ---------- 队列与冲突处理 ----------
    @_locked
    def reject_queue(self, qid):
        qrow = self.one("SELECT * FROM queue WHERE id=?", (qid,))
        if not qrow or qrow["status"] != "pending":
            return
        self.conn.execute("UPDATE queue SET status='rejected', resolved_at=? WHERE id=?",
                          (now(), qid))
        self.conn.commit()
        rec = self.one("SELECT * FROM records WHERE id=?", (qrow["record_id"],))
        if rec and rec["customer_id"] is None:
            # 拒绝合并：该记录是不同的人，独立成客户
            self.create_customer_for_record(qrow["record_id"])

    @_locked
    def resolve_attr_conflict(self, acid, cid):
        ac = self.one("SELECT * FROM attr_conflicts WHERE id=?", (acid,))
        if not ac or ac["status"] != "pending":
            raise ValueError("冲突不存在或已处理")
        self.merge_records([ac["record_id"]], target_id=cid, kind="attach",
                           note="归属冲突 #%d 人工确认" % acid)
        self.conn.execute(
            """UPDATE attr_conflicts SET status='resolved', resolved_customer_id=?, resolved_at=?
               WHERE id=?""", (cid, now(), acid))
        self.conn.commit()

    # ---------- 视图数据 ----------
    def dashboard(self):
        out = []
        for c in self.q("SELECT * FROM customers WHERE active=1 ORDER BY id"):
            g = self.golden_fields(c["id"])
            recs = self.q(
                """SELECT r.id, s.name AS source_name FROM records r
                   JOIN sources s ON s.id=r.source_id WHERE r.customer_id=?""", (c["id"],))
            out.append({"id": c["id"], "fields": g, "records": recs, "created_at": c["created_at"]})
        return out

    def customer_view(self, cid):
        c = self.get_customer(cid)
        fields = self.q(
            "SELECT * FROM customer_fields WHERE customer_id=? ORDER BY field", (cid,))
        recs = self.q(
            """SELECT r.*, s.name AS source_name, s.priority FROM records r
               JOIN sources s ON s.id=r.source_id WHERE r.customer_id=? ORDER BY r.id""", (cid,))
        merges = self.q(
            "SELECT * FROM merges WHERE customer_id=? ORDER BY id DESC", (cid,))
        return {"customer": c, "fields": fields, "records": recs, "merges": merges}

    def pending_queue(self):
        rows = []
        for qrow in self.q("SELECT * FROM queue WHERE status='pending' ORDER BY id"):
            rec = self.get_record(qrow["record_id"]) if qrow["record_id"] else None
            opts = []
            for cid in json.loads(qrow["options"] or "[]"):
                opts.append({"id": cid, "fields": self.golden_fields(cid),
                             "active": bool(self.get_customer(cid)["active"])})
            rows.append({"q": qrow, "record": rec, "options": opts,
                         "reason_label": REASON_LABELS.get(qrow["reason"], qrow["reason"])})
        return rows

    def merges_list(self):
        return self.q("SELECT * FROM merges ORDER BY id DESC")

    def pending_conflicts(self):
        out = []
        for ac in self.q("SELECT * FROM attr_conflicts WHERE status='pending' ORDER BY id"):
            rec = self.get_record(ac["record_id"])
            opts = [{"id": cid, "fields": self.golden_fields(cid)}
                    for cid in json.loads(ac["options"] or "[]")
                    if self.get_customer(cid) and self.get_customer(cid)["active"]]
            out.append({"ac": ac, "record": rec, "options": opts})
        return out
