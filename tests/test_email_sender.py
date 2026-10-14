# -*- coding: utf-8 -*-
"""SMTP 发送：构造 MIME 邮件并投递（用假 SMTP 类，不触网）。"""
from app.services import email_sender


class _FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=20):
        self.host, self.port = host, port
        self.logged = None
        self.sent = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        self.logged = (user, password)

    def send_message(self, msg):
        self.sent = msg


class _Cfg:
    smtp_host = "smtp.126.com"
    smtp_port = 465
    smtp_use_ssl = True
    smtp_username = "sender@126.com"
    smtp_from = "sender@126.com"


def test_send_email_builds_and_logs_in(monkeypatch):
    _FakeSMTP.instances.clear()
    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", _FakeSMTP)
    email_sender.send_email(_Cfg(), ["a@outlook.com"], "A股每日大盘汇总 | 2026-10-13",
                            "<b>hi</b>", "授权码")
    inst = _FakeSMTP.instances[-1]
    assert inst.host == "smtp.126.com" and inst.port == 465
    assert inst.logged == ("sender@126.com", "授权码")
    assert inst.sent["To"] == "a@outlook.com"
    assert inst.sent["From"] == "sender@126.com"
    assert inst.sent["Subject"].startswith("A股每日大盘汇总")
    body = inst.sent.get_payload(decode=True).decode("utf-8")
    assert "<b>hi</b>" in body
