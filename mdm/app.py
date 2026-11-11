# -*- coding: utf-8 -*-
"""客户主数据整理工具 - 网页界面。运行：python3 app.py 后访问 http://127.0.0.1:5000"""
import json
import os

from flask import Flask, abort, redirect, render_template_string, request, url_for

from core import FIELDS, FIELD_LABELS, MDM

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__)
mdm = MDM(os.path.join(HERE, "mdm.db"))

LAYOUT = """
<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>客户主数据整理</title>
<style>
 body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;background:#f5f6f8;color:#222}
 nav{background:#2c3e50;padding:10px 24px}
 nav a{color:#ecf0f1;text-decoration:none;margin-right:18px;font-size:14px}
 nav a:hover{color:#ffd166}
 .badge{background:#e74c3c;color:#fff;border-radius:8px;padding:0 6px;font-size:12px}
 main{max-width:1080px;margin:24px auto;padding:0 16px}
 table{border-collapse:collapse;width:100%;background:#fff;margin:12px 0}
 th,td{border:1px solid #e1e4e8;padding:8px 10px;font-size:14px;text-align:left;vertical-align:top}
 th{background:#f0f2f5}
 .card{background:#fff;border:1px solid #e1e4e8;border-radius:6px;padding:16px 20px;margin:14px 0}
 .btn{display:inline-block;background:#2c7be5;color:#fff;border:none;border-radius:4px;
      padding:6px 14px;font-size:14px;cursor:pointer;text-decoration:none}
 .btn.gray{background:#6c757d}.btn.red{background:#dc3545}.btn.green{background:#28a745}
 .tag{display:inline-block;border-radius:4px;padding:1px 8px;font-size:12px;margin-right:4px}
 .tag.auto{background:#d4edda;color:#155724}.tag.manual{background:#fff3cd;color:#856404}
 .tag.import{background:#e2e3e5;color:#383d41}.tag.lock{background:#f8d7da;color:#721c24}
 .tag.review{background:#fff3cd;color:#856404}.tag.candidate{background:#cce5ff;color:#004085}
 .muted{color:#888;font-size:13px}
 .flash{background:#d4edda;border:1px solid #c3e6cb;padding:10px 14px;border-radius:4px;margin:10px 0}
 .err{background:#f8d7da;border:1px solid #f5c6cb;padding:10px 14px;border-radius:4px;margin:10px 0}
 input[type=text],input[type=number],textarea,select{font-size:14px;padding:6px;border:1px solid #ccc;border-radius:4px}
 textarea{width:100%;font-family:monospace}
 .conflict{background:#fff8e1}
</style></head><body>
<nav>
 <a href="/">客户</a><a href="/records">来源记录</a><a href="/import">导入</a>
 <a href="/review">待确认{% if n_pending %} <span class="badge">{{ n_pending }}</span>{% endif %}</a>
 <a href="/merges">合并记录</a>
 <a href="/conflicts">归属冲突{% if n_conf %} <span class="badge">{{ n_conf }}</span>{% endif %}</a>
</nav>
<main>
{% if msg %}<div class="flash">{{ msg }}</div>{% endif %}
{% if err %}<div class="err">{{ err }}</div>{% endif %}
{{ body }}
</main></body></html>
"""


def render(body_tpl, **ctx):
    n_pending = mdm.one("SELECT COUNT(*) c FROM queue WHERE status='pending'")["c"]
    n_conf = mdm.one("SELECT COUNT(*) c FROM attr_conflicts WHERE status='pending'")["c"]
    body = render_template_string(body_tpl, **ctx)
    return render_template_string(
        LAYOUT, body=body, n_pending=n_pending, n_conf=n_conf,
        msg=request.args.get("msg"), err=request.args.get("err"))


def go(page, **kw):
    return redirect(url_for(page, **kw))


# ---------------- 客户列表 ----------------
@app.route("/")
def index():
    rows = mdm.dashboard()
    tpl = """
    <h2>统一客户视图（{{ rows|length }} 个客户）</h2>
    <table><tr><th>#</th><th>姓名</th><th>手机号</th><th>邮箱</th><th>地址</th><th>来源记录</th><th></th></tr>
    {% for r in rows %}<tr>
      <td>{{ r.id }}</td>
      <td>{{ r.fields.get('name','') }}</td><td>{{ r.fields.get('phone','') }}</td>
      <td>{{ r.fields.get('email','') }}</td><td>{{ r.fields.get('address','') }}</td>
      <td>{% for rec in r.records %}<span class="tag import">{{ rec.source_name }}#{{ rec.id }}</span>{% endfor %}</td>
      <td><a class="btn gray" href="/customer/{{ r.id }}">血缘详情</a></td>
    </tr>{% endfor %}</table>
    """
    return render(tpl, rows=rows)


# ---------------- 客户血缘详情 ----------------
@app.route("/customer/<int:cid>")
def customer(cid):
    v = mdm.customer_view(cid)
    if not v["customer"]:
        abort(404)
    tpl = """
    <h2>客户 #{{ v.customer.id }} {% if not v.customer.active %}<span class="tag lock">已并入 #{{ v.customer.merged_into }}</span>{% endif %}</h2>
    <div class="card"><h3>统一视图（每个字段均可追溯来源与原值）</h3>
    <table><tr><th>字段</th><th>当前值</th><th>来源系统</th><th>来源原值</th><th>来源记录</th><th>确定方式</th></tr>
    {% for f in v.fields %}<tr>
      <td>{{ labels[f.field] }}</td><td><b>{{ f.value }}</b></td>
      <td>{{ f.source_name }}</td><td>{{ f.source_value }}</td>
      <td>{% if f.record_id %}<a href="/records#r{{ f.record_id }}">记录#{{ f.record_id }}</a>{% endif %}</td>
      <td>{% if f.locked %}<span class="tag lock">人工确认·锁定</span>
          {% elif f.origin=='auto' or f.origin=='manual' %}<span class="tag manual">{{ f.origin }}</span>
          {% else %}<span class="tag import">导入</span>{% endif %}
          <span class="muted">优先级{{ f.priority }} · {{ f.updated_at }}</span></td>
    </tr>{% endfor %}</table></div>
    <div class="card"><h3>来源记录（{{ v.records|length }} 条）</h3>
    <table><tr><th>记录</th><th>来源系统</th><th>外部ID</th><th>姓名</th><th>手机(原值)</th><th>邮箱(原值)</th><th>地址</th><th>版本</th></tr>
    {% for r in v.records %}<tr>
      <td>#{{ r.id }}</td><td>{{ r.source_name }}</td><td>{{ r.external_id }}</td>
      <td>{{ r.name }}</td><td>{{ r.phone_raw }}{% if r.phone_verified %} ✓{% endif %}</td>
      <td>{{ r.email_raw }}{% if r.email_verified %} ✓{% endif %}</td>
      <td>{{ r.address }}</td><td>v{{ r.version }}</td>
    </tr>{% endfor %}</table></div>
    <div class="card"><h3>相关合并操作</h3>
    {% if v.merges %}<table><tr><th>#</th><th>类型</th><th>说明</th><th>时间</th><th>状态</th></tr>
    {% for m in v.merges %}<tr><td>#{{ m.id }}</td><td>{{ m.kind }}</td><td>{{ m.note }}</td>
      <td>{{ m.created_at }}</td><td>{{ '已撤销' if m.undone else '生效中' }}</td></tr>{% endfor %}</table>
    {% else %}<p class="muted">无</p>{% endif %}</div>
    """
    return render(tpl, v=v, labels=FIELD_LABELS)


# ---------------- 来源记录 ----------------
@app.route("/records")
def records():
    rows = mdm.q("""SELECT r.*, s.name AS source_name FROM records r
                    JOIN sources s ON s.id=r.source_id ORDER BY r.id""")
    tpl = """
    <h2>来源记录（{{ rows|length }} 条）</h2>
    <table><tr><th>#</th><th>来源</th><th>外部ID</th><th>姓名</th><th>手机(原值→规范)</th>
      <th>邮箱(原值→规范)</th><th>地址</th><th>版本</th><th>归属客户</th></tr>
    {% for r in rows %}<tr id="r{{ r.id }}">
      <td>#{{ r.id }}</td><td>{{ r.source_name }}</td><td>{{ r.external_id }}</td><td>{{ r.name }}</td>
      <td>{{ r.phone_raw }}{% if r.phone_verified %} ✓{% endif %}{% if r.phone_raw!=r.phone_norm %} → <b>{{ r.phone_norm }}</b>{% endif %}</td>
      <td>{{ r.email_raw }}{% if r.email_verified %} ✓{% endif %}{% if r.email_raw!=r.email_norm %} → <b>{{ r.email_norm }}</b>{% endif %}</td>
      <td>{{ r.address }}</td><td>v{{ r.version }}</td>
      <td>{% if r.customer_id %}<a href="/customer/{{ r.customer_id }}">客户#{{ r.customer_id }}</a>
          {% else %}<span class="tag review">待确认</span>{% endif %}</td>
    </tr>{% endfor %}</table>
    """
    return render(tpl, rows=rows)


# ---------------- 导入 ----------------
@app.route("/import", methods=["GET", "POST"])
def import_page():
    results, source_name = None, ""
    if request.method == "POST":
        source_name = request.form.get("source", "").strip()
        priority = int(request.form.get("priority") or 0)
        text = request.form.get("payload", "").strip()
        f = request.files.get("file")
        if f and f.filename:
            text = f.read().decode("utf-8")
        try:
            rows = json.loads(text)
            if isinstance(rows, dict):
                rows = [rows]
            results = mdm.import_rows(source_name, priority, rows)
        except Exception as e:  # noqa: BLE001
            return go("import_page", err="导入失败：%s" % e)
    sources = mdm.q("SELECT * FROM sources ORDER BY priority DESC")
    tpl = """
    <h2>导入客户资料</h2>
    <div class="card"><form method="post" enctype="multipart/form-data">
      <p>来源系统：<input type="text" name="source" required value="{{ source_name }}" placeholder="如 CRM系统">
         优先级：<input type="number" name="priority" value="10" style="width:70px">
         <span class="muted">数字越大优先级越高；人工确认的字段不会被低优先级来源覆盖</span></p>
      <p>JSON 文件：<input type="file" name="file"></p>
      <p>或粘贴 JSON：</p>
      <textarea name="payload" rows="8" placeholder='[{"external_id":"A1","name":"张三","phone":"138...","email":"...","address":"...","phone_verified":true}]'></textarea>
      <p><button class="btn">导入</button></p>
    </form></div>
    {% if sources %}<div class="card"><b>已有来源：</b>
      {% for s in sources %}<span class="tag import">{{ s.name }}（优先级{{ s.priority }}）</span>{% endfor %}</div>{% endif %}
    {% if results %}<div class="card"><h3>导入结果</h3>
    <table><tr><th>外部ID</th><th>结果</th><th>说明</th></tr>
    {% for r in results %}<tr>
      <td>{{ r.external_id }}</td><td>{{ labels.get(r.action, r.action) }}</td>
      <td>{% if r.customer_id %}<a href="/customer/{{ r.customer_id }}">客户#{{ r.customer_id }}</a>{% endif %}
          {% if r.version %}版本 v{{ r.version }}{% endif %}{{ r.reason or '' }}</td>
    </tr>{% endfor %}</table></div>{% endif %}
    """
    labels = {"created": "新建客户", "auto_merged": "自动合并", "updated": "更新来源版本",
              "review": "→ 人工确认", "candidate": "→ 候选推荐", "error": "错误"}
    return render(tpl, results=results, sources=sources, labels=labels, source_name=source_name)


# ---------------- 待确认队列 ----------------
@app.route("/review")
def review():
    rows = mdm.pending_queue()
    tpl = """
    <h2>待确认（{{ rows|length }}）</h2>
    {% if not rows %}<p class="muted">没有待处理项。</p>{% endif %}
    {% for it in rows %}<div class="card">
      <span class="tag {{ it.q.kind }}">{{ '人工确认' if it.q.kind=='review' else '候选推荐（不会自动合并）' }}</span>
      <b>{{ it.reason_label }}</b> <span class="muted">{{ it.q.detail }} · {{ it.q.created_at }}</span>
      {% if it.record %}<table><tr><th>新记录</th><th>来源</th><th>姓名</th><th>手机</th><th>邮箱</th><th>地址</th></tr>
        <tr><td>#{{ it.record.id }} / {{ it.record.external_id }}</td><td>{{ it.record.source_name }}</td>
        <td>{{ it.record.name }}</td><td>{{ it.record.phone_raw }}</td><td>{{ it.record.email_raw }}</td>
        <td>{{ it.record.address }}</td></tr></table>{% endif %}
      {% if it.options %}<table><tr><th>可能客户</th><th>姓名</th><th>手机</th><th>邮箱</th><th>地址</th></tr>
        {% for o in it.options %}<tr><td><a href="/customer/{{ o.id }}">客户#{{ o.id }}</a></td>
        <td>{{ o.fields.get('name','') }}</td><td>{{ o.fields.get('phone','') }}</td>
        <td>{{ o.fields.get('email','') }}</td><td>{{ o.fields.get('address','') }}</td></tr>{% endfor %}</table>{% endif %}
      <p>
        <a class="btn green" href="/compare?qid={{ it.q.id }}">对比字段并确认合并</a>
        <form method="post" action="/queue/{{ it.q.id }}/reject" style="display:inline"
              onsubmit="return confirm('确认拒绝？该记录将作为不同人独立成客户')">
          <button class="btn red">拒绝（不是同一人）</button></form>
      </p>
    </div>{% endfor %}
    """
    return render(tpl, rows=rows)


@app.route("/queue/<int:qid>/reject", methods=["POST"])
def reject(qid):
    mdm.reject_queue(qid)
    return go("review", msg="已拒绝，记录已独立成客户")


# ---------------- 字段对比 + 确认合并 ----------------
@app.route("/compare")
def compare():
    qid = request.args.get("qid", type=int)
    qrow = mdm.one("SELECT * FROM queue WHERE id=? AND status='pending'", (qid,)) if qid else None
    if not qrow:
        return go("review", err="该待确认项不存在或已处理")
    rec = mdm.get_record(qrow["record_id"])
    option_ids = json.loads(qrow["options"] or "[]")
    # 参与对比的记录列：新记录 + 每个候选客户的代表记录
    cols = [rec]
    cust_cols = []
    for cid in option_ids:
        crecs = mdm.q("""SELECT r.*, s.name AS source_name, s.priority AS source_priority
                         FROM records r JOIN sources s ON s.id=r.source_id
                         WHERE r.customer_id=? ORDER BY s.priority DESC""", (cid,))
        cust_cols.append({"cid": cid, "recs": crecs, "golden": mdm.golden_fields(cid)})
        cols.extend(crecs[:1])
    tpl = """
    <h2>字段对比 —— 逐项选择保留值</h2>
    <p class="muted">左侧为新记录，右侧为候选客户的当前视图。每个字段单独选择要保留的值，人工选择的字段将被锁定，后续低优先级来源不能覆盖。</p>
    <form method="post" action="/merge">
    <input type="hidden" name="qid" value="{{ qrow.id }}">
    <input type="hidden" name="record_id" value="{{ rec.id }}">
    <table><tr><th>字段</th>
      <th>新记录 #{{ rec.id }}<br><span class="muted">{{ rec.source_name }} / {{ rec.external_id }}</span></th>
      {% for cc in cust_cols %}<th>客户#{{ cc.cid }} 当前值<br>
        <span class="muted">{% for r in cc.recs %}{{ r.source_name }} {% endfor %}</span></th>{% endfor %}
    </tr>
    {% for f,label in labels.items() %}<tr class="{{ 'conflict' if conflict[f] }}">
      <td>{{ label }}{% if conflict[f] %}<br><span class="tag review">冲突</span>{% endif %}</td>
      <td>{% if rval[f] %}<label><input type="radio" name="f_{{ f }}" value="rec:{{ rec.id }}"> {{ rraw[f] }}</label>{% endif %}</td>
      {% for cc in cust_cols %}<td>
        {% if cc.golden.get(f) %}<label><input type="radio" name="f_{{ f }}" value="cust:{{ cc.cid }}"> {{ cc.golden[f] }}</label>{% endif %}
      </td>{% endfor %}
    </tr>{% endfor %}
    </table>
    <p>合并到：
      {% for cc in cust_cols %}<label><input type="radio" name="target" value="{{ cc.cid }}" {{ 'checked' if loop.first }}> 客户#{{ cc.cid }}</label>&nbsp;&nbsp;{% endfor %}
      <label><input type="radio" name="target" value="new"> 新建客户</label>
      <span class="muted">（不选的字段按来源优先级自动取值）</span></p>
    <p>备注：<input type="text" name="note" size="50"></p>
    <p><button class="btn green">确认合并</button>
       <a class="btn gray" href="/review">返回</a></p>
    </form>
    """
    rval = {f: mdm.rec_value(rec, f) for f in FIELDS}
    rraw = {f: mdm.rec_raw(rec, f) for f in FIELDS}
    conflict = {}
    for f in FIELDS:
        vals = {rval[f]} | {cc["golden"].get(f) for cc in cust_cols}
        vals.discard("")
        vals.discard(None)
        conflict[f] = len(vals) > 1
    return render(tpl, qrow=qrow, rec=rec, cust_cols=cust_cols, labels=FIELD_LABELS,
                  rval=rval, rraw=rraw, conflict=conflict)


@app.route("/merge", methods=["POST"])
def do_merge():
    qid = request.form.get("qid", type=int)
    rid = request.form.get("record_id", type=int)
    target = request.form.get("target")
    note = request.form.get("note", "")
    choices = {}
    for f in FIELDS:
        sel = request.form.get("f_" + f)
        if not sel:
            continue
        kind, _, ident = sel.partition(":")
        if kind == "rec":
            choices[f] = int(ident)
        else:  # cust:<cid> → 用该客户当前字段值对应的记录
            row = mdm.one("SELECT record_id FROM customer_fields WHERE customer_id=? AND field=?",
                          (int(ident), f))
            if row and row["record_id"]:
                choices[f] = row["record_id"]
    target_id = None if target == "new" else int(target)
    mid, cid = mdm.merge_records([rid], target_id=target_id, choices=choices,
                                 kind="merge", note=note or "人工确认合并", queue_id=qid)
    return go("customer", cid=cid, msg="合并完成（操作 #%d，可在「合并记录」中撤销）" % mid)


# ---------------- 合并记录与撤销 ----------------
@app.route("/merges")
def merges():
    rows = mdm.merges_list()
    tpl = """
    <h2>合并操作记录（全部保留，可撤销）</h2>
    <table><tr><th>#</th><th>类型</th><th>结果客户</th><th>涉及记录</th><th>说明</th><th>时间</th><th>状态</th><th></th></tr>
    {% for m in rows %}<tr>
      <td>#{{ m.id }}</td><td>{{ '自动并入' if m.kind=='attach' else '人工合并' }}</td>
      <td><a href="/customer/{{ m.customer_id }}">客户#{{ m.customer_id }}</a></td>
      <td>{{ m.record_ids }}</td><td>{{ m.note }}</td><td>{{ m.created_at }}</td>
      <td>{% if m.undone %}<span class="tag lock">已撤销 {{ m.undone_at }}</span>{% else %}生效中{% endif %}</td>
      <td>{% if not m.undone %}<form method="post" action="/merges/{{ m.id }}/undo"
        onsubmit="return confirm('确认撤销该合并？合并后新增且无法拆回的记录会进入归属冲突列表')">
        <button class="btn red">撤销</button></form>{% endif %}</td>
    </tr>{% endfor %}</table>
    """
    return render(tpl, rows=rows)


@app.route("/merges/<int:mid>/undo", methods=["POST"])
def undo(mid):
    try:
        conflicts = mdm.undo_merge(mid)
    except ValueError as e:
        return go("merges", err=str(e))
    if conflicts:
        return go("conflicts", msg="已撤销。有 %d 条合并后新增的记录无法明确拆回，请处理归属冲突" % len(conflicts))
    return go("merges", msg="已撤销合并 #%d" % mid)


# ---------------- 归属冲突 ----------------
@app.route("/conflicts")
def conflicts():
    rows = mdm.pending_conflicts()
    tpl = """
    <h2>归属冲突（{{ rows|length }}）</h2>
    <p class="muted">撤销合并后，这些合并期间新增的资料无法明确拆回原客户，需要人工指定归属，不会被丢弃。</p>
    {% if not rows %}<p class="muted">没有待处理的归属冲突。</p>{% endif %}
    {% for it in rows %}<div class="card">
      <b>记录 #{{ it.record.id }}</b>（{{ it.record.source_name }} / {{ it.record.external_id }}，来自已撤销的合并 #{{ it.ac.merge_id }}）
      <table><tr><th>姓名</th><th>手机</th><th>邮箱</th><th>地址</th></tr>
      <tr><td>{{ it.record.name }}</td><td>{{ it.record.phone_raw }}</td>
          <td>{{ it.record.email_raw }}</td><td>{{ it.record.address }}</td></tr></table>
      <p>指定归属：
      {% for o in it.options %}
        <form method="post" action="/conflicts/{{ it.ac.id }}/resolve" style="display:inline">
          <input type="hidden" name="customer_id" value="{{ o.id }}">
          <button class="btn green">归到 客户#{{ o.id }}（{{ o.fields.get('name','') }} {{ o.fields.get('phone','') }}）</button>
        </form>{% endfor %}</p>
    </div>{% endfor %}
    """
    return render(tpl, rows=rows)


@app.route("/conflicts/<int:acid>/resolve", methods=["POST"])
def resolve_conflict(acid):
    cid = request.form.get("customer_id", type=int)
    try:
        mdm.resolve_attr_conflict(acid, cid)
    except ValueError as e:
        return go("conflicts", err=str(e))
    return go("customer", cid=cid, msg="归属冲突已处理，记录已并入客户 #%d" % cid)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
