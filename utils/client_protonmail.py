import os
from base64 import b64decode
import logging
from urllib.parse import ParseResult, urlparse

import requests

from .client_base import EmailClientBase, testMain
from .oauth2_helper import OAuth2Factory, Token
from .mail import Email

logger = logging.getLogger(__name__)

class EmailClientProton(EmailClientBase):
    def __init__(self, email_account, passwd, server_uri=None):
        self.email_account = email_account
        parts = passwd.split('-')
        assert len(parts) == 2
        self.uid, self.token = parts
        self.password = passwd
        self.api_url = "https://mail-api.proton.me"
        self.server_uri: ParseResult = urlparse(server_uri) # type: ignore
        self.server = self.connect()
        self.sess = requests.Session()
        # hptezd5qkfnq56xfc7f7qepcioowwtd6-rurydiderd75qguwgiistt3x4xktzs6j
        self.sess.headers = {
            'x-pm-appversion': 'ios-mail@4.20.0.10280',
            'x-pm-locale': 'zh_CN',
            'user-agent': 'ProtonMail/4.20.0 (iOS/17.0; iPhone16,2)',
            'accept': 'application/vnd.protonmail.v1+json',
            
            # user-specific
            'x-pm-uid': self.uid,
            'authorization': f'Bearer {self.token}',
        }
        
        self.user = self.get_proton_user()
    
    def get_proton_user(self):
        r = self.sess.get('https://mail-api.proton.me/core/v4/users')
        assert r.json()['Code'] == 1000
        return r.json()['User']
    
    def get_mail_list(self, timeStart=0, count=50):
        headers = {
            'accept': 'application/vnd.protonmail.v1+json',
        }

        params = {
            'Desc': '1',
            'End': timeStart, # '1726427618'
            'LabelID': '15', # 15 -> all mail
            'PageSize': '50',
            'Sort': 'Time',
        }

        r = self.sess.get('https://mail-api.proton.me/mail/v4/messages', params=params, headers=headers)
        assert r['Code'] == 1000, f'ProtonMail get_mail_list error: {r}'
        return r['Messages']

    def get_mail_body()

    def connect(self):
        # display the welcome info received from server,
        # indicating the connection is set up properly
        logger.info('imap server welcome: %s', server.welcome.decode('utf8'))
        # authenticating
        token = OAuth2Factory.token_from_string(self.password)
        if token is None:
            # normal basic auth
            status, statusText = server.login(self.email_account, self.password)
        else:
            saslBody = token.getSasl(self.email_account)
            # status, statusText = server._simple_command('AUTHENTICATE', 'XOAUTH2', saslBody) # this will break imaplib's internal state
            # IMAP4.authenticate receives raw SASL bytes instead of base64 string, so we decode it first
            status, statusText = server.authenticate("XOAUTH2", lambda _: b64decode(saslBody))
        assert status == 'OK', f'imap failed to login: {status}'
        logger.info('imap login ok: %s', statusText)
        return server

    def get_mails_count(self):
        # Select the mailbox you want to check
        status, inboxdata = self.server.select("inbox", readonly=True)
        assert status == 'OK', f'imap failed to select: {status}'
        return int(inboxdata[0].decode())

    def get_mail_by_index(self, index):
        status, data = self.server.fetch('%d' % index, '(RFC822)')
        assert status == 'OK', f'imap failed to fetch: {status}'
        mail_lines = data[0][1]
        assert isinstance(mail_lines, bytes), f'imap fetch returned non-bytes: {type(mail_lines)}'
        return Email(mail_lines)

    def refresh_connection(self):
        self.server.noop()
    
    def cleanup(self):
        self.server.logout()
    
    def kill(self):
        self.server.close()

if __name__ == '__main__':
    # import sys
    # useraccount = sys.argv[1]
    # password = sys.argv[2]

    # client = EmailClientIMAP(useraccount, password)
    # num = client.get_mails_count()
    # print(num)
    # for i in range(1, num):
    #     print(client.get_mail_by_index(i))
    testMain(EmailClientIMAP)