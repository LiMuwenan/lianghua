# -*- coding: utf-8 -*-
"""SMTP 发送：126 邮箱 SSL（465），认证用客户端授权码（由调用方从环境变量取得）。"""
import logging
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger("app.services.email_sender")


def send_email(cfg, recipients, subject: str, html: str, password: str) -> None:
    """构造 HTML 邮件并发送。cfg 提供 smtp_* 属性；失败抛异常由上层记录。"""
    if not recipients:
        raise ValueError("收件人列表为空")
    from_addr = cfg.smtp_from or cfg.smtp_username
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)

    cls = smtplib.SMTP_SSL if cfg.smtp_use_ssl else smtplib.SMTP
    with cls(cfg.smtp_host, cfg.smtp_port, timeout=20) as smtp:
        smtp.login(cfg.smtp_username, password)
        smtp.send_message(msg)
    logger.info("邮件已发送：%s → %s", from_addr, recipients)
