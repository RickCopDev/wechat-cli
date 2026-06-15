"""get-recent-sessions 命令"""

import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime

import click

from ..core.contacts import get_contact_names
from ..core.messages import decompress_content, format_msg_type
from ..output.formatter import output


def _load_official_accounts(app):
    """从 contact.db 加载公众号/服务号 username 集合 (verify_flag >= 8 或 gh_ 开头)."""
    import os
    pre_decrypted = os.path.join(app.decrypted_dir, "contact", "contact.db")
    db_path = pre_decrypted if os.path.exists(pre_decrypted) else app.cache.get(os.path.join("contact", "contact.db"))
    if not db_path:
        return set()
    official = set()
    conn = sqlite3.connect(db_path)
    try:
        for uname, verify in conn.execute(
            "SELECT username, verify_flag FROM contact WHERE verify_flag >= 8 OR username LIKE 'gh_%'"
        ).fetchall():
            official.add(uname)
    finally:
        conn.close()
    return official


def _parse_last_time(value):
    """解析快捷时间表达式，返回起始时间戳。

    支持格式: '1h', '2hours', '3d', '4days', 'today'
    """
    value = (value or '').strip().lower()
    if not value:
        return None

    if value == 'today':
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp())

    m = re.match(r'^(\d+)(h|hour|hours|d|day|days)$', value)
    if not m:
        raise ValueError(f"--last 格式无效: {value}。支持: 1h/2hours/3d/4days/today")

    n = int(m.group(1))
    unit = m.group(2)

    now_ts = int(datetime.now().timestamp())
    if unit.startswith('h'):
        return now_ts - n * 3600
    else:  # day
        return now_ts - n * 86400


@click.command("sessions")
@click.option("--limit", default=20, help="返回的会话数量")
@click.option("--last", "last_time", default=None,
              help="快捷时间过滤: 1h/2hours(最近N小时), 3d/4days(最近N天), today(今天)")
@click.option("--type", "session_type", default=None,
              type=click.Choice(["group", "private", "official"]),
              help="会话类型过滤: group(群聊), private(私聊), official(公众号/服务号)")
@click.option("--format", "fmt", default="json", type=click.Choice(["json", "text"]), help="输出格式")
@click.pass_context
def sessions(ctx, limit, last_time, session_type, fmt):
    """获取最近会话列表

    \b
    示例:
      wechat-cli sessions                  # 默认返回最近 20 个会话 (JSON)
      wechat-cli sessions --limit 10       # 最近 10 个会话
      wechat-cli sessions --last 1h        # 最近 1 小时有活动的会话
      wechat-cli sessions --last 3d        # 最近 3 天内有活动的会话
      wechat-cli sessions --last today     # 今天有活动的会话
      wechat-cli sessions --type group      # 仅群聊会话
      wechat-cli sessions --type private    # 仅私聊会话
      wechat-cli sessions --type official   # 仅公众号/服务号
      wechat-cli sessions --format text    # 纯文本输出
    """
    app = ctx.obj

    try:
        start_ts = _parse_last_time(last_time)
    except ValueError as e:
        click.echo(f"错误: {e}", err=True)
        ctx.exit(2)

    path = app.cache.get(os.path.join("session", "session.db"))
    if not path:
        click.echo("错误: 无法解密 session.db", err=True)
        ctx.exit(3)

    names = get_contact_names(app.cache, app.decrypted_dir)

    # Build a set of official account usernames for filtering
    if session_type == 'private' or session_type == 'official':
        official_set = _load_official_accounts(app)

    with closing(sqlite3.connect(path)) as conn:
        clauses = ["last_timestamp > 0"]
        params = []
        if start_ts is not None:
            clauses.append("last_timestamp >= ?")
            params.append(start_ts)
        if session_type == 'group':
            clauses.append("username LIKE '%@chatroom'")
        elif session_type == 'private':
            # Filter out groups, gh_* accounts, brandsessionholder variants,
            # and any account with verify_flag >= 8 (official/service)
            clauses.append("username NOT LIKE '%@chatroom'")
            clauses.append("username NOT LIKE 'gh_%'")
            # brandsessionholder and brandservicesessionholder
            # (SQLite LIKE with % in string literal doesn't work as wildcard,
            #  so use IN for exact matches)
            clauses.append("username NOT IN ('brandsessionholder', 'brandservicesessionholder')")
            if official_set:
                placeholders = ','.join('?' * len(official_set))
                clauses.append(f"username NOT IN ({placeholders})")
                params.extend(official_set)
        elif session_type == 'official':
            # Official accounts: gh_* + brandsessionholder + verified wxid_ accounts
            official_clauses = [
                "username LIKE 'gh_%'",
            ]
            if official_set:
                placeholders = ','.join('?' * len(official_set))
                official_clauses.append(f"username IN ({placeholders})")
                params.extend(official_set)
            # Always include brandsessionholder variants
            params.append('brandsessionholder')
            params.append('brandservicesessionholder')
            official_clauses.append("username IN (?, ?)")
            clauses.append("(" + " OR ".join(official_clauses) + ")")
        where_sql = " AND ".join(clauses)
        rows = conn.execute(f"""
            SELECT username, unread_count, summary, last_timestamp,
                   last_msg_type, last_msg_sender, last_sender_display_name
            FROM SessionTable
            WHERE {where_sql}
            ORDER BY last_timestamp DESC
            LIMIT ?
        """, (*params, limit)).fetchall()

    results = []
    for r in rows:
        username, unread, summary, ts, msg_type, sender, sender_name = r
        display = names.get(username, username)
        is_group = '@chatroom' in username

        if isinstance(summary, bytes):
            summary = decompress_content(summary, 4) or '(压缩内容)'
        if isinstance(summary, str) and ':\n' in summary:
            summary = summary.split(':\n', 1)[1]

        sender_display = ''
        if is_group and sender:
            sender_display = names.get(sender, sender_name or sender)

        results.append({
            'chat': display,
            'username': username,
            'is_group': is_group,
            'unread': unread or 0,
            'last_message': str(summary or ''),
            'msg_type': format_msg_type(msg_type),
            'sender': sender_display,
            'timestamp': ts,
            'time': datetime.fromtimestamp(ts).strftime('%m-%d %H:%M'),
        })

    if fmt == 'json':
        output(results, 'json')
    else:
        lines = []
        for r in results:
            entry = f"[{r['time']}] {r['chat']}"
            if r['is_group']:
                entry += " [群]"
            if r['unread'] > 0:
                entry += f" ({r['unread']}条未读)"
            entry += f"\n  {r['msg_type']}: "
            if r['sender']:
                entry += f"{r['sender']}: "
            entry += r['last_message']
            lines.append(entry)
        output(f"最近 {len(results)} 个会话:\n\n" + "\n\n".join(lines), 'text')
