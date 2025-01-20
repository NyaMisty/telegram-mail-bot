import logging
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from urllib.parse import urlparse

from .oauth2_helper import OAuth2Factory

logger = logging.getLogger(__name__)

def send_email(smtp_server_uri, sender_email, password, receiver_email, subject, body):

    # 创建一个多部分的邮件容器
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject

    # 添加邮件正文
    msg.attach(MIMEText(body, 'plain'))
    
    # 解析URI
    smtp_server_uri = urlparse(smtp_server_uri)
    if smtp_server_uri.scheme == 'smtp':
        server = smtplib.SMTP(smtp_server_uri.hostname, smtp_server_uri.port or smtplib.SMTP_PORT)
    elif smtp_server_uri.scheme == 'smtps':
        server = smtplib.SMTP_SSL(smtp_server_uri.hostname, smtp_server_uri.port or smtplib.SMTP_SSL_PORT)
    elif smtp_server_uri.scheme == 'smtp+starttls':
        server = smtplib.SMTP(smtp_server_uri.hostname, smtp_server_uri.port or 587)
        server.starttls()
    else:
        raise NotImplementedError(f"Unsupported protocol: {smtp_server_uri.scheme}")

    if os.getenv('SMTPDEBUG'):
        server.set_debuglevel(100)

    try:
        token = OAuth2Factory.token_from_string(password)
        if not token:
            server.login(sender_email, password)  # 登录SMTP服务器
        else:
            server.ehlo_or_helo_if_needed()
            server.auth('XOAUTH2', lambda: token.getSasl(sender_email, raw=True).decode('ascii')) # inside server.auth it will do encode('ascii')
        text = msg.as_string()  # 转换为字符串
        ret = server.sendmail(sender_email, receiver_email, text)  # 发送邮件
        logger.info("successfully sent email with return info: %s", ret)
    finally:
        server.quit()

if __name__ == '__main__':
    import sys
    smtp_server_uri = sys.argv[1]
    email_addr = sys.argv[2]
    password = sys.argv[3]
    receiver_addr = sys.argv[4]
    send_email(
        smtp_server_uri=smtp_server_uri,
        sender_email=email_addr,
        password=password,
        receiver_email=receiver_addr,
        subject="Custom SMTP Server Email Example",
        body="This email is sent using a custom SMTP server configuration."
    )